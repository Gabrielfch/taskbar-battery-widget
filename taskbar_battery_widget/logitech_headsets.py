"""Direct battery reader for Logitech / Astro gaming headsets that the generic HID++ 2.0 scanner
(`hidpp.py`, usage page 0xFF00 collections) cannot read.

Three read-only protocols, protocol facts from public notes (HeadsetControl, Solaar), independent
implementation; no code or calibration table is copied:

* "legacy HID++": G533, G535, G633/G635/G733/G933/G935, G PRO / PRO X. A 20-byte HID++ 2.0 report to
  device index 0xFF on the 0xFF43/0x0202 collection asks for the cell voltage. The battery feature
  index is resolved at runtime with Root.getFeature (0x1F20 / 0x1001) and every request is built by
  `hidpp.build_guarded_request`; the voltage becomes a percentage with an original piecewise-linear
  curve.
* "Centurion": G PRO X 2 / X 3 LIGHTSPEED and G522 LIGHTSPEED. 64-byte frames, Root.getFeature for
  the bridge (validated with its status getter), then a bridged Root.getFeature for the battery
  feature (0x0104), then its getter.
* "Astro A50 Gen 5" (046d:0b1c): vendor report 0x02, command 0x06.

Only fixed read/query requests are ever built (see `build_*` / `is_allowed_centurion_payload`);
anything else raises ValueError. A headset that does not answer yields no device (silence is not
"off"; only an explicit error / "not connected" reply is). Never raises from `read`.
"""
from __future__ import annotations

import bisect
import logging
import threading
import time
from dataclasses import dataclass

from . import hidpp, naming
from .models import Device

log = logging.getLogger(__name__)

VENDOR_IDS = (0x046D,)
DEVICE_BUDGET_S = 1.5
LOGITECH_VID = 0x046D

# --------------------------------------------------------------------------- shared helpers
def _percent(mv: int, curve: tuple[tuple[int, int], ...]) -> int:
    """Piecewise-linear (millivolts -> percent), clamped to 0..100."""
    if mv <= curve[0][0]:
        return 0
    if mv >= curve[-1][0]:
        return 100
    xs = [p[0] for p in curve]
    i = bisect.bisect_right(xs, mv)
    (x0, y0), (x1, y1) = curve[i - 1], curve[i]
    return max(0, min(100, round(y0 + (y1 - y0) * (mv - x0) / (x1 - x0))))


# Original single-cell Li-ion discharge curves (mV, %), one per hardware family.
CURVE_G533 = ((3300, 0), (3450, 3), (3600, 9), (3690, 16), (3750, 27), (3810, 42), (3870, 57),
              (3960, 73), (4060, 88), (4170, 100))
CURVE_G535 = ((3300, 0), (3500, 4), (3640, 12), (3720, 24), (3790, 38), (3850, 52), (3930, 70),
              (4030, 86), (4140, 100))
CURVE_G633 = ((3200, 0), (3350, 4), (3500, 10), (3650, 22), (3750, 38), (3850, 58), (3950, 77),
              (4050, 91), (4150, 100))
CURVE_GPRO = ((3300, 0), (3460, 3), (3620, 9), (3700, 17), (3760, 28), (3820, 43), (3880, 58),
              (3970, 74), (4070, 89), (4160, 100))

LEGACY_FEATURES = (hidpp.F_ADC, hidpp.F_BATTERY_VOLTAGE)       # 0x1F20 / 0x1001: voltage getters


@dataclass(frozen=True)
class Model:
    name: str
    pids: tuple[int, ...]
    protocol: str                       # "legacy" | "centurion" | "astro5"
    # (page, usage) pairs of the collection to open
    collections: tuple[tuple[int, int], ...] = ()
    sw: int = 0                         # legacy: software id of the requests (1..15)
    curve: tuple[tuple[int, int], ...] = ()
    prefix: bytes = b""                 # centurion frame prefix


