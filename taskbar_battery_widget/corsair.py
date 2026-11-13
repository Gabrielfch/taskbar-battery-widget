"""Direct battery reader for Corsair wireless headsets (USB receiver, HID).

Three protocol families, all read-only:
- Void (legacy): vendor page 0xFFC5, output report 0xC9 0x64; the 5-byte input report 0x64 carries
  the level (bit 7 = mic up) at index 2 and a status code at index 4.
- Bragi (Virtuoso XT / SE): vendor page 0xFF42, 64-byte reports. GET property 0x0F is the battery
  in tenths of a percent, GET property 0x10 the charge state. Reports carry ids 2 (out) / 1 (in).
- Void Wireless V2 family: the same Bragi framing on a collection without report ids, after a
  receiver/headset heartbeat.
Only GET queries of the whitelisted properties are ever sent; nothing is ever set.

A headset is reported "off" only on an explicit answer (Void status 0, an error status from the
paired-headset target). No reply at all / a failed write yields no device, so the cached value stays.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

CORSAIR_VID = 0x1B1C
VENDOR_IDS = (CORSAIR_VID,)
DEVICE_BUDGET_S = 1.5

# --- Void (legacy) ----------------------------------------------------------------------------
VOID_PAGE = 0xFFC5
VOID_REQUEST = bytes([0xC9, 0x64])
VOID_REPLY_ID = 0x64
VOID_MIC_FLAG = 0x80
VOID_PIDS = frozenset({
    0x1B1C, 0x1B27, 0x0A14, 0x0A16, 0x0A17, 0x0A1D, 0x0A1A, 0x1B2A, 0x1B23, 0x1B29,
    0x0A55, 0x0A51, 0x0A52, 0x0A38, 0x0A4F, 0x0A2B, 0x0A75, 0x0A56,
})

# --- Bragi ------------------------------------------------------------------------------------
BRAGI_LEN = 64
BRAGI_OUT_ID, BRAGI_IN_ID = 0x02, 0x01
TARGET_SELF, TARGET_HEADSET = 0x08, 0x09          # receiver / wired unit, paired headset
BRAGI_GET = 0x02
PROP_BATTERY, PROP_CHARGE, PROP_HEARTBEAT, PROP_FIRMWARE = 0x0F, 0x10, 0x12, 0x13
ALLOWED_PROPS = frozenset({PROP_BATTERY, PROP_CHARGE, PROP_HEARTBEAT, PROP_FIRMWARE})
ALLOWED_TARGETS = frozenset({TARGET_SELF, TARGET_HEADSET})
STATUS_OK = 0x00
CHARGING = 1
BATTERY_MAX = 1000                                 # tenths of a percent
PROBE_TIMEOUT_MS = 300
V2_ATTEMPTS = 3

XT_PAGE = 0xFF42


@dataclass(frozen=True)
class Model:
    family: str            # "void" | "xt" | "v2"
    name: str
    link: str
    wired: bool = False
    beta: bool = False     # least certain protocol: read only with beta readers on


# pid -> model
MODELS: dict[int, Model] = {pid: Model("void", "Corsair Void", "2.4ghz") for pid in VOID_PIDS}
MODELS.update({
    0x0A64: Model("xt", "Corsair Virtuoso XT", "2.4ghz"),
    0x0A62: Model("xt", "Corsair Virtuoso XT", "usb", wired=True),
    0x0A3E: Model("xt", "Corsair Virtuoso SE", "2.4ghz"),
    0x0A3D: Model("xt", "Corsair Virtuoso SE", "usb", wired=True),
    0x2A08: Model("v2", "Corsair Void Wireless V2", "2.4ghz", beta=True),
    0x2A02: Model("v2", "Corsair Virtuoso Max Wireless", "2.4ghz", beta=True),
    0x0A97: Model("v2", "Corsair HS80 Max Wireless", "2.4ghz", beta=True),
})


# ---------------------------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------------------------

def build_void_request(out_len: int = 2) -> bytes:
    return VOID_REQUEST.ljust(max(2, out_len), b"\0")


def build_bragi_request(target: int, prop: int, command: int = BRAGI_GET) -> bytes:
    """64-byte `02 target GET prop 00...` query; ValueError for anything but a whitelisted GET."""
    if command != BRAGI_GET or target not in ALLOWED_TARGETS or prop not in ALLOWED_PROPS:
        raise ValueError("request is not allowed")
    return bytes([BRAGI_OUT_ID, target, command, prop]).ljust(BRAGI_LEN, b"\0")


def _is_allowed_wire(frame: bytes, report_ids: bool) -> bool:
    if not report_ids:
        if not frame or frame[0] != 0:
            return False
        frame = frame[1:]
    if len(frame) < BRAGI_LEN:
        return False
    body = frame.rstrip(b"\0")
    return (len(body) == 4 and body[0] == BRAGI_OUT_ID and body[1] in ALLOWED_TARGETS
            and body[2] == BRAGI_GET and body[3] in ALLOWED_PROPS)


def _send(h, frame: bytes, report_ids: bool, timeout_ms: int) -> bool:
    """Write a Bragi frame after re-checking it against the whitelist.

    When the handle exposes the collection's output report length, the write must match it exactly.
    """
    if not _is_allowed_wire(frame, report_ids):
        raise ValueError("request is not allowed")
    n = getattr(h, "out_len", 0) or 0
    if n and len(frame) != n:
        log.debug("corsair frame length %d != output report length %d; not sent", len(frame), n)
        return False
    return h.write(frame, timeout_ms=timeout_ms)


def _send_void(h, frame: bytes, timeout_ms: int) -> bool:
    if frame.rstrip(b"\0") != VOID_REQUEST:
        raise ValueError("request is not allowed")
    return h.write(frame, timeout_ms=timeout_ms)


# ---------------------------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------------------------

def parse_void_reply(buf: bytes | None) -> tuple[str, int | None, bool] | None:
    """('offline'|'ok'|'charging', level, mic_up) for a Void battery report; None if unrelated."""
    if not buf or len(buf) < 5 or buf[0] != VOID_REPLY_ID:
        return None
    status = buf[4]
    if status == 0:
        return "offline", None, False
    if status not in (1, 2, 4, 5):
        return None
    raw = buf[2]
    level = raw & ~VOID_MIC_FLAG & 0xFF
    if level > 100:
        return None
    return ("charging" if status in (4, 5) else "ok"), level, bool(raw & VOID_MIC_FLAG)


def normalize_bragi(buf: bytes | None) -> bytes | None:
    """Strip the leading 0 report id some collections prepend (`00 01 ...` -> `01 ...`)."""
    if not buf:
        return None
    b = bytes(buf)
    if len(b) >= 2 and b[0] == 0 and b[1] == BRAGI_IN_ID:
        b = b[1:]
    return b


def match_bragi(buf: bytes | None, target: int, strict_source: bool = True) -> bytes | None:
    """The GET reply from `target` (`01 src 02 status data...`), else None."""
    b = normalize_bragi(buf)
    if b is None or len(b) < 8 or b[0] != BRAGI_IN_ID or b[2] != BRAGI_GET:
        return None
    if strict_source and b[1] != (0 if target == TARGET_SELF else 1):
        return None
    return b


def bragi_value(reply: bytes, width: int = 4) -> int:
    return int.from_bytes(reply[4:4 + width], "little")


# ---------------------------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------------------------

def _stop(deadline: float, cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set() or time.monotonic() >= deadline


def _bragi_get(h, target: int, prop: int, deadline: float, cancel, report_ids: bool,
               strict_source: bool = True, probe_ms: int | None = None) -> bytes | None:
    """One GET; returns the reply with status OK, or None (no answer / error status)."""
    if _stop(deadline, cancel):
        return None
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    frame = _frame(h, target, prop, report_ids)
    if remaining_ms <= 0 or not _send(h, frame, report_ids, max(1, min(1000, remaining_ms))):
        return None
    end = deadline if probe_ms is None else min(deadline, time.monotonic() + probe_ms / 1000)
    while time.monotonic() < end:
        if cancel is not None and cancel.is_set():
            return None
        left = int((min(end, deadline) - time.monotonic()) * 1000)
        r = match_bragi(h.read(timeout_ms=max(1, min(100 if probe_ms else 250, left))), target,
                        strict_source)
        if r is not None:
            return r
    return None


def _frame(h, target: int, prop: int, report_ids: bool) -> bytes:
    """Bragi query as written to the wire: `02 target 02 prop ...` (report id 2), or with a leading 0
    when the collection has no report ids. Padded up to the output report length when that is larger."""
    frame = build_bragi_request(target, prop)
    if not report_ids:
        frame = b"\0" + frame
    n = getattr(h, "out_len", 0) or 0
    return frame.ljust(n, b"\0") if n > len(frame) else frame


def _off(info, name: str, m: Model) -> Device:
    return Device(id=f"corsair:{info.pid:04x}", name=name, kind="headset", battery=None,
                  connected=False, link=m.link, source="hid")


def _read_void(info, name: str, m: Model, h, deadline: float, cancel) -> Device | None:
    dev_id = f"corsair:{info.pid:04x}"
    if _stop(deadline, cancel):
        raise TimeoutError
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0 or not _send_void(h, build_void_request(info.out_len), max(1, min(1000, remaining_ms))):
        return None                                 # write failed: unknown, not "off"
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise TimeoutError
        left = int((deadline - time.monotonic()) * 1000)
        parsed = parse_void_reply(h.read(timeout_ms=max(1, min(250, left))))
        if parsed is None:
            continue
        state, level, _mic = parsed
        if state == "offline":                      # explicit "no headset connected" status
            return _off(info, name, m)
        return Device(id=dev_id, name=name, kind="headset", battery=level, connected=True,
                      charging=state == "charging", link=m.link, source="hid")
    return None                                     # silent


def _read_xt(info, name: str, m: Model, h, deadline: float, cancel) -> Device | None:
    dev_id = f"corsair:{info.pid:04x}"
    hinted, alternate = (TARGET_SELF, TARGET_HEADSET) if m.wired else (TARGET_HEADSET, TARGET_SELF)
    refused = False
    for target in (hinted, alternate):
        r = _bragi_get(h, target, PROP_BATTERY, deadline, cancel, True, probe_ms=PROBE_TIMEOUT_MS)
        if r is None:
            continue
        if r[3] != STATUS_OK:
            refused = refused or target == hinted   # explicit error status from the expected target
            continue
        tenths = bragi_value(r)
        if tenths > BATTERY_MAX:
            continue
        c = _bragi_get(h, target, PROP_CHARGE, deadline, cancel, True, probe_ms=PROBE_TIMEOUT_MS)
        charging = c is not None and c[3] == STATUS_OK and bragi_value(c) == CHARGING
        return Device(id=dev_id, name=name, kind="headset", battery=tenths // 10, connected=True,
                      charging=charging, link=m.link, source="hid")
    return _off(info, name, m) if refused else None


def _read_v2(info, name: str, m: Model, h, deadline: float, cancel) -> Device | None:
    dev_id = f"corsair:{info.pid:04x}"
    # 64-byte collection: the report id (2) is part of the frame; 65: a leading 0 is the (absent) id
    ids = info.out_len == BRAGI_LEN
    # wake the receiver and check the headset answers its heartbeat (read-only GETs)
    for prop in (PROP_FIRMWARE, PROP_HEARTBEAT):
        if _stop(deadline, cancel):
            raise TimeoutError
        if not _send(h, _frame(h, TARGET_SELF, prop, ids), ids, 500):
            return None
    hb = _bragi_get(h, TARGET_HEADSET, PROP_HEARTBEAT, deadline, cancel, ids, False)
    if hb is None:
        return None                                 # silent: unknown, not "off"
    if hb[3] != STATUS_OK:
        return _off(info, name, m)                  # explicit error status: headset not paired/on
    for _ in range(V2_ATTEMPTS):
        r = _bragi_get(h, TARGET_HEADSET, PROP_BATTERY, deadline, cancel, ids, False)
        if r is None:
            return None
        if r[3] != STATUS_OK:
            continue
        tenths = bragi_value(r, 2)
        if 0 < tenths <= BATTERY_MAX:
            return Device(id=dev_id, name=name, kind="headset", battery=tenths // 10,
                          connected=True, charging=False, link=m.link, source="hid")
    return Device(id=dev_id, name=name, kind="headset", battery=None, connected=True,
                  link=m.link, source="hid")


def _collection_ok(m: Model, i) -> bool:
    if m.family == "void":
        return i.usage_page == VOID_PAGE and i.usage == 1 and i.in_len >= 5 and i.out_len >= 2
    if m.family == "xt":
        return i.usage_page == XT_PAGE and i.usage == 1 and i.in_len >= BRAGI_LEN \
            and i.out_len >= BRAGI_LEN
    return i.usage_page >= 0xFF00 and i.in_len in (BRAGI_LEN, BRAGI_LEN + 1) \
        and i.out_len in (BRAGI_LEN, BRAGI_LEN + 1)


_READERS = {"void": _read_void, "xt": _read_xt, "v2": _read_v2}


class _Sized:
    """Context-manager wrapper exposing the collection's output length to the helpers."""

    def __init__(self, h, out_len: int):
        self._h = h
        self.out_len = out_len

    def __getattr__(self, name):
        return getattr(self._h, name)


def _read_one(info, deadline: float, cancel, handle_factory) -> Device | None:
    m = MODELS[info.pid]
    name = sanitize_name(getattr(info, "product", "") or "") or m.name
    with handle_factory(info.path, in_len=info.in_len) as h:
        return _READERS[m.family](info, name, m, _Sized(h, info.out_len), deadline, cancel)


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None, beta: bool = False) -> list[Device]:
    """Read every supported Corsair headset receiver in `infos`. Never raises.

    Models flagged `beta` are skipped unless `beta` is True."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == CORSAIR_VID and x.pid in MODELS),
                        key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            if i.pid in done or (MODELS[i.pid].beta and not beta) \
                    or not _collection_ok(MODELS[i.pid], i):
                continue
            try:
                dev = _read_one(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel,
                                handle_factory)
            except Exception:
                log.debug("corsair read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is None:
                continue
            done.add(i.pid)
            out.append(dev)
        return out
    except Exception:
        log.debug("corsair scan failed", exc_info=True)
        return []
