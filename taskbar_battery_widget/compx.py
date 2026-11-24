"""Battery reader for keyboards on a Compx 2.4 GHz receiver (``3554:FA09``).

The receiver ships with the AULA F75 (and other keyboards built on the same Compx radio) and
enumerates as product ``"2.4G Wireless Receiver"``. Its control collection is vendor usage page
``0xFF02``, usage ``0x0002``, 20-byte input/output reports, report id ``0x13`` only.

The keyboard cannot be asked for its battery level through this receiver (the receiver rejects
every report id but ``0x13``, which also carries lighting commands, so nothing is ever written).
Instead it *pushes* a status report whenever its power state changes: when its level changes and when
the charging cable is plugged in or out::

    13 0A 01 00 04 05 <pct> <flags> 00 .. 00 <sum>

``pct`` (index 6) is the level 1..100 and ``sum`` (index 19) is the 8-bit sum of bytes 0..18. The
meaning of ``flags`` is not known yet (0x11 / 0x10 / 0x01 were seen), so charging is not reported.

It also reports sleep and wake: ``13 0A 01 00 04 02 <state> 00 .. <sum>`` with state 0 when it
falls asleep or leaves 2.4 GHz mode and 1 when it wakes or returns to 2.4 GHz (returning is followed by
a power report). The two "off" cases cannot be told apart, so both show the keyboard as disconnected with its
last value; it counts as connected again on the next wake or level report.

Because the reports are rare, one background thread per receiver keeps the collection open and
remembers the last level; ``read()`` only reports it and never touches the device. The keyboard also
pushes whenever its level changes by a percent. After a restart the last level is taken from the
app's last-known cache (``last_known.json``, written by ``device_history``; read-only here) for up
to 7 days until the keyboard pushes a fresh one; plugging the cable in or out forces a fresh push.

Format learned from a read-only capture on a real AULA F75 receiver; independent implementation.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .hid_win import _is_bluetooth_path, path_hash
from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

COMPX_VID = 0x3554
KEYBOARD_RECEIVER_PIDS = frozenset({0xFA09})
VENDOR_IDS: tuple[int, ...] = (COMPX_VID,)

CONTROL_USAGE_PAGE = 0xFF02
CONTROL_USAGE = 0x0002
REPORT_LEN = 20             # Win32 report length, report id included
REPORT_ID = 0x13
POWER_HEADER = bytes((0x13, 0x0A, 0x01, 0x00, 0x04, 0x05))
STATE_HEADER = bytes((0x13, 0x0A, 0x01, 0x00, 0x04, 0x02))

DEFAULT_NAME = "2.4 GHz keyboard"
REMEMBER_S = 7 * 86400.0    # a level saved by an earlier run is shown for at most this long
# The keyboard sends the same report when it falls asleep and when it leaves 2.4 GHz mode, so
# both are shown as disconnected (last value, dimmed) until it wakes or pushes a level again.
ASLEEP_AS_DISCONNECTED = True
LISTEN_POLL_MS = 1000       # how often a listener checks its stop flag
RETRY_DELAY_S = 5.0         # pause before a failed listener may be started again


def _frame(buf: bytes | None, header: bytes) -> bytes | None:
    if not buf or len(buf) < REPORT_LEN:
        return None
    b = bytes(buf[:REPORT_LEN])
    if b[:len(header)] != header or sum(b[:REPORT_LEN - 1]) & 0xFF != b[REPORT_LEN - 1]:
        return None
    return b


def parse_state_report(buf: bytes | None) -> bool | None:
    """True (awake) / False (asleep or off the receiver) from a ``13 0A 01 00 04 02`` report."""
    b = _frame(buf, STATE_HEADER)
    if b is None or b[6] not in (0, 1) or any(b[7:REPORT_LEN - 1]):
        return None
    return b[6] == 1


def parse_power_report(buf: bytes | None) -> int | None:
    """Battery percent from a pushed ``13 0A 01 00 04 05`` report, else None."""
    b = _frame(buf, POWER_HEADER)
    if b is None:
        return None
    pct = b[6]
    if not 1 <= pct <= 100:
        return None
    return int(pct)


def _matches(info) -> bool:
    if getattr(info, "vid", 0) != COMPX_VID or getattr(info, "pid", 0) not in KEYBOARD_RECEIVER_PIDS:
        return False
    if _is_bluetooth_path(getattr(info, "path", "") or ""):
        return False
    if getattr(info, "usage_page", 0) != CONTROL_USAGE_PAGE or getattr(info, "usage", 0) != CONTROL_USAGE:
        return False
    return getattr(info, "in_len", 0) == REPORT_LEN and getattr(info, "out_len", 0) == REPORT_LEN


def _display_name(product: str) -> str:
    clean = sanitize_name(product or "")
    low = clean.lower()
    if not clean or "dongle" in low or "receiver" in low:
        return DEFAULT_NAME
    return clean


class _ListenOnly:
    """Wrapper that can only read: this reader never sends anything to the receiver."""

    def __init__(self, h):
        self._h = h

    def read(self, timeout_ms: int = LISTEN_POLL_MS) -> bytes | None:
        return self._h.read(timeout_ms=timeout_ms)

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        raise ValueError("write is not allowed")


class _Listener:
    """Keeps one receiver collection open and remembers the last pushed level."""

    def __init__(self, path: str, handle_factory, key: str = ""):
        self.path = path
        self.key = key
        self._factory = handle_factory
        self._stop = threading.Event()
        self.opened = False                   # the collection could be opened for reading
        self.pct: int | None = None
        self.awake: bool | None = None        # None until the keyboard reports sleep / wake
        self.updated: float | None = None     # epoch seconds of the last push
        self.failed_at: float | None = None   # monotonic time the listener died
        self._thread = threading.Thread(target=self._run, name="compx-listener", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            with self._factory(self.path, in_len=REPORT_LEN) as raw:
                h = _ListenOnly(raw)
                self.opened = True
                while not self._stop.is_set():
                    r = h.read(timeout_ms=LISTEN_POLL_MS)
                    if r is None:
                        continue
                    awake = parse_state_report(r)
                    if awake is not None:
                        if awake != self.awake:
                            self.awake = awake
                            _notify()
                        continue
                    pct = parse_power_report(r)
                    if pct is not None:
                        changed = pct != self.pct or self.awake is not True
                        self.pct, self.updated, self.awake = pct, time.time(), True
                        if changed:
                            _notify()
        except Exception:
            log.debug("compx listener stopped", exc_info=True)
        finally:
            self.failed_at = time.monotonic()


_listeners: dict[str, _Listener] = {}
_listeners_lock = threading.Lock()
_callbacks: list = []                   # called (from a listener thread) when a level changes


def add_change_callback(cb) -> None:
    """Call `cb()` whenever a keyboard pushes a new level (so the UI can rescan right away)."""
    with _listeners_lock:
        if cb not in _callbacks:
            _callbacks.append(cb)


def remove_change_callback(cb) -> None:
    with _listeners_lock:
        if cb in _callbacks:
            _callbacks.remove(cb)


def _notify() -> None:
    with _listeners_lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb()
        except Exception:
            log.debug("compx change callback failed", exc_info=True)


LAST_KNOWN_PATH: Path | None = None  # tests override; default is device_history's last_known.json


def _recall(dev_id: str) -> tuple[int, float] | None:
    """Last level of `dev_id` saved by an earlier run in the app's last-known cache (read-only)."""
    try:
        from .device_history import MAX_FILE_BYTES, _valid_entry, last_known_path
        from .models import visibility_key
        path = Path(LAST_KNOWN_PATH) if LAST_KNOWN_PATH is not None else last_known_path()
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        devices = raw.get("devices") if isinstance(raw, dict) else None
        if not isinstance(devices, dict):
            return None
        ent = _valid_entry(devices.get(visibility_key(dev_id)))
        if not ent or not ent["battery"] or ent["ts"] is None:
            return None
        if 0 <= time.time() - ent["ts"] <= REMEMBER_S:
            return int(ent["battery"]), float(ent["ts"])
    except Exception:
        pass
    return None


