"""Direct battery readers for assorted wireless headset dongles (raw HID, read-only).

Covered: Lenovo Wireless VoIP headset receiver, MCHOSE X9 and MCHOSE V9 Pro dongles. Every
request comes from a module-level whitelist; building anything else raises ValueError. Not covered on purpose: Razer BlackShark V2 Pro (PlayStation)
needs a dongle mode switch (a state-changing command) before the battery can be asked, the
Roccat Elo 7.1 Air has no battery query at all, and the Plantronics BT600 needs non-GET session
(HELLO) frames. A dongle that stays silent yields no device (silence is not "headset off").
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

LENOVO_VID = 0x17EF
MCHOSE_VID = 0x3837
MCHOSE_V9_VID = 0x291D
VENDOR_IDS: tuple[int, ...] = (LENOVO_VID, MCHOSE_VID, MCHOSE_V9_VID)

DEVICE_BUDGET_S = 1.5
READ_SLICE_MS = 250
MAX_SKIPPED_REPORTS = 24

# ---------------------------------------------------------------- Lenovo Wireless VoIP
LENOVO_LEN = 61
LENOVO_CMD_ID, LENOVO_RSP_ID = 0x24, 0x27
LENOVO_CMD_STATUS = 0x01
LENOVO_ALLOWED_COMMANDS = frozenset({LENOVO_CMD_STATUS})


def build_lenovo_request(cmd: int) -> bytes:
    if cmd not in LENOVO_ALLOWED_COMMANDS:
        raise ValueError(f"command {cmd:#04x} is not allowed")
    b = bytearray(LENOVO_LEN)
    b[0], b[1] = LENOVO_CMD_ID, cmd
    return bytes(b)


def parse_lenovo_status(buf: bytes | None) -> tuple[str, int | None] | None:
    """('ok', percent) | ('off', None) when the receiver reports no headset | None if unrelated."""
    if not buf or len(buf) != LENOVO_LEN or buf[0] != LENOVO_RSP_ID or buf[1] != LENOVO_CMD_STATUS:
        return None
    if buf[2] != 0:
        return "off", None
    if buf[7] > 100:
        return None
    return "ok", int(buf[7])


# ---------------------------------------------------------------- MCHOSE (X9, V9 Pro)
MCHOSE_LEN = 64
MCHOSE_REPORT_ID = 0x55
MCHOSE_MSG_VALUE = 0x65
MCHOSE_QUERY_BATTERY = 0x01
MCHOSE_FIELD_BATTERY = 0x02
MCHOSE_ALLOWED_QUERIES = frozenset({MCHOSE_QUERY_BATTERY})


def build_mchose_request(query: int) -> bytes:
    if query not in MCHOSE_ALLOWED_QUERIES:
        raise ValueError(f"query {query:#04x} is not allowed")
    b = bytearray(MCHOSE_LEN)
    b[0], b[1], b[2] = MCHOSE_REPORT_ID, MCHOSE_MSG_VALUE, query
    return bytes(b)


def parse_mchose_battery(buf: bytes | None) -> int | None:
    if (not buf or len(buf) < 4 or buf[0] != MCHOSE_REPORT_ID or buf[1] != MCHOSE_MSG_VALUE
            or buf[3] != MCHOSE_FIELD_BATTERY or buf[2] > 100):
        return None
    return int(buf[2])


# ---------------------------------------------------------------- shared plumbing
def _stop(deadline: float, cancel: threading.Event | None) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


def _send(h, req: bytes, allowed: frozenset[bytes], deadline: float,
          cancel: threading.Event | None) -> bool:
    if req not in allowed:
        raise ValueError("request is not allowed")
    if _stop(deadline, cancel):
        return False
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    return bool(h.write(req, timeout_ms=max(1, min(1000, remaining_ms))))


def _await(h, deadline: float, cancel: threading.Event | None, accept):
    """First report for which accept(buf) is not None (that value), else None."""
    for _ in range(MAX_SKIPPED_REPORTS):
        if _stop(deadline, cancel):
            return None
        res = accept(h.read(timeout_ms=READ_SLICE_MS))
        if res is not None:
            return res
    return None


# Each reader returns None (no usable answer, device skipped) or (battery, charging, connected).
_LENOVO_ALLOWED = frozenset({build_lenovo_request(c) for c in LENOVO_ALLOWED_COMMANDS})


def _read_lenovo(h, deadline, cancel):
    h.drain()
    req = build_lenovo_request(LENOVO_CMD_STATUS)
    if req not in _LENOVO_ALLOWED:
        raise ValueError("request is not allowed")
    if _stop(deadline, cancel) or not h.set_feature(req):
        return None
    res = _await(h, deadline, cancel, parse_lenovo_status)
    if res is None:
        return None                                  # silent: keep the cached value
    if res[0] == "off":
        return (None, False, False)
    return (res[1], False, True)                     # charging is not reported by this receiver


_MCHOSE_ALLOWED = frozenset({build_mchose_request(q) for q in MCHOSE_ALLOWED_QUERIES})


def _read_mchose(h, deadline, cancel):
    h.drain()
    if not _send(h, build_mchose_request(MCHOSE_QUERY_BATTERY), _MCHOSE_ALLOWED, deadline, cancel):
        return None
    pct = _await(h, deadline, cancel, parse_mchose_battery)
    if pct is None:
        return None
    return (pct, False, True)                        # charging is not reported by the dongle


@dataclass(frozen=True)
class Model:
    vendor: str
    name: str
    usage_page: int
    usage: int
    in_len: int
    out_len: int
    feat_len: int          # 0 = not required
    reader: object


MODELS: dict[tuple[int, int], Model] = {
    (LENOVO_VID, 0xA07D): Model("lenovo", "Lenovo Wireless VoIP Headset", 0xFF07, 0x222,
                                LENOVO_LEN, LENOVO_LEN, LENOVO_LEN, _read_lenovo),
    (MCHOSE_VID, 0x6045): Model("mchose", "MCHOSE X9 Wireless", 0xFF90, 0x01,
                                MCHOSE_LEN, MCHOSE_LEN, 0, _read_mchose),
    (MCHOSE_V9_VID, 0x385D): Model("mchose", "MCHOSE V9 Pro Wireless", 0xFF90, 0x01,
                                   MCHOSE_LEN, MCHOSE_LEN, 0, _read_mchose),
}


def _matches(i) -> Model | None:
    m = MODELS.get((i.vid, i.pid))
    if m is None or i.usage_page != m.usage_page or i.usage != m.usage:
        return None
    if i.in_len != m.in_len or i.out_len != m.out_len:
        return None
    if m.feat_len and getattr(i, "feat_len", m.feat_len) != m.feat_len:
        return None
    return m


def _read_one(info, model: Model, deadline: float, cancel, handle_factory) -> Device | None:
    with handle_factory(info.path, in_len=info.in_len) as h:
        res = model.reader(h, deadline, cancel)
    if res is None:
        return None
    battery, charging, connected = res
    name = sanitize_name(getattr(info, "product", "") or "") or model.name
    return Device(id=f"{model.vendor}:{info.pid:04x}", name=name, kind="headset", battery=battery,
                  charging=charging, connected=connected, link="2.4ghz", source="hid")


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every supported dongle in `infos`. Never raises; failures yield no device."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[tuple[int, int]] = set()
        for i in sorted((x for x in infos if x.vid in VENDOR_IDS), key=lambda x: x.path):
            if _stop(deadline, cancel):
                break
            model = _matches(i)
            if model is None or (i.vid, i.pid) in done:
                continue
            try:
                dev = _read_one(i, model, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                cancel, handle_factory)
            except Exception:
                log.debug("misc headset read failed for %04x:%04x", i.vid, i.pid, exc_info=True)
                continue
            if dev is not None:
                done.add((i.vid, i.pid))
                out.append(dev)
        return out
    except Exception:
        log.debug("misc headset scan failed", exc_info=True)
        return []
