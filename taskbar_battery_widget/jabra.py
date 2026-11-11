"""Direct battery reader for Jabra Link 380 / Link 390 dongles and the Evolve2 65 Flex (USB).

Protocol (Jabra GN protocol over HID report 5, 64 bytes, vendor page 0xFF00): a GET of the
headset battery status; the reply carries a flags byte (bit 0 = charging) and the level in
percent. Only that read-only GET is ever sent (whitelist). The settings commands of the protocol
(sidetone, busy light, voice prompts, ...) are deliberately not implemented.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

JABRA_VID = 0x0B0E
VENDOR_IDS: tuple[int, ...] = (JABRA_VID,)
VENDOR_PAGE = 0xFF00
REPORT_ID = 5
REPORT_LEN = 64
TYPE_GET, TYPE_RESPONSE = 1, 3
CMD_NACK = 0xFE
NACK_OFFLINE = frozenset({0xF3, 0xF4, 0xF5})     # peer offline / no child / no device at address
CMD_STATUS, SUB_BATTERY = 0x12, 0x02
ALLOWED_QUERIES = frozenset({(CMD_STATUS, SUB_BATTERY)})
ADDR_VIA_DONGLE, ADDR_ON_USB = 0x04, 0x08
ALLOWED_ADDRESSES = frozenset({ADDR_VIA_DONGLE, ADDR_ON_USB})
FLAG_CHARGING = 0x01
DEVICE_BUDGET_S = 1.5
READ_SLICE_MS = 250
MAX_SKIPPED_REPORTS = 16


@dataclass(frozen=True)
class Model:
    name: str
    usage: int
    address: int
    link: str


MODELS: dict[int, Model] = {
    0x24C7: Model("Jabra Link 380", 0x01, ADDR_VIA_DONGLE, "2.4ghz"),
    0x2E57: Model("Jabra Link 390", 0x05, ADDR_VIA_DONGLE, "2.4ghz"),
    0x2519: Model("Jabra Evolve2 65 Flex", 0x01, ADDR_ON_USB, "usb"),
}

_seq = 0
_seq_lock = threading.Lock()


def next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq = (_seq + 1) & 0xFF
        return _seq


def build_request(address: int, seq: int, cmd: int, sub: int) -> bytes:
    """64-byte output report for a whitelisted GET."""
    if address not in ALLOWED_ADDRESSES or (cmd, sub) not in ALLOWED_QUERIES:
        raise ValueError(f"query {cmd:#04x}/{sub:#04x} to {address:#04x} is not allowed")
    b = bytearray(REPORT_LEN)
    b[0] = REPORT_ID
    b[1] = address
    b[2] = 0x00                                   # source: host
    b[3] = seq & 0xFF
    b[4] = (TYPE_GET << 6) | 6                    # GET, no payload
    b[5], b[6] = cmd, sub
    return bytes(b)


def parse_reply(buf: bytes | None, seq: int):
    """('ok', percent, charging) | ('off',) | ('rejected',) for the reply to `seq`; else None."""
    if not buf or len(buf) < 7 or buf[0] != REPORT_ID or (buf[4] >> 6) != TYPE_RESPONSE \
            or buf[3] != (seq & 0xFF):
        return None
    if buf[5] == CMD_NACK:
        return ("off",) if buf[6] in NACK_OFFLINE else ("rejected",)
    n = (buf[4] & 0x3F) - 6
    if n < 2 or len(buf) < 7 + n:
        return ("rejected",)
    return "ok", min(100, int(buf[8])), bool(buf[7] & FLAG_CHARGING)


def _query(h, model: Model, deadline: float, cancel: threading.Event | None):
    """Parsed reply tuple, or None when cancelled / write failed / nothing answered (silence is not off)."""
    if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
        return None
    seq = next_seq()
    pkt = build_request(model.address, seq, CMD_STATUS, SUB_BATTERY)
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0 or not h.write(pkt, timeout_ms=max(1, min(1000, remaining_ms))):
        return None
    for _ in range(MAX_SKIPPED_REPORTS):
        if cancel is not None and cancel.is_set():
            return None
        if time.monotonic() >= deadline:
            break
        left = int((deadline - time.monotonic()) * 1000)
        res = parse_reply(h.read(timeout_ms=max(1, min(READ_SLICE_MS, left))), seq)
        if res is not None:
            return res
    return None                                   # no answer: unknown, keep the cached value


def _read_one(info, model: Model, deadline: float, cancel, handle_factory) -> Device | None:
    name = sanitize_name(getattr(info, "product", "") or "") or model.name
    dev_id = f"jabra:{info.pid:04x}"
    with handle_factory(info.path, in_len=info.in_len) as h:
        res = _query(h, model, deadline, cancel)
    if res is None or res[0] == "rejected":
        return None
    if cancel is not None and cancel.is_set():
        return None
    if res[0] == "off":
        return Device(id=dev_id, name=name, kind="headset", battery=None, connected=False,
                      link=model.link, source="hid")
    return Device(id=dev_id, name=name, kind="headset", battery=res[1], connected=True,
                  charging=res[2], link=model.link, source="hid")


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every known Jabra dongle / USB headset in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == JABRA_VID and x.pid in MODELS),
                        key=lambda x: x.path):
            if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
                break
            model = MODELS[i.pid]
            if i.pid in done or i.usage_page != VENDOR_PAGE or i.usage != model.usage \
                    or i.in_len != REPORT_LEN or i.out_len != REPORT_LEN:
                continue
            try:
                dev = _read_one(i, model, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                cancel, handle_factory)
            except Exception:
                log.debug("jabra read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is not None:
                done.add(i.pid)
                out.append(dev)
        return out
    except Exception:
        log.debug("jabra scan failed", exc_info=True)
        return []