def _listener_for(path: str, handle_factory, key: str = "") -> _Listener | None:
    """Running listener for `path`, starting one if needed (not right after a failure)."""
    with _listeners_lock:
        cur = _listeners.get(path)
        if cur is not None and cur.alive():
            return cur
        if cur is not None and cur.failed_at is not None \
                and time.monotonic() - cur.failed_at < RETRY_DELAY_S:
            return cur
        new = _Listener(path, handle_factory, key)
        if cur is not None:             # a restarted listener keeps the last known level
            new.pct, new.updated = cur.pct, cur.updated
        else:                           # first start: the level saved by an earlier run
            old = _recall(key)
            if old is not None:
                new.pct, new.updated = old
        _listeners[path] = new
        new.start()
        return new


def _forget_missing(present: set[str]) -> None:
    with _listeners_lock:
        for path in [p for p in _listeners if p not in present]:
            _listeners.pop(path).stop()


def stop_all() -> None:
    """Stop every listener (tests, shutdown)."""
    _forget_missing(set())


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Report every Compx keyboard receiver in ``infos``. Never raises, never does device I/O here."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        mine = sorted((i for i in (infos or ()) if _matches(i)), key=lambda x: x.path)
        _forget_missing({i.path for i in mine})
        done: set[str] = set()
        rank: dict[str, int] = {}
        out: list[Device] = []
        for i in mine:
            if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
                break
            phash = path_hash(i.path)
            if phash in done:
                continue
            done.add(phash)
            base_id = f"compx:{i.pid:04x}"
            nth = rank.get(base_id, 0)
            rank[base_id] = nth + 1
            dev_id = f"{base_id}:{phash}" if nth else base_id
            lst = _listener_for(i.path, handle_factory, dev_id)
            if lst is None or not (lst.opened or lst.pct is not None):
                continue                # not open yet (next scan) or the collection is unusable
            pct = lst.pct
            dev = Device(
                id=dev_id,
                name=_display_name(getattr(i, "product", "") or ""),
                kind="keyboard",
                battery=pct,
                charging=False,
                connected=lst.awake is not False or not ASLEEP_AS_DISCONNECTED,
                link="2.4ghz",
                source="hid",
                updated=lst.updated if pct is not None else None,
                # Until the keyboard says otherwise it is taken as awake, so the saved level
                # shows as live right after start-up instead of waiting for the next push.
                idle=pct is not None and lst.awake is False and not ASLEEP_AS_DISCONNECTED,
            )
            out.append(dev)
        return out
    except Exception:
        log.debug("compx scan failed", exc_info=True)
        return []
