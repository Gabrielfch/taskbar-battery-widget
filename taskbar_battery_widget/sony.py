"""Battery reader for Sony devices over raw HID (VID 0x054C), except DualSense (see dualsense.py).

Models
- DualShock 4 (0x05C4, 0x09CC) and its USB wireless adapter (0x0BA0): input report 0x01 (USB,
  64 bytes) or 0x11 (Bluetooth, extended) with a status byte; low nibble = level, bit 4 = cable.
  Over Bluetooth the controller only sends the extended report after feature report 0x02 was read
  once per connection (the one query this module issues for controllers).
- PlayStation PULSE 3D (0x0D5E): the dongle pushes a status report (id 0xB0) on changes only.
- INZONE H5 (0x0EBF) / H9 II (0x0FA8): vendor HCI-over-HID on usage page 0xFF04; one GET of the
  battery event. INZONE Buds (0x0EC2): the dongle pushes a battery report, listened to passively.

Only the battery GET request and feature report 0x02 can ever reach a device.
"""
from __future__ import annotations

import logging
import threading
import time

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

VENDOR_IDS = (0x054C,)

DS4_PIDS = frozenset({0x05C4, 0x09CC})
DS4_DONGLE_PID = 0x0BA0
PULSE_PID = 0x0D5E
H5_PID, H9II_PID = 0x0EBF, 0x0FA8
BUDS_PID = 0x0EC2

GAMEPAD_PAGE, GAMEPAD_USAGE = 0x01, 0x05
DS4_USB_ID, DS4_BT_ID = 0x01, 0x11
DS4_USB_STATUS, DS4_BT_STATUS = 30, 32          # index incl. report id; BT has 2 extra header bytes
DS4_USB_LEN = 64
DS4_FEATURE_ID, DS4_FEATURE_LEN = 0x02, 37      # calibration report; reading it enables 0x11 on BT
ALLOWED_FEATURE_IDS = frozenset({DS4_FEATURE_ID})
DS4_DONGLE_NO_PAD = 0x04                        # status[1] bit: adapter has no controller (uncertain)

PULSE_PAGE, PULSE_USAGE = 0xFF01, 0x0020
PULSE_ID, PULSE_LEN = 0xB0, 8
PULSE_CHARGING, PULSE_CONNECTED = 0x80, 0x08

INZONE_PAGE = 0xFF04
INZONE_USAGE = {H5_PID: 0x0002, H9II_PID: 0x0001}
INZONE_LEN = 64
HCI_REPORT_ID = 0x02
EID_BATTERY = 0x04
ETYPE_GET, ETYPE_RET, ETYPE_NTFY_ACTIVE = 0x01, 0x10, 0xA0
ADDR_PC_TO_RX, ADDR_PC = 0x41, 0x1
TID = 2
KEY = (0x96, 0xC3)
ALLOWED_EVENTS = frozenset({(ADDR_PC_TO_RX, EID_BATTERY, ETYPE_GET)})

BUDS_LEN = 64
BUDS_TYPE, BUDS_SUBTYPE = 0x12, 0x04
BUDS_RIGHT, BUDS_LEFT, BUDS_CHECK = 14, 16, 19

NAMES = {0x05C4: "DualShock 4", 0x09CC: "DualShock 4", DS4_DONGLE_PID: "DualShock 4",
         PULSE_PID: "PULSE 3D", H5_PID: "INZONE H5", H9II_PID: "INZONE H9 II", BUDS_PID: "INZONE Buds"}
PASSIVE_S = 0.6
PUSH_PASSIVE_S = 0.3                            # PULSE 3D / Buds push on change: short listen window
DEVICE_BUDGET_S = 1.5

_triggered: set[str] = set()                    # BT paths whose extended mode was already requested


# ---- DualShock 4 -------------------------------------------------------------------------

def decode_ds4_status(status: int) -> tuple[int, bool]:
    """Status byte -> (level %, charging). Level is approximate (10 % steps)."""
    n = status & 0x0F
    if status & 0x10:                           # cable connected: 0..10 charging, 11 full
        return (100 if n >= 10 else n * 10 + 5), n <= 10
    return min(n * 10 + 5, 100), False


def parse_ds4(buf: bytes | None, transport: str, pid: int = 0) -> tuple[int, bool, bool] | None:
    """(level, charging, pad_present) from a report, else None."""
    if not buf:
        return None
    if transport == "usb" and buf[0] == DS4_USB_ID and len(buf) > DS4_USB_STATUS + 1:
        s, s1 = buf[DS4_USB_STATUS], buf[DS4_USB_STATUS + 1]
    elif transport == "bluetooth" and buf[0] == DS4_BT_ID and len(buf) > DS4_BT_STATUS + 1:
        s, s1 = buf[DS4_BT_STATUS], buf[DS4_BT_STATUS + 1]
    else:
        return None
    level, charging = decode_ds4_status(s)
    present = not (pid == DS4_DONGLE_PID and s1 & DS4_DONGLE_NO_PAD)
    return level, charging, present


