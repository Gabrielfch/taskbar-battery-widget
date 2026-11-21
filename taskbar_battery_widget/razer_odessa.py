"""Direct battery reader for Razer ODESSA-family 2.4 GHz headset dongles.

Two 64-byte interrupt-report protocols (report id 2), implemented independently from public
protocol documentation and verified against live hardware:

* HyperSpeed (`1532:0565`, vendor page `0xFF14`): byte 9 selects dongle (`0x00`) vs headset
  (`0x80`), XOR checksum at index 62.
* BlackShark V2 Pro 2023 (`1532:0555`, RZ04-0453, vendor page `0xFF00` usage `0x0001`): MXIC
  `'PA'`/`'PI'` framing (`cmd_type = 0x03` GETs, replies typed `0x08`). Pure GETs are tried
  first; only if the dongle is unarmed (no Synapse) or the 2.4 GHz link is dozing does the
  reader send the fixed host-query enable frame (`0x02/0xE1 = 1`) to wake the link.

Only read-only GET commands (`0x20` wireless connection status, `0x21` battery status, `0x2A`
charging status) and, for `0555`, the fixed `PA_WAKE` frame are whitelisted; building or
sending anything else raises `ValueError`.
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
DEVICE_BUDGET_S = 1.2
MAX_BUSY_RETRIES = 3

PA_PIDS = frozenset({0x0555})
PA_VENDOR_PAGE = 0xFF00
PA_USAGE = 0x0001
PA_DIR_OUT = 0x80
PA_REQ_MAGIC = b"PA"
PA_REP_MAGIC = b"PI"
PA_REP_TYPE = 0x08
PA_CMD_TYPE_GET = 0x03
PA_PROBE_MS = 200
PA_REPLY_MS = 250
PA_WAKE_DELAY_S = 0.035
PA_WAKE = bytes([
    REPORT_ID, PA_DIR_OUT, 0x07, 0x00, 0x00, 0x50, 0x41, 0x0E, 0x00, 0x02, 0xE1, 0x01,
]).ljust(REPORT_LEN, b"\0")

# dongle usb pid -> (display name, device id suffix)
ODESSA_DONGLES: dict[int, tuple[str, int | str]] = {
    0x0555: ("Razer BlackShark V2 Pro", "0555"),
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
    if info.pid in PA_PIDS:
        return _read_pa(info, deadline, cancel, handle_factory)
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


def build_pa_request(cmd_id: int) -> bytes:
    """64-byte 'PA' GET output report for a whitelisted command (0x20, 0x21, 0x2A)."""
    if cmd_id not in ALLOWED_COMMANDS:
        raise ValueError(f"command 0x{cmd_id:02x} is not allowed")
    b = bytearray(REPORT_LEN)
    b[0] = REPORT_ID
    b[1] = PA_DIR_OUT
    b[2] = 0x08
    b[5:7] = PA_REQ_MAGIC
    b[7] = 0x08
    b[9] = PA_CMD_TYPE_GET
    b[10] = cmd_id
    return bytes(b)


ALLOWED_PA_FRAMES = frozenset({PA_WAKE, *(build_pa_request(c) for c in ALLOWED_COMMANDS)})


def parse_pa_reply(buf: bytes | None, cmd_id: int) -> bytes | None:
    """Payload bytes when `buf` is a 'PI' GET reply to `cmd_id`; None for other reports."""
    if not buf or len(buf) != REPORT_LEN:
        return None
    if buf[0] != REPORT_ID or buf[2:4] != PA_REP_MAGIC or buf[4] != PA_REP_TYPE:
        return None
    end = 2 + buf[1]
    if end < 15 or end > REPORT_LEN:
        return None
    if buf[12] != cmd_id or buf[13] != 1:
        return None
    n = buf[14]
    if 15 + n > end:
        return None
    return bytes(buf[15:15 + n])


def _expired(deadline: float, cancel: threading.Event | None) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


def _write_pa(h, pkt: bytes, deadline: float, cancel: threading.Event | None) -> bool:
    if pkt not in ALLOWED_PA_FRAMES:
        raise ValueError("PA frame is not allowed")
    if _expired(deadline, cancel):
        return False
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0:
        return False
    return bool(h.write(pkt, timeout_ms=max(1, min(1000, remaining_ms))))


def _wake_pa(h, deadline: float, cancel: threading.Event | None) -> bool:
    """Wake a dozing 2.4 GHz link and enable host queries (two 0x02/0xE1 frames, 35 ms apart)."""
    for _ in range(2):
        if not _write_pa(h, PA_WAKE, deadline, cancel):
            return False
        delay = min(PA_WAKE_DELAY_S, max(0.0, deadline - time.monotonic()))
        if delay > 0:
            time.sleep(delay)
    h.drain()
    return not _expired(deadline, cancel)


def _query_pa_once(h, cmd_id: int, wait_ms: int, deadline: float,
                   cancel: threading.Event | None) -> bytes | None:
    pkt = build_pa_request(cmd_id)
    if _expired(deadline, cancel):
        return None
    h.drain()
    if not _write_pa(h, pkt, deadline, cancel):
        return None
    until = min(deadline, time.monotonic() + wait_ms / 1000.0)
    for _ in range(8):
        if _expired(deadline, cancel):
            return None
        left_ms = int((until - time.monotonic()) * 1000)
        if left_ms <= 0:
            return None
        r = h.read(timeout_ms=max(1, min(wait_ms, left_ms)))
        if r is None:
            return None
        parsed = parse_pa_reply(r, cmd_id)
        if parsed is not None:
            return parsed
    return None


def _read_pa(info, deadline: float, cancel: threading.Event | None,
             handle_factory) -> Device | None:
    name, syn_pid = ODESSA_DONGLES[info.pid]
    dev_id = f"razer:{syn_pid}"
    with handle_factory(info.path, in_len=info.in_len) as h:
        woke = False
        conn = _query_pa_once(h, CMD_CONNECTION, PA_PROBE_MS, deadline, cancel)
        if conn is None:
            if not _wake_pa(h, deadline, cancel):
                return None
            woke = True
            conn = _query_pa_once(h, CMD_CONNECTION, PA_REPLY_MS, deadline, cancel)
        if not conn:
            return None
        if conn[0] != 1:
            return Device(id=dev_id, name=name, kind="headset", battery=None, connected=False,
                          link="2.4ghz", source="hid")
        bat = _query_pa_once(h, CMD_BATTERY, PA_REPLY_MS if woke else PA_PROBE_MS, deadline, cancel)
        if bat is None and not woke:
            if not _wake_pa(h, deadline, cancel):
                return None
            bat = _query_pa_once(h, CMD_BATTERY, PA_REPLY_MS, deadline, cancel)
        if not bat or bat[0] > 100:
            return None
        chg = _query_pa_once(h, CMD_CHARGING, PA_REPLY_MS, deadline, cancel)
        charging = bool(chg) and chg[0] == CHARGING_ON
        return Device(id=dev_id, name=name, kind="headset", battery=int(bat[0]), connected=True,
                      charging=charging, link="2.4ghz", source="hid")


def _matches_collection(info) -> bool:
    if info.in_len != REPORT_LEN or info.out_len != REPORT_LEN:
        return False
    if info.pid in PA_PIDS:
        return info.usage_page == PA_VENDOR_PAGE and info.usage == PA_USAGE
    return info.usage_page == VENDOR_PAGE


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
        if i.pid in done or not _matches_collection(i):
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
