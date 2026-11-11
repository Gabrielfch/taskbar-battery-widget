"""Battery reader for the Sony DualSense / DualSense Edge (USB and Bluetooth, HID).

The status byte of the extended input report carries the battery: low nibble = level in tenths
(level = min(n * 10 + 5, 100)), high nibble = charge state (0 discharging, 1 charging, 2 full,
0xA/0xB/0xF error). USB: input report 0x01 (64 bytes), status at index 53. Bluetooth: the
controller sends the simple report 0x01 (no battery) until feature report 0x05 is read, then it
streams report 0x31 (78 bytes) with the status at index 54.

Everything is passive except one read-only request: HidD_GetFeature(0x05), at most once per
device connection. Nothing is ever written to the controller.
"""
from __future__ import annotations

import logging
import threading
import time

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

SONY_VID = 0x054C
DUALSENSE_PIDS = frozenset({0x0CE6, 0x0DF2})
GAMEPAD_PAGE, GAMEPAD_USAGE = 0x01, 0x05
USB_IN_LEN, BT_IN_LEN = 64, 78
USB_STATUS_INDEX, BT_STATUS_INDEX = 53, 54
BT_EXTENDED_ID = 0x31
SIMPLE_ID = 0x01
FEATURE_ID = 0x05                       # calibration report: reading it enables extended mode
ALLOWED_FEATURE_IDS = frozenset({FEATURE_ID})
DEFAULT_NAME = "DualSense Wireless Controller"
PASSIVE_S = 0.6
DEVICE_BUDGET_S = 1.8

_triggered: set[str] = set()            # BT device paths whose extended mode was already requested


def decode_status(status: int) -> tuple[int, bool, bool]:
    """Status byte -> (level %, charging, full)."""
    status &= 0xFF
    level = min((status & 0x0F) * 10 + 5, 100)
    state = status >> 4
    if state == 1:
        return level, True, False
    if state == 2:
        return 100, False, True
    return level, False, False          # 0 discharging; 0xA/0xB/0xF and others: keep level


def parse_report(buf: bytes | None, transport: str) -> int | None:
    """Status byte of a battery-carrying input report, else None (e.g. simple BT report)."""
    if not buf:
        return None
    if transport == "usb":
        return buf[USB_STATUS_INDEX] if buf[0] == SIMPLE_ID and len(buf) > USB_STATUS_INDEX else None
    if transport == "bluetooth":
        return buf[BT_STATUS_INDEX] if buf[0] == BT_EXTENDED_ID and len(buf) > BT_STATUS_INDEX else None
    return None


def _transport(info) -> str | None:
    path = (getattr(info, "path", "") or "").lower()
    if info.in_len == BT_IN_LEN or "bthenum" in path or "{00001124" in path:
        return "bluetooth"
    if info.in_len == USB_IN_LEN:
        return "usb"
    return None


class _ReadOnly:
    """Whitelist wrapper: only drain, read and get_feature(0x05) reach the handle."""

    def __init__(self, h):
        self._h = h

    def drain(self):
        self._h.drain()

    def read(self, timeout_ms=250):
        return self._h.read(timeout_ms=timeout_ms)

    def get_feature(self, report_id: int, length: int):
        if report_id not in ALLOWED_FEATURE_IDS:
            raise ValueError("feature report is not allowed")
        return self._h.get_feature(report_id, length)


def _listen(h, transport, until: float, cancel) -> tuple[int | None, bool]:
    """Read reports until a battery status shows up or `until`. Returns (status, saw_any)."""
    saw = False
    while time.monotonic() < until:
        if cancel is not None and cancel.is_set():
            break
        r = h.read(timeout_ms=max(1, min(200, int((until - time.monotonic()) * 1000))))
        if r is None:
            continue
        saw = True
        s = parse_report(r, transport)
        if s is not None:
            return s, saw
    return None, saw


def _read_status(info, transport, deadline, cancel, enable_bt_extended, handle_factory):
    with handle_factory(info.path, in_len=info.in_len) as raw:
        h = _ReadOnly(raw)
        h.drain()
        status, saw = _listen(h, transport, min(deadline, time.monotonic() + PASSIVE_S), cancel)
        if status is not None or transport != "bluetooth" or not saw or not enable_bt_extended \
                or info.path in _triggered:
            return status
        _triggered.add(info.path)
        h.get_feature(FEATURE_ID, max(41, info.feat_len or 0))
        return _listen(h, transport, deadline, cancel)[0]


def read_dualsense(infos, deadline: float, cancel: threading.Event | None = None,
                   enable_bt_extended: bool = True, handle_factory=None) -> list[Device]:
    """Read every DualSense in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        sony = [i for i in infos if i.vid == SONY_VID and i.pid in DUALSENSE_PIDS]
        live = {i.path for i in sony}
        _triggered.intersection_update(live)    # a vanished device starts over when it returns
        out: list[Device] = []
        rank: dict[int, int] = {}               # pid -> controllers of that pid seen so far
        for i in sorted(sony, key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            if i.usage_page != GAMEPAD_PAGE or i.usage != GAMEPAD_USAGE:
                continue
            nth = rank.get(i.pid, 0)
            rank[i.pid] = nth + 1
            tr = _transport(i)
            if tr is None:
                continue
            try:
                status = _read_status(i, tr, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                      cancel, enable_bt_extended, handle_factory)
            except Exception:
                log.debug("dualsense read failed for %04x", i.pid, exc_info=True)
                continue
            if status is None:
                continue
            level, charging, _full = decode_status(status)
            from .hid_win import path_hash
            # first controller of a pid keeps 'sony:<pid>'; further identical ones get a path hash
            dev_id = f"sony:{i.pid:04x}" + (f":{path_hash(i.path)}" if nth else "")
            out.append(Device(id=dev_id,
                              name=sanitize_name(getattr(i, "product", "") or "") or DEFAULT_NAME,
                              kind="gamepad", battery=level, connected=True, charging=charging,
                              link=tr, source="hid"))
        return out
    except Exception:
        log.debug("dualsense scan failed", exc_info=True)
        return []
