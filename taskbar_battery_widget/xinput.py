"""Battery reader for Xbox controllers through XInput (read-only, no HID access).

XInputGetBatteryInformation(user 0..3, BATTERY_DEVTYPE_GAMEPAD) returns a battery type and a
coarse level (empty / low / medium / full). The level is mapped to 5 / 30 / 65 / 100 %, so the
percentage is only approximate. A wired controller reports no battery at all.

XInput cannot tell a 2.4 GHz wireless adapter from Bluetooth, so wireless pads are labelled
"2.4ghz". A pad paired over Bluetooth is also listed (with a real percentage) by the Bluetooth
scanner; `drop_bluetooth_duplicates` lets the integration remove the XInput twin.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time

from .models import Device

log = logging.getLogger(__name__)

VENDOR_IDS: tuple[int, ...] = ()

DEVTYPE_GAMEPAD = 0
MAX_USERS = 4
ERROR_SUCCESS = 0
TYPE_DISCONNECTED, TYPE_WIRED, TYPE_ALKALINE, TYPE_NIMH, TYPE_UNKNOWN = 0, 1, 2, 3, 0xFF
LEVEL_PERCENT = {0: 5, 1: 30, 2: 65, 3: 100}        # empty / low / medium / full (approximate)
DLL_NAMES = ("xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll")
NAME = "Xbox Controller"
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800

_fn = None
_fn_lock = threading.Lock()


class _BatteryInfo(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


def _load():
    """XInputGetBatteryInformation from the first DLL that has it, else None."""
    global _fn
    with _fn_lock:
        if _fn is not None:
            return _fn
        if sys.platform != "win32":
            return None
        sysdir = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")
        for name in DLL_NAMES:
            try:       # full System32 path: never resolved through the search path / app directory
                f = ctypes.WinDLL(os.path.join(sysdir, name),
                                  winmode=LOAD_LIBRARY_SEARCH_SYSTEM32).XInputGetBatteryInformation
            except (OSError, AttributeError):
                continue
            f.argtypes = [ctypes.c_uint32, ctypes.c_ubyte, ctypes.POINTER(_BatteryInfo)]
            f.restype = ctypes.c_uint32
            _fn = f
            return f
        return None


def _query(index: int):
    """(battery type, level) for one user slot, or None when the slot is empty or XInput is missing."""
    f = _load()
    if f is None:
        return None
    info = _BatteryInfo()
    if f(index, DEVTYPE_GAMEPAD, ctypes.byref(info)) != ERROR_SUCCESS:
        return None
    return info.BatteryType, info.BatteryLevel


def decode(btype: int, level: int) -> tuple[int | None, str] | None:
    """(battery %, link) for a reading, or None for a disconnected slot."""
    if btype == TYPE_DISCONNECTED:
        return None
    if btype == TYPE_WIRED:
        return None, "usb"
    if btype in (TYPE_ALKALINE, TYPE_NIMH):
        return LEVEL_PERCENT.get(level), "2.4ghz"
    return None, "2.4ghz"           # UNKNOWN type: the level is not trustworthy


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read XInput user slots 0..3. `infos` and `handle_factory` are ignored. Never raises."""
    out: list[Device] = []
    try:
        for i in range(MAX_USERS):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            try:
                r = _query(i)
                d = decode(*r) if r else None
            except Exception:
                log.debug("xinput query failed for slot %d", i, exc_info=True)
                continue
            if d is None:
                continue
            battery, link = d
            out.append(Device(id=f"xinput:{i}", name=NAME, kind="gamepad", battery=battery,
                              connected=True, charging=False, link=link, source="hid"))
    except Exception:
        log.debug("xinput scan failed", exc_info=True)
    return out


def drop_bluetooth_duplicates(xinput_devices: list[Device], bluetooth_devices: list[Device]) -> list[Device]:
    """Remove XInput entries already covered by Bluetooth "Xbox Wireless Controller" entries.

    XInput slots cannot be matched to Bluetooth addresses, so for every connected Bluetooth Xbox
    controller that has a battery percentage one non-wired XInput entry is dropped (lowest slot
    first). Wired entries are kept.
    """
    n = sum(1 for d in bluetooth_devices
            if d.link == "bluetooth" and d.connected and d.battery is not None
            and "xbox" in (d.name or "").lower())
    out = []
    for d in xinput_devices:
        if n > 0 and d.link != "usb":
            n -= 1
            continue
        out.append(d)
    return out
