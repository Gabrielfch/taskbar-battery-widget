"""Direct battery reader for HyperX wireless headsets (USB dongle, HID).

Cloud Flight Wireless (the original reader) plus Cloud II Wireless (HP and Kingston revisions),
Cloud Alpha Wireless and Cloud Alpha 2 Wireless (see the "other models" section). Cloud III and
Cloud Alpha S expose no battery query and are not handled.

A model reader reports "off" only on an explicit not-connected answer; no reply yields no device
(the Cloud Flight dongle stays silent while the headset is off, so there silence means off).

Cloud Flight:

Protocol (vendor collection, usage page 0xFF53, 20-byte reports): the output report
`21 FF 05 00...` asks for the battery voltage; the input report `21 FF 05 HI LO ...` answers
with the cell voltage in millivolts (big-endian). Only that one read-only request is ever sent.
"""
from __future__ import annotations

import bisect
import logging
import threading
import time
from dataclasses import dataclass

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

HYPERX_VID = 0x0951
HP_VID = 0x03F0
VENDOR_IDS = (HYPERX_VID, HP_VID)
HYPERX_PIDS = frozenset({0x16C4, 0x1723})
VENDOR_PAGE = 0xFF53
REPORT_LEN = 20
REPORT_ID = 0x21
REQUEST_HEAD = bytes([REPORT_ID, 0xFF, 0x05])
ALLOWED_REQUESTS = frozenset({REQUEST_HEAD + bytes(REPORT_LEN - 3)})
CHARGING_MV = 0x100B                # above this the cell is being charged
DEFAULT_NAME = "HyperX Cloud Flight Wireless"
DEVICE_BUDGET_S = 1.5

# (millivolts, percent) knee points of a single-cell Li-ion discharge curve (piecewise linear)
_CURVE = (
    (3300, 0), (3500, 5), (3650, 15), (3750, 35), (3850, 60), (3925, 85), (3975, 100),
)
_MVS = [p[0] for p in _CURVE]

_last_battery: dict[int, int] = {}  # pid -> last percentage seen while discharging


def build_request() -> bytes:
    return REQUEST_HEAD + bytes(REPORT_LEN - len(REQUEST_HEAD))


def parse_reply(buf: bytes | None) -> int | None:
    """Voltage in mV when buf is a `21 FF 05 HI LO` report, else None."""
    if not buf or len(buf) < 5 or bytes(buf[:3]) != REQUEST_HEAD:
        return None
    return (buf[3] << 8) | buf[4]


def voltage_to_percent(mv: int) -> int:
    if mv <= _CURVE[0][0]:
        return 0
    if mv >= _CURVE[-1][0]:
        return 100
    i = bisect.bisect_right(_MVS, mv)
    (x0, y0), (x1, y1) = _CURVE[i - 1], _CURVE[i]
    return max(0, min(100, round(y0 + (y1 - y0) * (mv - x0) / (x1 - x0))))


def _send(h, req: bytes, timeout_ms: int) -> bool:
    if req not in ALLOWED_REQUESTS:
        raise ValueError("request is not allowed")
    return h.write(req, timeout_ms=timeout_ms)


def _query(h, deadline: float, cancel: threading.Event | None) -> int | None:
    if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
        return None
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0 or not _send(h, build_request(), max(1, min(1000, remaining_ms))):
        return None
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            return None
        left = int((deadline - time.monotonic()) * 1000)
        mv = parse_reply(h.read(timeout_ms=max(1, min(250, left))))
        if mv is not None:
            return mv
    return None


def _read_one(info, deadline: float, cancel, handle_factory) -> Device:
    name = sanitize_name(getattr(info, "product", "") or "") or DEFAULT_NAME
    dev_id = f"hyperx:{info.pid:04x}"
    with handle_factory(info.path, in_len=info.in_len) as h:
        mv = _query(h, deadline, cancel)
    if mv is None:                                  # headset off / no answer
        return Device(id=dev_id, name=name, kind="headset", battery=None, connected=False,
                      link="2.4ghz", source="hid")
    if mv > CHARGING_MV:                            # percentage unknown while charging
        return Device(id=dev_id, name=name, kind="headset", battery=_last_battery.get(info.pid),
                      connected=True, charging=True, link="2.4ghz", source="hid")
    pct = voltage_to_percent(mv)
    _last_battery[info.pid] = pct
    return Device(id=dev_id, name=name, kind="headset", battery=pct, connected=True,
                  charging=False, link="2.4ghz", source="hid")