_LEGACY_COLL = ((0xFF43, 0x0202),)
MODELS: tuple[Model, ...] = (
    Model("Logitech G533", (0x0A66,), "legacy", _LEGACY_COLL, 0x01, CURVE_G533),
    Model("Logitech G535", (0x0AC4,), "legacy", _LEGACY_COLL + ((0x000C, 0x0001),), 0x0D,
          CURVE_G535),
    Model("Logitech G633/G733/G933/G935",
          (0x0A5C, 0x0A89, 0x0A5B, 0x0A87, 0x0AB5, 0x0AFE, 0x0B1F), "legacy", _LEGACY_COLL,
          0x0A, CURVE_G633),
    Model("Logitech G PRO Headset", (0x0AA7, 0x0AAA, 0x0ABA, 0x0AFB, 0x0AFC), "legacy", _LEGACY_COLL,
          0x0D, CURVE_GPRO),
    Model("Logitech G PRO X 2 LIGHTSPEED", (0x0AF7,), "centurion", ((0xFFA0, 0x0001),),
          prefix=b"\x51"),
    Model("Logitech G PRO X 3 LIGHTSPEED", (0x0B45,), "centurion", ((0xFFA0, 0x0001),),
          prefix=b"\x50\x23"),
    Model("Logitech G522 LIGHTSPEED", (0x0B18,), "centurion", ((0xFFA0, 0x0001),),
          prefix=b"\x50\x23"),
    Model("Logitech ASTRO A50 Gen 5", (0x0B1C,), "astro5", ((0xFF32, 0x0074),)),
)
_BY_PID = {pid: m for m in MODELS for pid in m.pids}


# --------------------------------------------------------------------------- legacy HID++
LEGACY_LEN = 20
LEGACY_DEVICE_INDEX = 0xFF
_CHARGING_STATE = 0x03


def build_legacy_lookup(feature_id: int, sw: int) -> bytes:
    """Root.getFeature for one of the voltage features. Anything else raises ValueError."""
    if feature_id not in LEGACY_FEATURES:
        raise ValueError("legacy HID++ feature is not allowed")
    return hidpp.build_guarded_request(LEGACY_DEVICE_INDEX, 0, hidpp.F_ROOT, 0,
                                       bytes([feature_id >> 8, feature_id & 0xFF]), sw, True)


def build_legacy_request(feature_index: int, feature_id: int, sw: int) -> bytes:
    """Voltage getter (fn 0) of the resolved feature index. Anything else raises ValueError."""
    if feature_id not in LEGACY_FEATURES:
        raise ValueError("legacy HID++ feature is not allowed")
    return hidpp.build_guarded_request(LEGACY_DEVICE_INDEX, feature_index, feature_id, 0, b"", sw, True)


def parse_legacy_lookup(buf: bytes | None, sw: int):
    """-> ('ok', feature index) | ('unsupported', None) | None when unrelated/short."""
    rep = hidpp.parse_report(buf)
    if rep is None or not hidpp.reply_matches(rep, LEGACY_DEVICE_INDEX, 0, 0, sw):
        return None
    if rep.error is not None:
        return "unsupported", None
    idx = hidpp.decode_get_feature(rep.params)
    return ("ok", idx) if idx is not None else ("unsupported", None)


def parse_legacy_reply(buf: bytes | None, feature_index: int, sw: int):
    """-> ('ok', (mv, state)) | ('off', None) for an error reply | None when unrelated/short/invalid."""
    rep = hidpp.parse_report(buf)
    if rep is None or not hidpp.reply_matches(rep, LEGACY_DEVICE_INDEX, feature_index, 0, sw):
        return None
    if rep.error is not None:                       # headset off or unreachable
        return "off", None
    p = rep.params
    if len(p) < 3:
        return None
    mv = (p[0] << 8) | p[1]
    if not 2500 <= mv <= 5000:
        return None
    return "ok", (mv, p[2])