# ---- PULSE 3D / Buds ---------------------------------------------------------------------

def parse_pulse(buf: bytes | None) -> tuple[int | None, bool, bool] | None:
    """(level or None, charging, connected) from a 0xB0 status report, else None."""
    if not buf or len(buf) < PULSE_LEN or buf[0] != PULSE_ID:
        return None
    connected = bool(buf[4] & PULSE_CONNECTED)
    lvl = buf[3]
    if lvl == PULSE_CHARGING:
        return None, True, connected
    return (lvl if lvl <= 100 else None), False, connected


def parse_buds(buf: bytes | None) -> int | None:
    """Lowest earbud level (%) from a battery report, else None. Verifies the checksum byte."""
    if not buf or len(buf) <= BUDS_CHECK or buf[0] != HCI_REPORT_ID \
            or buf[1] != BUDS_TYPE or buf[2] != BUDS_SUBTYPE:
        return None
    r, l = buf[BUDS_RIGHT], buf[BUDS_LEFT]
    if (r + l + 117) & 0xFF != buf[BUDS_CHECK] or r > 100 or l > 100:
        return None
    return min(r, l)


# ---- INZONE HCI --------------------------------------------------------------------------

def build_request(address: int, event_id: int, event_type: int, payload: bytes = b"") -> bytes:
    """Whitelisted vendor GET request (64 bytes). Anything but the battery GET raises ValueError."""
    if (address, event_id, event_type) not in ALLOWED_EVENTS or payload:
        raise ValueError("request is not allowed")
    buf = bytearray(INZONE_LEN)
    buf[0] = HCI_REPORT_ID
    buf[1] = 12                                  # hid length = 12 + payload
    buf[2:7] = bytes((0x01, 0x00, 0xFC, 8, KEY[0]))
    buf[7] = KEY[1]
    buf[8], buf[9], buf[10] = address, event_id, event_type
    buf[11], buf[12] = TID & 0xFF, TID >> 8
    buf[13] = sum(buf[6:13]) & 0xFF
    return bytes(buf)


def parse_inzone_battery(buf: bytes | None):
    """(percent, charging) from a matching battery reply; "offline" when the headset is off; else None."""
    if not buf or len(buf) < 16 or buf[0] != HCI_REPORT_ID:
        return None
    n = buf[1]
    if n < 12 or n > INZONE_LEN - 2 or len(buf) < n + 2:
        return None
    if buf[2] != 0x04 or buf[3] != 0xFF or buf[5] != 0 or (buf[6], buf[7]) != KEY \
            or buf[8] >> 4 != ADDR_PC:
        return None
    if sum(buf[5:n + 1]) & 0xFF != buf[n + 1]:
        return None
    if buf[9] != EID_BATTERY or buf[10] not in (ETYPE_RET, ETYPE_NTFY_ACTIVE) \
            or buf[11] | buf[12] << 8 != TID:
        return None
    payload = buf[13:n + 1]
    if len(payload) < 2:
        return None
    if payload[1] == 0xFF:
        return "offline"
    if payload[1] > 100:
        return None
    return payload[1], payload[0] != 0


# ---- plumbing ----------------------------------------------------------------------------

class _ReadOnly:
    """Whitelist wrapper: drain, read, battery GET write, feature report 0x02."""

    def __init__(self, h):
        self._h = h

    def drain(self):
        self._h.drain()

    def read(self, timeout_ms=250):
        return self._h.read(timeout_ms=timeout_ms)

    def write(self, data: bytes, timeout_ms=500):
        if bytes(data) != build_request(ADDR_PC_TO_RX, EID_BATTERY, ETYPE_GET):
            raise ValueError("write is not allowed")
        return self._h.write(data, timeout_ms)

    def get_feature(self, report_id: int, length: int):
        if report_id not in ALLOWED_FEATURE_IDS:
            raise ValueError("feature report is not allowed")
        return self._h.get_feature(report_id, length)


def _stop(cancel, until) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= until


def _wait_ms(until: float) -> int:
    return max(1, min(200, int((until - time.monotonic()) * 1000)))


def _listen(h, parse, until: float, cancel):
    """Read until `parse(report)` is not None or `until`. Returns (parsed, saw_any_report)."""
    saw = False
    while not _stop(cancel, until):
        r = h.read(timeout_ms=_wait_ms(until))
        if r is None:
            continue
        saw = True
        p = parse(r)
        if p is not None:
            return p, saw
    return None, saw


def _transport(info) -> str | None:
    path = (getattr(info, "path", "") or "").lower()
    if "bthenum" in path or "{00001124" in path or info.in_len > DS4_USB_LEN:
        return "bluetooth"
    return "usb" if info.in_len == DS4_USB_LEN else None


