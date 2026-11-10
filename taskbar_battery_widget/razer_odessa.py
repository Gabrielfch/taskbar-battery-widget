"""Direct battery reader for Razer ODESSA-family 2.4 GHz headset dongles.

Protocol (64-byte reports, report id 2, vendor page 0xFF14). Only read-only GET commands are
ever sent (whitelist): 0x20 wireless connection status (dongle), 0x21 battery status and
0x2A charging status (headset).
"""
from __future__ import annotations

import logging
import threading
import time

from .models import Device

log = logging.getLogger(__name__)

RAZER_VID = 0x1532
REPORT_ID = 2
REPORT_LEN = 64
TXN_OFFSET = 0x60
CMD_CONNECTION = 0x20
CMD_BATTERY = 0x21
CMD_CHARGING = 0x2A
ALLOWED_COMMANDS = frozenset({CMD_CONNECTION, CMD_BATTERY, CMD_CHARGING})
VENDOR_PAGE = 0xFF14
CHARGING_ON = 1
STATUS_OK = 2
STATUS_BUSY = (1, 3)
DEVICE_BUDGET_S = 1.0
MAX_BUSY_RETRIES = 3

# dongle usb pid -> (display name, headset pid)
ODESSA_DONGLES: dict[int, tuple[str, int]] = {
    0x0565: ("Razer BlackShark V2 HyperSpeed", 1390),
}

_txn = 0
_txn_lock = threading.Lock()


def next_txn() -> int:
    global _txn
    with _txn_lock:
        _txn = _txn % 31 + 1
        return _txn


def build_request(cmd: bytes, txn: int, remote: bool) -> bytes:
    """64-byte output report for a whitelisted command."""
    if len(cmd) != 3 or cmd[1:] != b"\0\0" or cmd[0] not in ALLOWED_COMMANDS:
        raise ValueError(f"command {bytes(cmd[:3]).hex() or '<empty>'} is not allowed")
    b = bytearray(REPORT_LEN)
    b[0] = REPORT_ID
    b[2] = (txn + TXN_OFFSET) & 0xFF
    b[6] = len(cmd) + 1
    b[9] = 0x80 if remote else 0x00
    b[10:10 + len(cmd)] = cmd
    x = 0
    for v in b[:REPORT_LEN - 2]:
        x ^= v
    b[REPORT_LEN - 2] = x
    assert len(b) == REPORT_LEN
    return bytes(b)


def _reply_checksum(buf: bytes) -> int:
    """Replies are checksummed like requests but the status byte (index 1) is excluded
    (verified against live replies)."""
    x = 0
    for i in range(REPORT_LEN - 2):
        if i != 1:
            x ^= buf[i]
    return x


def parse_reply(buf: bytes | None, cmd: bytes, txn: int) -> tuple[int, bytes] | None:
    """(status, data) when buf answers (cmd, txn); None when it is some other report."""
    if not buf or len(buf) < 14 or not cmd:
        return None
    if len(buf) != REPORT_LEN or buf[REPORT_LEN - 2] != _reply_checksum(buf):
        return None
    if buf[0] != REPORT_ID or buf[2] != (txn + TXN_OFFSET) & 0xFF:
        return None
    if buf[10] != cmd[0] or buf[11] != 1:
        return None
    n = buf[12]
    if 13 + n > len(buf):
        return None
    return buf[1], bytes(buf[13:13 + n])


def _query(h, cmd: bytes, remote: bool, deadline: float,
           cancel: threading.Event | None) -> bytes | None:
    """One command with busy retries; returns the data bytes or None."""
    for _ in range(MAX_BUSY_RETRIES + 1):
        if cancel is not None and cancel.is_set():
            return None
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return None
        txn = next_txn()
        pkt = build_request(cmd, txn, remote)
        h.drain()
        if not h.write(pkt, timeout_ms=max(1, min(1000, remaining_ms))):
            return None
        status = None
        data = b""
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                return None
            r = h.read(timeout_ms=250)
            parsed = parse_reply(r, cmd, txn)
            if parsed is not None:
                status, data = parsed
                break
        if status is None:
            return None
        if status == STATUS_OK:
            return data
        if status not in STATUS_BUSY:
            return None
        time.sleep(0.05)
    return None


def _read_one(info, deadline: float, cancel, handle_factory) -> Device | None:
    name, syn_pid = ODESSA_DONGLES[info.pid]
    dev_id = f"razer:{syn_pid}"
    with handle_factory(info.path, in_len=info.in_len) as h:
        conn = _query(h, bytes([CMD_CONNECTION, 0, 0]), False, deadline, cancel)
        if not conn:
            return None
        if conn[0] != 1:
            return Device(id=dev_id, name=name, kind="headset", battery=None, connected=False,
                          link="2.4ghz", source="hid")
        bat = _query(h, bytes([CMD_BATTERY, 0, 0]), True, deadline, cancel)
        if not bat or bat[0] > 100:
            return None
        chg = _query(h, bytes([CMD_CHARGING, 0, 0]), True, deadline, cancel)
        charging = bool(chg) and chg[0] == CHARGING_ON
        return Device(id=dev_id, name=name, kind="headset", battery=int(bat[0]), connected=True,
                      charging=charging, link="2.4ghz", source="hid")


def read_odessa(infos, deadline: float, cancel: threading.Event | None = None,
                handle_factory=None) -> list[Device]:
    """Read every known ODESSA dongle in `infos`. Never raises; failures yield no device."""
    if handle_factory is None:
        from .hid_win import HidHandle
        handle_factory = HidHandle
    out: list[Device] = []
    done: set[int] = set()
    for i in sorted((x for x in infos if x.vid == RAZER_VID and x.pid in ODESSA_DONGLES),
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
            log.debug("odessa read failed for %04x", i.pid, exc_info=True)
            continue
        if dev is not None:
            done.add(i.pid)
            out.append(dev)
    return out