def _legacy_device(model: Model, deadline, cancel, h):
    """-> ('ok', (percent, charging)) | ('off', None) | None (no usable answer: no device)."""
    sw = model.sw
    idx = fid = None
    for feature in LEGACY_FEATURES:
        res = _exchange(h, build_legacy_lookup(feature, sw), deadline, cancel,
                        lambda b: parse_legacy_lookup(b, sw))
        if res is None:
            return None                             # silent: do not guess
        if res[0] == "ok":
            idx, fid = res[1], feature
            break
    if idx is None:                                 # neither voltage feature exists: abort
        return None
    res = _exchange(h, build_legacy_request(idx, fid, sw), deadline, cancel,
                    lambda b: parse_legacy_reply(b, idx, sw))
    if res is None:
        return None
    if res[0] == "off":
        return "off", None
    mv, state = res[1]
    return "ok", (_percent(mv, model.curve), state == _CHARGING_STATE)


# --------------------------------------------------------------------------- Astro A50 Gen 5
ASTRO_LEN = 64
ASTRO_REQUEST = bytes([0x02, 0x0C, 0x03, 0x00, 0x06, 0x0C]).ljust(ASTRO_LEN, b"\0")
ALLOWED_ASTRO_REQUESTS = frozenset({ASTRO_REQUEST})


def build_astro_request() -> bytes:
    return ASTRO_REQUEST


def parse_astro_reply(buf: bytes | None):
    """-> (percent, charging) for a battery reply (02 0c LEN 00 06 xx %, ?, dock), else None."""
    if not buf or len(buf) < 9 or buf[0] != 0x02 or buf[1] != 0x0C or buf[4] != 0x06 or buf[2] <= 3:
        return None
    if buf[6] > 100:
        return None
    return buf[6], buf[8] != 0


# --------------------------------------------------------------------------- Centurion
CENTURION_LEN = 64
CENTURION_BRIDGE_FEATURE = 0x0003
CENTURION_BATTERY_FEATURE = 0x0104
_SW = 0x01


def is_allowed_centurion_payload(payload: bytes) -> bool:
    p = bytes(payload)
    if p == bytes([0x00, 0x01, 0x00, 0x03]):                                     # Root.getFeature(bridge)
        return True
    if len(p) == 2 and 1 <= p[0] <= 0x7F and p[1] == _SW:                        # bridge status
        return True
    if len(p) == 9 and 1 <= p[0] <= 0x7F and p[1:] == bytes(
            [0x11, 0x00, 0x05, 0x00, 0x00, 0x01, 0x01, 0x04]):                   # bridged Root.getFeature(battery)
        return True
    if len(p) == 7 and 1 <= p[0] <= 0x7F and p[1:5] == bytes([0x11, 0x00, 0x03, 0x00]) \
            and 1 <= p[5] <= 0x7F and p[6] == _SW:                               # bridged battery getter
        return True
    return False


def build_centurion_frame(prefix: bytes, payload: bytes) -> bytes:
    if not is_allowed_centurion_payload(payload):
        raise ValueError("Centurion request is not allowed")
    head = bytes(prefix) + bytes([len(payload) + 1, 0x00])
    return (head + bytes(payload)).ljust(CENTURION_LEN, b"\0")


def centurion_bridge_lookup(prefix: bytes) -> bytes:
    return build_centurion_frame(prefix, bytes([0x00, 0x01, 0x00, 0x03]))


def centurion_status(prefix: bytes, bridge: int) -> bytes:
    return build_centurion_frame(prefix, bytes([bridge, _SW]))


def centurion_battery_lookup(prefix: bytes, bridge: int) -> bytes:
    return build_centurion_frame(prefix, bytes([bridge, 0x11, 0x00, 0x05, 0x00, 0x00, 0x01, 0x01, 0x04]))


def centurion_battery_get(prefix: bytes, bridge: int, sub_index: int) -> bytes:
    return build_centurion_frame(prefix, bytes([bridge, 0x11, 0x00, 0x03, 0x00, sub_index, _SW]))


