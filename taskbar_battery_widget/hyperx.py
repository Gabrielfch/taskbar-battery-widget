"""Direct battery reader for the HyperX Cloud Flight Wireless headset (USB dongle, HID).

Protocol (vendor collection, usage page 0xFF53, 20-byte reports): the output report
`21 FF 05 00...` asks for the battery voltage; the input report `21 FF 05 HI LO ...` answers
with the cell voltage in millivolts (big-endian). Only that one read-only request is ever sent.
"""
from __future__ import annotations

import bisect
import logging
import threading
import time

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

HYPERX_VID = 0x0951
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
        mv = parse_reply(h.read(timeout_ms=250))
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


def read_hyperx(infos, deadline: float, cancel: threading.Event | None = None,
                handle_factory=None) -> list[Device]:
    """Read every HyperX Cloud Flight dongle in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == HYPERX_VID and x.pid in HYPERX_PIDS),
                        key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            if i.pid in done or i.usage_page != VENDOR_PAGE or i.in_len != REPORT_LEN \
                    or i.out_len != REPORT_LEN:
                continue
            try:
                dev = _read_one(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel,
                                handle_factory)
            except Exception:
                log.debug("hyperx read failed for %04x", i.pid, exc_info=True)
                continue
            done.add(i.pid)
            out.append(dev)
        return out
    except Exception:
        log.debug("hyperx scan failed", exc_info=True)
        return []
