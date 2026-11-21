"""Direct battery reader for the AULA F108 Pro wireless keyboard 2.4 GHz dongle.

The F108 Pro 2.4 GHz receiver enumerates with USB IDs ``05AC:024F`` (shared with Apple's
Aluminium Keyboard and several unrelated clones) and product string ``"F108Pro Dongle"``.
Interface 3 exposes vendor usage page ``0xFF60``, usage ``0x0061`` with 32-byte input/output
reports prefixed by report ID ``0x00`` (33 bytes on Win32):

* Request: ``00 20 01 00 ... 21`` (order ``0x20`` battery, length ``1``, chunk ``0``, 8-bit sum
  of bytes ``1..31`` at index ``32``).
* Reply: ``00 20 01 00 <pct> 00 ... <sum8>``, echoing ``20 01 00`` with ``pct`` in ``1..100``
  (``0`` means unknown / keyboard asleep, which yields no fresh reading so the cache keeps the
  last known percentage).

Because ``05AC:024F`` and ``0xFF60:0x0061`` are reused by other keyboards (e.g. QMK Raw HID),
this reader strictly checks the USB product string (``F108Pro``) before opening the interface.

Protocol facts come from MIT-licensed public documentation (``parsiya/f108-pro``,
``deddroid/ak820-rs`` and ``gha-projects/ak820-mac``) and live hardware verification; this is
an independent implementation and only the whitelisted ``0x20`` battery query can ever be sent.
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

AULA_DONGLE_VID = 0x05AC
AULA_F108PRO_PID = 0x024F
VENDOR_IDS: tuple[int, ...] = (AULA_DONGLE_VID,)

RF_USAGE_PAGE = 0xFF60
RF_USAGE = 0x0061
RF_REPORT_LEN = 33          # report id 0x00 + 32-byte RF frame
REPORT_ID = 0x00

ORDER_BATTERY = 0x20
ALLOWED_ORDERS = frozenset({ORDER_BATTERY})

DEFAULT_NAME = "AULA F108 Pro"
DEVICE_BUDGET_S = 1.2
ATTEMPT_WAIT_MS = 350
MAX_ATTEMPTS = 3
MAX_SKIPPED_REPORTS = 8

_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def checksum(payload31: bytes) -> int:
    """8-bit modular sum of the 31 frame bytes preceding the checksum byte."""
    return sum(payload31) & 0xFF


def build_request(order: int = ORDER_BATTERY) -> bytes:
    """33-byte Win32 output report (report id 0 + 32-byte RF battery query)."""
    if order not in ALLOWED_ORDERS:
        raise ValueError(f"order 0x{order:02x} is not allowed")
    b = bytearray(RF_REPORT_LEN)
    b[0] = REPORT_ID
    b[1] = order
    b[2] = 0x01
    b[3] = 0x00
    b[RF_REPORT_LEN - 1] = checksum(b[1:RF_REPORT_LEN - 1])
    return bytes(b)


_ALLOWED_PACKETS = frozenset({build_request(o) for o in ALLOWED_ORDERS})


def parse_battery_reply(buf: bytes | None) -> tuple[str, int | None] | None:
    """``('ok', 1..100)`` | ``('unknown', None)`` when byte 4 is 0 | ``None`` if unrelated/bad."""
    if not buf or len(buf) != RF_REPORT_LEN or buf[0] != REPORT_ID:
        return None
    if buf[1] != ORDER_BATTERY or buf[2] != 0x01 or buf[3] != 0x00:
        return None
    if checksum(buf[1:RF_REPORT_LEN - 1]) != buf[RF_REPORT_LEN - 1]:
        return None
    pct = buf[4]
    if pct == 0:
        return "unknown", None
    if pct > 100:
        return None
    return "ok", int(pct)


def _is_f108pro_product(product: str) -> bool:
    norm = _ALNUM_RE.sub("", (product or "").lower())
    return "f108pro" in norm


def _matches(info) -> bool:
    if getattr(info, "vid", 0) != AULA_DONGLE_VID or getattr(info, "pid", 0) != AULA_F108PRO_PID:
        return False
    if _is_bluetooth_path(getattr(info, "path", "") or ""):
        return False
    if getattr(info, "usage_page", 0) != RF_USAGE_PAGE or getattr(info, "usage", 0) != RF_USAGE:
        return False
    if getattr(info, "in_len", 0) != RF_REPORT_LEN or getattr(info, "out_len", 0) != RF_REPORT_LEN:
        return False
    return _is_f108pro_product(getattr(info, "product", "") or "")


def _display_name(product: str) -> str:
    clean = sanitize_name(product or "")
    low = clean.lower()
    if not clean or "dongle" in low or "receiver" in low:
        return DEFAULT_NAME
    return clean


class _ReadOnly:
    """Whitelist wrapper: only the 33-byte ``0x20`` battery query can ever be written."""

    def __init__(self, h):
        self._h = h

    def drain(self) -> None:
        self._h.drain()

    def read(self, timeout_ms: int = ATTEMPT_WAIT_MS) -> bytes | None:
        return self._h.read(timeout_ms=timeout_ms)

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        raw = bytes(data)
        if raw not in _ALLOWED_PACKETS:
            raise ValueError("write is not allowed")
        return bool(self._h.write(raw, timeout_ms=timeout_ms))


def _expired(deadline: float, cancel: threading.Event | None) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


def _query_once(h: _ReadOnly, deadline: float,
                cancel: threading.Event | None) -> tuple[str, int | None] | None:
    if _expired(deadline, cancel):
        return None
    req = build_request(ORDER_BATTERY)
    h.drain()
    left_ms = int((deadline - time.monotonic()) * 1000)
    if left_ms <= 0 or not h.write(req, timeout_ms=max(1, min(500, left_ms))):
        return None
    until = min(deadline, time.monotonic() + ATTEMPT_WAIT_MS / 1000.0)
    for _ in range(MAX_SKIPPED_REPORTS):
        if _expired(deadline, cancel):
            return None
        wait_ms = int((until - time.monotonic()) * 1000)
        if wait_ms <= 0:
            return None
        r = h.read(timeout_ms=max(1, min(ATTEMPT_WAIT_MS, wait_ms)))
        if r is None:
            return None
        parsed = parse_battery_reply(r)
        if parsed is not None:
            return parsed
    return None


def _read_one(info, deadline: float, cancel: threading.Event | None,
              handle_factory) -> tuple[str, Device] | None:
    phash = path_hash(info.path)
    with handle_factory(info.path, in_len=RF_REPORT_LEN) as raw:
        h = _ReadOnly(raw)
        pct: int | None = None
        for _ in range(MAX_ATTEMPTS):
            if _expired(deadline, cancel):
                break
            res = _query_once(h, deadline, cancel)
            if res is not None and res[0] == "ok":
                pct = res[1]
                break
            if res is not None and res[0] == "unknown":
                # Receiver answered 0 % ("unknown" while keyboard is asleep): do not spam retries
                break
    if pct is None:
        return None
    dev = Device(
        id=f"aula:{info.pid:04x}",
        name=_display_name(getattr(info, "product", "") or ""),
        kind="keyboard",
        battery=pct,
        charging=False,
        connected=True,
        link="2.4ghz",
        source="hid",
    )
    return phash, dev


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every supported AULA 2.4 GHz dongle in ``infos``. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        mine = [i for i in (infos or ()) if _matches(i)]
        done: set[str] = set()
        rank: dict[str, int] = {}
        out: list[Device] = []
        for i in sorted(mine, key=lambda x: x.path):
            if _expired(deadline, cancel):
                break
            phash = path_hash(i.path)
            if phash in done:
                continue
            try:
                res = _read_one(i, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                cancel, handle_factory)
            except Exception:
                log.debug("aula read failed for %04x:%04x", i.vid, i.pid, exc_info=True)
                continue
            if res is None:
                continue
            done.add(phash)
            _, dev = res
            nth = rank.get(dev.id, 0)
            rank[dev.id] = nth + 1
            if nth:
                dev.id = f"{dev.id}:{phash}"
            out.append(dev)
        return out
    except Exception:
        log.debug("aula scan failed", exc_info=True)
        return []