def extract_centurion_payload(buf: bytes | None, prefix: bytes) -> bytes | None:
    n = len(prefix)
    if not buf or len(buf) < n + 3 or bytes(buf[:n]) != bytes(prefix):
        return None
    cpl = buf[n]
    if cpl <= 1 or cpl + n + 1 > len(buf):
        return None
    return bytes(buf[n + 2:n + 1 + cpl])


def parse_centurion_battery(params: bytes) -> tuple[int, bool] | None:
    if not params or params[0] > 100:
        return None
    return params[0], len(params) >= 3 and params[2] in (1, 2)


def _frames(h, prefix, deadline, cancel):
    """Yield decoded payloads until the deadline (at most 8 frames)."""
    for _ in range(8):
        if _expired(deadline, cancel):
            return
        left = int((deadline - time.monotonic()) * 1000)
        buf = h.read(max(1, min(300, left)))
        if buf is None:
            continue
        pl = extract_centurion_payload(buf, prefix)
        if pl is not None and len(pl) >= 2:
            yield pl


def _write(h, frame, deadline, cancel) -> bool:
    if _expired(deadline, cancel):
        return False
    left = int((deadline - time.monotonic()) * 1000)
    return bool(h.write(frame, max(1, min(1000, left))))


def _centurion_direct(h, prefix, frame, index, deadline, cancel, match_sw=False):
    h.drain()
    if not _write(h, frame, deadline, cancel):
        return None
    for pl in _frames(h, prefix, deadline, cancel):
        if pl[0] == index and (not match_sw or pl[1] == _SW):
            return pl[2:]
    return None


def _centurion_bridge_state(h, prefix, bridge, deadline, cancel) -> str:
    """Bridge status getter -> 'on' | 'off' | 'fail'. 'fail' also means the reply had the wrong shape,
    so `bridge` is not a bridge feature index. Only a clear 'none connected' answer means 'off'."""
    params = _centurion_direct(h, prefix, centurion_status(prefix, bridge), bridge, deadline,
                               cancel, match_sw=True)
    if params is None or len(params) < 4:
        return "fail"
    return "off" if params[2] == 0 and params[3] == 0 else "on"


def _centurion_bridged(h, prefix, frame, bridge, sub_index, deadline, cancel):
    """-> ('ok', params) | ('off', None) | ('fail', None)."""
    h.drain()
    if not _write(h, frame, deadline, cancel):
        return "fail", None
    acked = False
    for pl in _frames(h, prefix, deadline, cancel):
        if pl[0] == 0xFF and len(pl) >= 3 and pl[1] == bridge and pl[2] == 0x11:   # request rejected
            return _centurion_probe(h, prefix, bridge, deadline, cancel)
        if pl[0] != bridge:
            continue
        if pl[1] == 0x00:                                           # connection event
            if len(pl) >= 4 and pl[3] == 0x00:
                return "off", None
            continue
        if pl[1] >> 4 != 1:
            continue
        if pl[1] & 0x0F == _SW:
            acked = True
            continue
        if pl[1] & 0x0F != 0 or len(pl) < 6 or pl[4] != 0:
            continue
        if pl[5] == 0xFF and len(pl) >= 7 and pl[6] == sub_index:
            return "fail", None
        if pl[5] == sub_index and len(pl) >= 7:
            return "ok", pl[7:]
    if acked:
        return _centurion_probe(h, prefix, bridge, deadline, cancel)
    return "fail", None


def _centurion_probe(h, prefix, bridge, deadline, cancel):
    """Ask the bridge whether a headset is connected; only a clear 'none' answer means off."""
    if _centurion_bridge_state(h, prefix, bridge, deadline, cancel) == "off":
        return "off", None
    return "fail", None