# ---------------------------------------------------------------------------------------------
# Other models. Every request is a fixed read-only query built by build_model_request().
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Model:
    key: str                  # protocol family
    name: str
    vid: int
    pids: frozenset
    usage_page: int           # exact collection: both usage page and usage are pinned
    usage: int
    in_len: int
    out_len: int


MODELS: tuple[Model, ...] = (
    Model("cloud2", "HyperX Cloud II Wireless", HP_VID, frozenset({0x0696, 0x018B}),
          0xFF90, 0x0303, 20, 52),
    Model("cloud2k", "HyperX Cloud II Wireless", HYPERX_VID, frozenset({0x1718}),
          0xFF00, 0x0001, 64, 62),
    Model("alpha", "HyperX Cloud Alpha Wireless", HP_VID, frozenset({0x098D}),
          0xFF00, 0x0001, 31, 31),
    Model("alpha2", "HyperX Cloud Alpha 2 Wireless", HP_VID, frozenset({0x08BE}),
          0xFF13, 0xFF00, 64, 64),
)

_KINGSTON_HEAD = bytes.fromhex("06000200" "9a000068" "4a8e0a" "000000" "bb")
_CMD_LEVEL, _CMD_CHARGE = 0x02, 0x03          # cloud2 / cloud2k
_ALPHA_CONN, _ALPHA_CHARGE, _ALPHA_LEVEL = 0x03, 0x0C, 0x0B
_ALPHA2_CONN, _ALPHA2_BATTERY = (0x52, 0x01), (0x50, 0x02)
_PROTO_LEN = {"cloud2": 52, "cloud2k": 62, "alpha": 31, "alpha2": 64}


def _raw_request(key: str, cmd) -> bytes:
    n = _PROTO_LEN[key]
    if key == "cloud2":
        head = bytes([0x06, 0xFF, 0xBB, cmd])
    elif key == "cloud2k":
        head = _KINGSTON_HEAD + bytes([cmd])
    elif key == "alpha":
        head = bytes([0x21, 0xBB, cmd])
    else:
        head = bytes(cmd)
    return head + bytes(n - len(head))


ALLOWED_MODEL_REQUESTS = frozenset(
    {_raw_request(k, c) for k in ("cloud2", "cloud2k") for c in (_CMD_LEVEL, _CMD_CHARGE)}
    | {_raw_request("alpha", c) for c in (_ALPHA_CONN, _ALPHA_CHARGE, _ALPHA_LEVEL)}
    | {_raw_request("alpha2", c) for c in (_ALPHA2_CONN, _ALPHA2_BATTERY)})


def build_model_request(key: str, cmd) -> bytes:
    """Canonical output report for a whitelisted query; ValueError for anything else."""
    try:
        req = _raw_request(key, cmd)
    except (KeyError, TypeError, ValueError):
        raise ValueError("request is not allowed") from None
    if req not in ALLOWED_MODEL_REQUESTS:
        raise ValueError("request is not allowed")
    return req


def _send_model(h, req: bytes, out_len: int, timeout_ms: int) -> bool:
    if req not in ALLOWED_MODEL_REQUESTS:
        raise ValueError("request is not allowed")
    return h.write(req.ljust(max(out_len, len(req)), b"\0"), timeout_ms=timeout_ms)


def match_reply(key: str, buf: bytes | None, cmd) -> bytes | None:
    """buf when it answers `cmd` for protocol `key`, else None (unrelated or short report)."""
    if not buf or len(buf) < 8:
        return None
    b = bytes(buf)
    if key == "cloud2":
        ok = b[:4] == bytes([0x06, 0xFF, 0xBB, cmd])
    elif key == "cloud2k":
        ok = b[0] == 0x0B and b[2] == 0xBB and b[3] == cmd
    elif key == "alpha":
        ok = b[0] == 0x21 and b[1] == 0xBB and b[2] == cmd
    else:
        ok = b[0] == cmd[0] + 1 and b[1] == cmd[1]
    return b if ok else None


