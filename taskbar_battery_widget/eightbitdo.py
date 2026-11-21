"""Battery reader for 8BitDo controllers over raw HID (VID 0x2DC8).

Models & protocols (only modes that expose a live battery percentage are reported)
- Ultimate 2 Wireless 2.4 GHz dongle (0x6013 on page 0xFFA0): vendor query
  ``81 04 21 01 00 00 06`` -> ``02 04 04 00 21 01 …`` carries ``0..100 %`` at byte 18 while the
  controller is on; when unanswered, ``81 05 00 21 01`` confirms the dongle is present with the
  controller switched off.
- Ultimate 2C Wireless 2.4 GHz dongle (0x310A on page 0xFF7A): stock firmware never transmits a
  battery level over the 2.4 GHz radio (nor over USB), so stock units yield no device instead of a
  placeholder. Units running firmware with the ``0xBA`` battery signature in the ``81 05 00 21 01``
  identify reply (confirmed across two consecutive replies) report live ``0..100 %`` at byte 8.
- USB D-Input / HIDAPI controllers (Ultimate 2 0x6012, Ultimate 3 0x202F, Pro 2 0x6003/0x6006,
  Pro 3 0x6009, SN30 Pro 0x6001/0x6101, SF30 Pro 0x6000/0x6100): input reports 0x01 / 0x03 / 0x04
  on Generic Desktop gamepad/joystick carry ``level = byte[14] & 0x7F`` and ``charging = bit 7``.
  Older Pro / SN30 / SF30 models switch to enhanced reports once feature report 0x06 is read.

Protocol facts come from public documentation (SDL's ``SDL_hidapi_8bitdo.c``,
``MiguelSanzBr/8bitdo-ultimate2-linux`` and ``Ghermin/8bitdo-2c-battery-patch``); this is an
independent implementation and no code was copied. Only whitelisted read/query commands can ever
be built or sent.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from .hid_win import _is_bluetooth_path, path_hash
from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

EIGHTBITDO_VID = 0x2DC8
VENDOR_IDS: tuple[int, ...] = (EIGHTBITDO_VID,)

U2C_ACTIVE_PID = 0x310A
U2C_DONGLE_HW_PID = 0x301C
U2_DONGLE_PID = 0x6013
U2_DINPUT_PID = 0x6012
ULTIMATE3_PID = 0x202F

DINPUT_FEATURE_PIDS = frozenset({
    0x6000, 0x6100,     # SF30 Pro
    0x6001, 0x6101,     # SN30 Pro
    0x6003, 0x6006,     # Pro 2
    0x6009,             # Pro 3
})
DINPUT_PASSIVE_PIDS = frozenset({U2_DINPUT_PID, ULTIMATE3_PID})
DINPUT_PIDS = DINPUT_FEATURE_PIDS | DINPUT_PASSIVE_PIDS

VENDOR_CONTROLLER_PAGE = 0xFF7A
VENDOR_DONGLE_PAGE = 0xFFA0
VENDOR_USAGE = 0x0001
VENDOR_LEN = 64

GAMEPAD_PAGE = 0x0001
GAMEPAD_USAGES = frozenset({0x0004, 0x0005})
STATE_REPORT_IDS = frozenset({0x01, 0x03, 0x04})
STATE_MIN_LEN = 15
U2_STATE_MIN_LEN = 34
STATE_BATTERY_IDX = 14

ENABLE_ENHANCED_FEATURE_ID = 0x06
ENABLE_ENHANCED_FEATURE_LEN = 64
ALLOWED_FEATURE_IDS = frozenset({ENABLE_ENHANCED_FEATURE_ID})

CMD_IDENTIFY = "identify"
CMD_PAD_STATUS = "pad_status"
ALLOWED_COMMANDS = frozenset({CMD_IDENTIFY, CMD_PAD_STATUS})

_IDENTIFY_PREFIX = b"\x81\x05\x00\x21\x01"
_IDENTIFY_REPLY = b"\x02\x22"
_PAD_STATUS_PREFIX = b"\x81\x04\x21\x01\x00\x00\x06"
_PAD_STATUS_REPLY = b"\x02\x04\x04\x00\x21\x01"
PATCH_SIGNATURE = 0xBA

NAMES: dict[int, str] = {
    U2C_ACTIVE_PID: "8BitDo Ultimate 2C Wireless Controller",
    U2_DONGLE_PID: "8BitDo Ultimate 2 Wireless",
    U2_DINPUT_PID: "8BitDo Ultimate 2 Wireless",
    ULTIMATE3_PID: "8BitDo Ultimate 3",
    0x6000: "8BitDo SF30 Pro",
    0x6100: "8BitDo SF30 Pro",
    0x6001: "8BitDo SN30 Pro",
    0x6101: "8BitDo SN30 Pro",
    0x6003: "8BitDo Pro 2",
    0x6006: "8BitDo Pro 2",
    0x6009: "8BitDo Pro 3",
}

_GENERIC_PRODUCTS = frozenset({
    "8bitdo idle",
    "8bitdo receiver",
    "usb composite device",
})
_CONTROLLER_WRAP_RE = re.compile(r"^controller\s*\((.+)\)$", re.IGNORECASE)

DEVICE_BUDGET_S = 1.5
QUERY_WAIT_S = 0.35
PASSIVE_S = 0.5
READ_SLICE_MS = 200

_triggered: set[str] = set()        # D-Input paths where feature 0x06 was already read


def build_request(command: str) -> bytes:
    """Build a 64-byte whitelisted read-only vendor query; anything else raises ValueError."""
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"command {command!r} is not allowed")
    raw = _IDENTIFY_PREFIX if command == CMD_IDENTIFY else _PAD_STATUS_PREFIX
    return raw.ljust(VENDOR_LEN, b"\x00")


_ALLOWED_PACKETS = frozenset({build_request(c) for c in ALLOWED_COMMANDS})


def parse_identify(buf: bytes | None) -> tuple[int, int | None] | None:
    """(hw_pid, signed_battery_or_None) from an ``02 22`` identify reply, else None."""
    if not buf or len(buf) != VENDOR_LEN or buf[:2] != _IDENTIFY_REPLY:
        return None
    hw_pid = buf[6] | (buf[7] << 8)
    signed = (int(buf[8])
              if (hw_pid == U2C_DONGLE_HW_PID and buf[9] == PATCH_SIGNATURE and buf[8] <= 100)
              else None)
    return hw_pid, signed


def parse_pad_status(buf: bytes | None) -> int | None:
    """Battery percentage (0..100) from an Ultimate 2 dongle ``02 04 04 00 21 01`` reply, else None."""
    if not buf or len(buf) != VENDOR_LEN or buf[:6] != _PAD_STATUS_REPLY:
        return None
    pct = buf[18]
    return int(pct) if pct <= 100 else None


def parse_state_battery(buf: bytes | None, min_len: int = STATE_MIN_LEN) -> tuple[int, bool] | None:
    """(percent, charging) from an enhanced 8BitDo input report (byte 14), else None."""
    need = max(STATE_MIN_LEN, int(min_len))
    if not buf or len(buf) < need or buf[0] not in STATE_REPORT_IDS:
        return None
    raw = buf[STATE_BATTERY_IDX]
    level = raw & 0x7F
    if level > 100:
        return None
    charging = bool(raw & 0x80) and level < 100
    return int(level), charging


def _clean_product(info, default: str) -> str:
    raw = (getattr(info, "product", "") or "").strip()
    m = _CONTROLLER_WRAP_RE.match(raw)
    if m:
        raw = m.group(1)
    s = sanitize_name(raw)
    if not s or s.lower() in _GENERIC_PRODUCTS or "idle" in s.lower():
        return default
    return s


class _ReadOnly:
    """Whitelist wrapper: only whitelisted 64-byte queries and feature report 0x06 are permitted."""

    def __init__(self, h):
        self._h = h

    def drain(self) -> None:
        self._h.drain()

    def read(self, timeout_ms: int = READ_SLICE_MS) -> bytes | None:
        return self._h.read(timeout_ms=timeout_ms)

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        raw = bytes(data)
        if raw not in _ALLOWED_PACKETS:
            raise ValueError("write is not allowed")
        return bool(self._h.write(raw, timeout_ms=timeout_ms))

    def get_feature(self, report_id: int, length: int) -> bytes | None:
        if report_id not in ALLOWED_FEATURE_IDS:
            raise ValueError("feature report is not allowed")
        return self._h.get_feature(report_id, length)


def _stop(cancel: threading.Event | None, until: float) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= until


def _wait_ms(until: float) -> int:
    return max(1, min(READ_SLICE_MS, int((until - time.monotonic()) * 1000)))


def _listen(h: _ReadOnly, parse, until: float, cancel: threading.Event | None):
    """Read until ``parse(report)`` is not None or ``until``. Returns ``(parsed, saw_any_report)``."""
    saw = False
    while not _stop(cancel, until):
        r = h.read(timeout_ms=_wait_ms(until))
        if r is None:
            continue
        saw = True
        parsed = parse(r)
        if parsed is not None:
            return parsed, saw
    return None, saw


def _query_vendor(h: _ReadOnly, command: str, parse, until: float, cancel: threading.Event | None):
    req = build_request(command)
    h.drain()
    if _stop(cancel, until) or not h.write(req, timeout_ms=max(1, min(500, int((until - time.monotonic()) * 1000)))):
        return None
    return _listen(h, parse, min(until, time.monotonic() + QUERY_WAIT_S), cancel)[0]


def _dev(dev_id: str, name: str, battery: int | None, connected: bool, charging: bool, link: str) -> Device:
    return Device(id=dev_id, name=name, kind="gamepad", battery=battery,
                  charging=charging, connected=connected, link=link, source="hid")


def _read_u2c_active(info, deadline: float, cancel: threading.Event | None, factory) -> Device | None:
    """Ultimate 2C over 2.4 GHz: returns a Device only when live signed battery % is present."""
    name = _clean_product(info, NAMES[U2C_ACTIVE_PID])
    with factory(info.path, in_len=VENDOR_LEN) as raw:
        h = _ReadOnly(raw)
        ident = _query_vendor(h, CMD_IDENTIFY, parse_identify, deadline, cancel)
        if ident is None or ident[0] != U2C_DONGLE_HW_PID or ident[1] is None:
            return None
        # Confirm 0xBA signature twice so a random stock-firmware checksum never false-positives.
        ident2 = _query_vendor(h, CMD_IDENTIFY, parse_identify, deadline, cancel)
        if ident2 is None or ident2[0] != U2C_DONGLE_HW_PID or ident2[1] is None:
            return None
    return _dev("eightbitdo:310a", name, ident2[1], True, False, "2.4ghz")


def _read_u2_dongle(info, deadline: float, cancel: threading.Event | None, factory) -> Device | None:
    name = _clean_product(info, NAMES[U2_DONGLE_PID])
    with factory(info.path, in_len=VENDOR_LEN) as raw:
        h = _ReadOnly(raw)
        pct = _query_vendor(h, CMD_PAD_STATUS, parse_pad_status, deadline, cancel)
        if pct is not None:
            return _dev("eightbitdo:6013", name, pct, True, False, "2.4ghz")
        ident = _query_vendor(h, CMD_IDENTIFY, parse_identify, deadline, cancel)
    if ident is None:
        return None
    return _dev("eightbitdo:6013", name, None, False, False, "2.4ghz")


def _read_dinput(info, deadline: float, cancel: threading.Event | None, factory) -> Device | None:
    min_len = U2_STATE_MIN_LEN if info.pid == U2_DINPUT_PID else STATE_MIN_LEN
    name = _clean_product(info, NAMES[info.pid])
    with factory(info.path, in_len=info.in_len) as raw:
        h = _ReadOnly(raw)
        h.drain()
        parse = lambda b: parse_state_battery(b, min_len)               # noqa: E731
        res, saw = _listen(h, parse, min(deadline, time.monotonic() + PASSIVE_S), cancel)
        if res is None and saw and info.pid in DINPUT_FEATURE_PIDS and info.path not in _triggered:
            if not _stop(cancel, deadline):
                _triggered.add(info.path)
                h.get_feature(ENABLE_ENHANCED_FEATURE_ID,
                              max(ENABLE_ENHANCED_FEATURE_LEN, getattr(info, "feat_len", 0) or 0))
                res, _ = _listen(h, parse, deadline, cancel)
    if res is None:
        return None
    level, charging = res
    return _dev(f"eightbitdo:{info.pid:04x}", name, level, True, charging, "usb")


def _select(info):
    """Reader function for ``info``, or None when ``info`` is not a target 8BitDo collection."""
    if getattr(info, "vid", 0) != EIGHTBITDO_VID:
        return None
    pid = getattr(info, "pid", 0)
    up, u = getattr(info, "usage_page", 0), getattr(info, "usage", 0)
    in_len, out_len = getattr(info, "in_len", 0), getattr(info, "out_len", 0)
    if in_len == VENDOR_LEN and out_len == VENDOR_LEN and u == VENDOR_USAGE:
        if pid == U2C_ACTIVE_PID and up == VENDOR_CONTROLLER_PAGE:
            return _read_u2c_active
        if pid == U2_DONGLE_PID and up == VENDOR_DONGLE_PAGE:
            return _read_u2_dongle
    if pid in DINPUT_PIDS and up == GAMEPAD_PAGE and u in GAMEPAD_USAGES:
        if _is_bluetooth_path(getattr(info, "path", "") or ""):
            return None
        need = U2_STATE_MIN_LEN if pid == U2_DINPUT_PID else STATE_MIN_LEN
        if in_len >= need:
            return _read_dinput
    return None


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every supported 8BitDo device in ``infos``. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        mine = [i for i in (infos or ()) if _select(i) is not None]
        _triggered.intersection_update({i.path for i in mine})
        out: list[Device] = []
        done: set[str] = set()
        rank: dict[str, int] = {}
        for i in sorted(mine, key=lambda x: x.path):
            if _stop(cancel, deadline):
                break
            phash = path_hash(i.path)
            if phash in done:
                continue
            reader = _select(i)
            if reader is None:
                continue
            try:
                dev = reader(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel, handle_factory)
            except Exception:
                log.debug("8bitdo read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is not None:
                done.add(phash)
                nth = rank.get(dev.id, 0)
                rank[dev.id] = nth + 1
                if nth:
                    dev.id = f"{dev.id}:{phash}"
                out.append(dev)
        return out
    except Exception:
        log.debug("8bitdo scan failed", exc_info=True)
        return []