def _centurion_battery(h, model: Model, deadline, cancel):
    """-> ('ok', (pct, charging)) | ('off', None) | ('fail', None).

    The bridge and battery feature indexes come from the device: each is validated (a getter on it
    must answer with the expected shape) before anything else is sent to it.
    """
    pre = model.prefix
    p = _centurion_direct(h, pre, centurion_bridge_lookup(pre), 0x00, deadline, cancel)
    if not p or not 1 <= p[0] <= 0x7F:
        return "fail", None
    bridge = p[0]
    state = _centurion_bridge_state(h, pre, bridge, deadline, cancel)
    if state != "on":                               # 'fail': not a bridge; 'off': no headset
        return state, None
    state, p = _centurion_bridged(h, pre, centurion_battery_lookup(pre, bridge), bridge, 0x00,
                                  deadline, cancel)
    if state != "ok":
        return state, None
    if not p or not 1 <= p[0] <= 0x7F or p[0] == bridge:
        return "fail", None
    sub = p[0]
    state, p = _centurion_bridged(h, pre, centurion_battery_get(pre, bridge, sub), bridge, sub,
                                  deadline, cancel)
    if state != "ok":
        return state, None
    res = parse_centurion_battery(p)
    return ("ok", res) if res else ("fail", None)


# --------------------------------------------------------------------------- transport
def _expired(deadline: float, cancel: threading.Event | None) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


def _exchange(h, request: bytes, deadline: float, cancel, parser):
    """Write one request, then read until `parser(buf)` returns something (or the deadline)."""
    if _expired(deadline, cancel):
        return None
    h.drain()
    left = int((deadline - time.monotonic()) * 1000)
    if left <= 0 or not h.write(request, max(1, min(1000, left))):
        return None
    while not _expired(deadline, cancel):
        left = int((deadline - time.monotonic()) * 1000)
        res = parser(h.read(max(1, min(250, left))))     # last read capped to the remaining time
        if res is not None:
            return res
    return None


def _matches(info, model: Model) -> bool:
    if (info.usage_page, info.usage) not in model.collections:
        return False
    if model.protocol == "legacy":
        return info.out_len == LEGACY_LEN and info.in_len >= LEGACY_LEN
    if model.protocol in ("centurion", "astro5"):
        return info.out_len == 64 and info.in_len == 64
    return False


def _read_one(info, model: Model, deadline: float, cancel, handle_factory) -> Device | None:
    dev_id = f"logi:{info.pid:04x}"
    name = naming.sanitize_name(getattr(info, "product", "") or "") or model.name
    h = handle_factory(info.path, info.in_len)
    try:
        battery: int | None = None
        charging = False
        connected = True
        if model.protocol == "legacy":
            res = _legacy_device(model, deadline, cancel, h)
            if res is None:
                return None                         # silent / unresolved: keep the cached value
            if res[0] == "off":
                connected = False
            else:
                battery, charging = res[1]
        elif model.protocol == "astro5":
            res = _exchange(h, build_astro_request(), deadline, cancel, parse_astro_reply)
            if res is None:
                return None
            battery, charging = res
        else:
            state, res = _centurion_battery(h, model, deadline, cancel)
            if state == "ok":
                battery, charging = res
            elif state == "off":
                connected = False
            else:                                   # no usable answer: do not guess
                return None
    finally:
        try:
            h.close()
        except Exception:
            pass
    return Device(id=dev_id, name=name, kind="headset", battery=battery, charging=charging,
                  connected=connected, link="2.4ghz", source="hid")


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every supported Logitech/Astro headset base in `infos`. Never raises.

    The caller (`wireless`) removes bases that the generic HID++ scanner already lists.
    """
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == LOGITECH_VID and x.pid in _BY_PID),
                        key=lambda x: x.path):
            if _expired(deadline, cancel):
                break
            model = _BY_PID[i.pid]
            if i.pid in done or not _matches(i, model):
                continue
            try:
                dev = _read_one(i, model, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel,
                                handle_factory)
            except Exception:
                log.debug("logitech headset read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is None:
                continue
            done.add(i.pid)
            out.append(dev)
        return out
    except Exception:
        log.debug("logitech headset scan failed", exc_info=True)
        return []