def _ask(h, key: str, cmd, out_len: int, deadline: float, cancel) -> bytes | None:
    if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
        return None
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0 or not _send_model(h, build_model_request(key, cmd), out_len,
                                            max(1, min(1000, remaining_ms))):
        return None
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            return None
        left = int((deadline - time.monotonic()) * 1000)
        r = match_reply(key, h.read(timeout_ms=max(1, min(250, left))), cmd)
        if r is not None:
            return r
    return None


def _dev(pid: int, name: str, battery, connected: bool, charging: bool = False) -> Device:
    return Device(id=f"hyperx:{pid:04x}", name=name, kind="headset", battery=battery,
                  connected=connected, charging=charging, link="2.4ghz", source="hid")


def _read_model(m: Model, info, deadline: float, cancel, handle_factory) -> Device | None:
    name = sanitize_name(getattr(info, "product", "") or "") or m.name
    pid = info.pid
    n = info.out_len
    with handle_factory(info.path, in_len=info.in_len) as h:
        if m.key in ("cloud2", "cloud2k"):
            if m.key == "cloud2k":
                try:                                  # harmless pre-read some firmware expects
                    h.get_input_report(0x06, 64)
                except Exception:
                    pass
            lvl = _ask(h, m.key, _CMD_LEVEL, n, deadline, cancel)
            if lvl is None:
                return None                           # silent: unknown, not "off"
            chg = _ask(h, m.key, _CMD_CHARGE, n, deadline, cancel)
            charging = chg is not None and chg[4] == 1
            if lvl[7] > 100:
                return None
            return _dev(pid, name, int(lvl[7]), True, charging)
        if m.key == "alpha":
            conn = _ask(h, m.key, _ALPHA_CONN, n, deadline, cancel)
            if conn is None:                          # silent: unknown, not "off"
                return None
            if conn[3] == 0x01:                       # explicit: headset not linked
                return _dev(pid, name, None, False)
            chg = _ask(h, m.key, _ALPHA_CHARGE, n, deadline, cancel)
            if chg is not None and chg[3] == 0x01:    # percentage unknown while charging
                return _dev(pid, name, _last_battery.get(pid), True, True)
            lvl = _ask(h, m.key, _ALPHA_LEVEL, n, deadline, cancel)
            if lvl is None or lvl[3] > 100:
                return None
            _last_battery[pid] = int(lvl[3])
            return _dev(pid, name, int(lvl[3]), True)
        conn = _ask(h, m.key, _ALPHA2_CONN, n, deadline, cancel)
        if conn is None:
            return None
        if conn[2] == 0x00:
            return _dev(pid, name, None, False)
        bat = _ask(h, m.key, _ALPHA2_BATTERY, n, deadline, cancel)
        if bat is None or bat[2] > 100:
            return None
        return _dev(pid, name, int(bat[2]), True, bat[3] == 0x01)


def _model_for(info) -> Model | None:
    for m in MODELS:
        if info.vid == m.vid and info.pid in m.pids:
            return m
    return None


def _collection_ok(m: Model, i) -> bool:
    if i.usage_page != m.usage_page or i.usage != m.usage:
        return False
    return i.in_len == m.in_len and i.out_len == m.out_len


def read_hyperx(infos, deadline: float, cancel: threading.Event | None = None,
                handle_factory=None) -> list[Device]:
    """Read every supported HyperX dongle in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[tuple[int, int]] = set()
        for i in sorted((x for x in infos if x.vid in VENDOR_IDS), key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            if (i.vid, i.pid) in done:
                continue
            m = None
            if i.vid == HYPERX_VID and i.pid in HYPERX_PIDS:     # Cloud Flight
                if i.usage_page != VENDOR_PAGE or i.in_len != REPORT_LEN \
                        or i.out_len != REPORT_LEN:
                    continue
            else:
                m = _model_for(i)
                if m is None or not _collection_ok(m, i):
                    continue
            try:
                end = min(deadline, time.monotonic() + DEVICE_BUDGET_S)
                dev = _read_one(i, end, cancel, handle_factory) if m is None \
                    else _read_model(m, i, end, cancel, handle_factory)
            except Exception:
                log.debug("hyperx read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is None:
                continue
            done.add((i.vid, i.pid))
            out.append(dev)
        return out
    except Exception:
        log.debug("hyperx scan failed", exc_info=True)
        return []


read = read_hyperx      # reader-contract entry point
