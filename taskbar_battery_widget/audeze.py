"""Direct battery reader for Audeze Maxwell and Maxwell 2 headsets (USB dongle, HID).

Protocol (vendor page 0xFF13, 62-byte reports): a message `05 5A len(LE16) body` is cut into
output reports `06 <n> 80 <fragment>`; the reply is polled from input report 0x07 (same layout)
and the fragments are reassembled. The only body ever sent is the battery query `D6 0C 00`; the
answer is a type 0x5D message whose byte 8 is the percentage. No charging flag is available.
Only an explicit "refused" answer marks the headset off; no answer at all yields no device.
"""
from __future__ import annotations

import logging
import threading
import time

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

AUDEZE_VID = 0x3329
VENDOR_IDS = (AUDEZE_VID,)
VENDOR_PAGE = 0xFF13
REPORT_LEN = 62
REPORT_ID_OUT = 0x06
REPORT_ID_IN = 0x07
ROUTE_HEADSET = 0x80
FRAGMENT_OFFSET = 3
MARKER = 0x05
TYPE_REQUEST, TYPE_RESPONSE, TYPE_RESULT = 0x5A, 0x5B, 0x5D
HEADER_LEN = 4
MAX_MESSAGE = 1024
CMD_BATTERY = bytes([0xD6, 0x0C])
BATTERY_BODY = CMD_BATTERY + b"\x00"
ALLOWED_BODIES = frozenset({BATTERY_BODY})
STATUS_OK = 0x00
STATUS_OFFSET = 6
PERCENT_OFFSET = 8
DEVICE_BUDGET_S = 1.5
POLL_S = 0.05
ATTEMPT_S = 0.7

# pid -> display name
MODELS: dict[int, str] = {
    0x4B19: "Audeze Maxwell",
    0x4B18: "Audeze Maxwell",              # Xbox dongle
    0x4B29: "Audeze Maxwell 2",
    0x4B28: "Audeze Maxwell 2",            # Xbox version
}


def build_message(body: bytes) -> bytes:
    """`05 5A len body` for a whitelisted body; ValueError otherwise."""
    if bytes(body) not in ALLOWED_BODIES:
        raise ValueError("request is not allowed")
    return bytes([MARKER, TYPE_REQUEST]) + len(body).to_bytes(2, "little") + bytes(body)


def build_report(message: bytes, out_len: int = REPORT_LEN) -> bytes:
    """Single output report carrying a whole (short) message."""
    if len(message) > REPORT_LEN - FRAGMENT_OFFSET:
        raise ValueError("message does not fit one report")
    rep = bytes([REPORT_ID_OUT, len(message), ROUTE_HEADSET]) + message
    return rep.ljust(max(out_len, REPORT_LEN), b"\0")


def build_request(out_len: int = REPORT_LEN) -> bytes:
    return build_report(build_message(BATTERY_BODY), out_len)


def _send(h, report: bytes, timeout_ms: int) -> bool:
    n = report[1] if len(report) > 2 else 0
    msg = report[FRAGMENT_OFFSET:FRAGMENT_OFFSET + n]
    if len(report) < REPORT_LEN or report[0] != REPORT_ID_OUT or report[2] != ROUTE_HEADSET \
            or msg != build_message(BATTERY_BODY) or report[FRAGMENT_OFFSET + n:].strip(b"\0"):
        raise ValueError("request is not allowed")
    return h.write(report, timeout_ms=timeout_ms)


def report_fragment(report: bytes | None) -> bytes:
    """Message bytes carried by an input report 0x07, b'' when it is not one / malformed."""
    if not report or len(report) < FRAGMENT_OFFSET or report[0] != REPORT_ID_IN:
        return b""
    n = report[1]
    if n > REPORT_LEN - FRAGMENT_OFFSET or FRAGMENT_OFFSET + n > len(report):
        return b""
    return bytes(report[FRAGMENT_OFFSET:FRAGMENT_OFFSET + n])


def extract_message(stream: bytearray) -> bytes:
    """Pop the next complete message from the stream (b'' when more bytes are needed)."""
    while stream:
        if stream[0] != MARKER:
            del stream[0]
            continue
        if len(stream) < HEADER_LEN:
            break
        size = HEADER_LEN + (stream[2] | (stream[3] << 8))
        if size > MAX_MESSAGE:
            del stream[0]
            continue
        if len(stream) < size:
            break
        msg = bytes(stream[:size])
        del stream[:size]
        return msg
    return b""


def parse_battery(msg: bytes) -> int | str | None:
    """Percentage (int) for a battery result, 'refused' for an error answer, None if unrelated."""
    if len(msg) < HEADER_LEN + 2 or msg[HEADER_LEN:HEADER_LEN + 2] != CMD_BATTERY:
        return None
    if msg[1] == TYPE_RESULT and len(msg) > PERCENT_OFFSET:
        return min(msg[PERCENT_OFFSET], 100)
    if msg[1] == TYPE_RESPONSE and len(msg) > STATUS_OFFSET and msg[STATUS_OFFSET] != STATUS_OK:
        return "refused"
    return None


def _attempt(h, in_len: int, out_len: int, end: float, cancel) -> int | str | None:
    left_ms = int((end - time.monotonic()) * 1000)
    if left_ms <= 0 or not _send(h, build_request(out_len), max(1, min(1000, left_ms))):
        return None
    stream = bytearray()
    last = None
    while time.monotonic() < end:
        if cancel is not None and cancel.is_set():
            return None
        rep = h.get_input_report(REPORT_ID_IN, in_len)
        rep = bytes(rep) if rep else None
        if rep is not None and rep != last:
            last = rep
            stream += report_fragment(rep)
            for msg in iter(lambda: extract_message(stream), b""):
                res = parse_battery(msg)
                if res is not None:
                    return res
        time.sleep(POLL_S)
    return None


def _query(h, in_len: int, out_len: int, deadline: float, cancel) -> int | str | None:
    """Battery percentage, 'refused', or None when the headset did not answer."""
    for _ in range(2):
        if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
            return None
        res = _attempt(h, in_len, out_len, min(deadline, time.monotonic() + ATTEMPT_S), cancel)
        if res is not None:
            return res
    return None


def _device(info, name: str, battery) -> Device:
    return Device(id=f"audeze:{info.pid:04x}", name=name, kind="headset", battery=battery,
                  connected=battery is not None, link="2.4ghz", source="hid")


def _read_one(info, deadline: float, cancel, handle_factory):
    """Device when the headset answered, 'off' when it refused, None when nothing came back."""
    name = sanitize_name(getattr(info, "product", "") or "") or MODELS[info.pid]
    with handle_factory(info.path, in_len=info.in_len) as h:
        res = _query(h, info.in_len, info.out_len, deadline, cancel)
    if res is None:
        return None
    if res == "refused":
        return _device(info, name, None)
    return _device(info, name, int(res))


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every Audeze Maxwell dongle in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == AUDEZE_VID and x.pid in MODELS),
                        key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            if i.pid in done or i.usage_page != VENDOR_PAGE or i.usage != 1 \
                    or i.in_len != REPORT_LEN or i.out_len < REPORT_LEN:
                continue
            try:
                dev = _read_one(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S), cancel,
                                handle_factory)
            except Exception:
                log.debug("audeze read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is None:                      # no answer on this collection; try the others
                continue
            done.add(i.pid)
            out.append(dev)
        return out
    except Exception:
        log.debug("audeze scan failed", exc_info=True)
        return []