def _dev(pid, kind, battery, connected, charging, link, info) -> Device:
    return Device(id=f"sony:{pid:04x}",
                  name=sanitize_name(getattr(info, "product", "") or "") or NAMES[pid],
                  kind=kind, battery=battery, charging=charging, connected=connected,
                  link=link, source="hid")


def _read_ds4(i, deadline, cancel, ext, factory):
    tr = _transport(i)
    if tr is None:
        return None
    with factory(i.path, in_len=i.in_len) as raw:
        h = _ReadOnly(raw)
        h.drain()

        def parse(b):
            return parse_ds4(b, tr, i.pid)

        res, saw = _listen(h, parse, min(deadline, time.monotonic() + PASSIVE_S), cancel)
        if res is None and tr == "bluetooth" and saw and ext and i.path not in _triggered:
            _triggered.add(i.path)
            h.get_feature(DS4_FEATURE_ID, max(DS4_FEATURE_LEN, i.feat_len or 0))
            res = _listen(h, parse, deadline, cancel)[0]
    if res is None:
        return None
    level, charging, present = res
    if not present:
        return _dev(i.pid, "gamepad", None, False, False, tr, i)
    return _dev(i.pid, "gamepad", level, True, charging, tr, i)


def _read_pulse(i, deadline, cancel, ext, factory):
    with factory(i.path, in_len=i.in_len) as raw:
        h = _ReadOnly(raw)
        res = None
        end = min(deadline, time.monotonic() + PUSH_PASSIVE_S)
        # queued reports first (newest wins); with none seen, wait briefly for a push
        while not _stop(cancel, end):
            r = h.read(timeout_ms=_wait_ms(end))
            if r is None:
                if res is not None:
                    break
                continue
            p = parse_pulse(r)
            if p is not None:
                res = p
    if res is None:
        return None
    level, charging, connected = res
    if not connected:
        return _dev(i.pid, "headset", None, False, False, "2.4ghz", i)
    return _dev(i.pid, "headset", level, True, charging, "2.4ghz", i)


def _read_inzone(i, deadline, cancel, ext, factory):
    req = build_request(ADDR_PC_TO_RX, EID_BATTERY, ETYPE_GET)
    with factory(i.path, in_len=i.in_len) as raw:
        h = _ReadOnly(raw)
        h.drain()
        if _stop(cancel, deadline) or not h.write(req, 500):
            return None
        res, _ = _listen(h, parse_inzone_battery, deadline, cancel)
    if res is None:
        return None
    if res == "offline":
        return _dev(i.pid, "headset", None, False, False, "2.4ghz", i)
    return _dev(i.pid, "headset", res[0], True, res[1], "2.4ghz", i)


def _read_buds(i, deadline, cancel, ext, factory):
    with factory(i.path, in_len=i.in_len) as raw:
        h = _ReadOnly(raw)
        res, _ = _listen(h, parse_buds, min(deadline, time.monotonic() + PUSH_PASSIVE_S), cancel)
    return None if res is None else _dev(i.pid, "headset", res, True, False, "2.4ghz", i)


def _select(i):
    """Reader for a collection of `i`, or None when it is not the collection the protocol needs."""
    if i.pid in DS4_PIDS or i.pid == DS4_DONGLE_PID:
        return _read_ds4 if (i.usage_page, i.usage) == (GAMEPAD_PAGE, GAMEPAD_USAGE) else None
    if i.pid == PULSE_PID:
        return _read_pulse if (i.usage_page, i.usage) == (PULSE_PAGE, PULSE_USAGE) else None
    if i.pid in INZONE_USAGE:
        ok = (i.usage_page, i.usage) == (INZONE_PAGE, INZONE_USAGE[i.pid]) and i.in_len >= INZONE_LEN \
            and i.out_len == INZONE_LEN
        return _read_inzone if ok else None
    if i.pid == BUDS_PID:
        return _read_buds if i.in_len == BUDS_LEN and i.usage_page >= 0xFF00 else None
    return None


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None, enable_bt_extended: bool = True) -> list[Device]:
    """Read every supported Sony device (not DualSense) in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        mine = [i for i in infos if i.vid == VENDOR_IDS[0] and _select(i) is not None]
        _triggered.intersection_update({i.path for i in mine})
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted(mine, key=lambda x: x.path):
            if _stop(cancel, deadline):
                break
            if i.pid in done:
                continue
            try:
                d = _select(i)(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel,
                               enable_bt_extended, handle_factory)
            except Exception:
                log.debug("sony read failed for %04x", i.pid, exc_info=True)
                continue
            if d is not None:
                done.add(i.pid)
                out.append(d)
        return out
    except Exception:
        log.debug("sony scan failed", exc_info=True)
        return []
