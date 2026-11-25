"""EXPERIMENTAL (beta) battery reader for Areson 2.4 GHz mouse receivers ``25A7:FA07`` / ``25A7:FA08``.

Seen on Redragon M913 / M915RGB-WL and Rytaki Gamspeed V8 Pro (issue #9). The protocol uses 17-byte
frames: ``[0]`` report id, ``[1]`` command, ``[2]`` status (0 = ok), ``[3..4]`` address, ``[5]`` data
length, ``[6..15]`` data, ``[16]`` checksum chosen so that all 17 bytes add up to ``0x55`` (mod 256).

"Feature" transport (FA07 / FA08), all on ONE USB interface (same ``mi_XX``):

* request: ``HidD_SetFeature`` on the collection with usage page ``0xFF02`` (feature length >= 17),
  buffer = the GetPower frame ``08 04 00 ... 49`` zero-padded to that collection's feature length;
* reply: an input report on the collection with usage page ``0xFF01`` (input length >= 17), skipping
  unrelated reports (DPI buttons, command ``0A``...). ``b[1] == 0x04``, status 0, valid checksum,
  ``b[5] >= 1``; level = ``b[6] * 10`` (tens, 0..10); charging = ``b[5] >= 2 and b[7] != 0``.
  Level 0 without charging means "no reading" (mouse asleep / off).

With the cable plugged in, a dual-mode mouse (Gamspeed V8 Pro) also shows up on its own as ``FA08``
while its ``FA07`` dongle keeps answering: both report the same mouse (issue #9). When exactly one
``FA07`` and one ``FA08`` answer, they are shown as one entry (see ``_merge_dongle_and_cable``).

The only frame that can ever be sent is the pure query GetPower (command ``04``); no link command.
Protocol facts come from the MIT-licensed qu1ncky/BatteryTray; this is an independent implementation.
"""
from __future__ import annotations

import logging
import threading
import time

from .hid_win import path_hash
from .hidpp import group_key
from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

ARESON_VID = 0x25A7
PIDS: tuple[int, ...] = (0xFA07, 0xFA08)
VENDOR_IDS: tuple[int, ...] = (ARESON_VID,)

FRAME_LEN = 17
CMD_GET_POWER = 0x04
CHECKSUM_TARGET = 0x55
SEND_USAGE_PAGE = 0xFF02
RECV_USAGE_PAGE = 0xFF01

DONGLE_PID = 0xFA07
CABLE_PID = 0xFA08

DEFAULT_NAME = "2.4 GHz mouse"
DEVICE_BUDGET_S = 1.2
REPLY_WAIT_S = 0.8
MAX_SKIPPED_REPORTS = 32


def checksum(frame16: bytes) -> int:
    """Byte that makes the 17 frame bytes sum to 0x55 (mod 256)."""
    return (CHECKSUM_TARGET - sum(frame16)) & 0xFF


def build_get_power() -> bytes:
    b = bytearray(FRAME_LEN)
    b[0] = 0x08
    b[1] = CMD_GET_POWER
    b[FRAME_LEN - 1] = checksum(bytes(b[:FRAME_LEN - 1]))
    return bytes(b)


GET_POWER = build_get_power()


def _padded(feat_len: int) -> bytes:
    return GET_POWER + bytes(max(FRAME_LEN, int(feat_len)) - FRAME_LEN)


def parse_reply(buf: bytes | None) -> tuple[str, int | None, bool] | None:
    """``('ok', pct, charging)`` | ``('unknown', None, False)`` (level 0, not charging) | ``None``."""
    if not buf or len(buf) < FRAME_LEN:
        return None
    b = bytes(buf[:FRAME_LEN])
    if b[1] != CMD_GET_POWER or b[2] != 0 or b[5] < 1:
        return None
    if checksum(b[:FRAME_LEN - 1]) != b[FRAME_LEN - 1]:
        return None
    pct = b[6] * 10
    if pct > 100:
        return None
    charging = b[5] >= 2 and b[7] != 0
    if pct == 0 and not charging:
        return "unknown", None, False
    return "ok", pct, charging


def _is_mine(info) -> bool:
    return getattr(info, "vid", 0) == ARESON_VID and getattr(info, "pid", 0) in PIDS


class _FeatureOnly:
    """Whitelist wrapper: ``set_feature`` accepts exactly the padded GetPower frame; nothing else."""

    def __init__(self, h, feat_len: int):
        self._h = h
        self._allowed = _padded(feat_len)

    def set_feature(self, data: bytes) -> bool:
        if bytes(data) != self._allowed:
            raise ValueError("feature write is not allowed")
        return bool(self._h.set_feature(self._allowed))

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        raise ValueError("output reports are not allowed")

    def get_feature(self, *a, **k):
        raise ValueError("feature reads are not allowed")


class _Reader:
    """Input side: only reads and drains."""

    def __init__(self, h):
        self._h = h

    def drain(self) -> None:
        self._h.drain()

    def read(self, timeout_ms: int) -> bytes | None:
        return self._h.read(timeout_ms=timeout_ms)

    def write(self, data: bytes, timeout_ms: int = 500) -> bool:
        raise ValueError("output reports are not allowed")

    def set_feature(self, data: bytes) -> bool:
        raise ValueError("feature writes are not allowed")


def _expired(deadline: float, cancel: threading.Event | None) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


_logged: set[str] = set()


def _log_first(phash: str, raw: bytes | None) -> None:
    if phash in _logged:
        return
    _logged.add(phash)
    if raw is None:
        log.info("areson: no GetPower reply from receiver %s", phash)
    else:
        log.info("areson: first GetPower reply from receiver %s: %s", phash, bytes(raw).hex(" ").upper())


def _display_name(product: str) -> str:
    clean = sanitize_name(product or "")
    low = clean.lower()
    if not clean or any(w in low for w in ("receiver", "dongle", "wireless")):
        return DEFAULT_NAME
    return clean


def _query(send_info, recv_info, deadline: float, cancel, handle_factory):
    """Returns (first raw report or None, parsed or None)."""
    with handle_factory(recv_info.path, in_len=recv_info.in_len) as rraw:
        rd = _Reader(rraw)
        with handle_factory(send_info.path, in_len=0, feature_only=True) as sraw:
            wr = _FeatureOnly(sraw, send_info.feat_len)
            if _expired(deadline, cancel):
                return None, None
            rd.drain()
            if not wr.set_feature(_padded(send_info.feat_len)):
                return None, None
            until = min(deadline, time.monotonic() + REPLY_WAIT_S)
            first: bytes | None = None
            for _ in range(MAX_SKIPPED_REPORTS):
                if _expired(deadline, cancel):
                    break
                wait_ms = int((until - time.monotonic()) * 1000)
                if wait_ms <= 0:
                    break
                r = rd.read(max(1, min(250, wait_ms)))
                if r is None:
                    continue
                parsed = parse_reply(r)
                if parsed is not None:
                    return bytes(r), parsed
                if first is None:
                    first = bytes(r)
            return first, None


def _pairs(mine):
    """(feature collection FF02, input collection FF01) pairs on the same USB interface."""
    groups: dict[tuple, dict] = {}
    for i in mine:
        g = groups.setdefault(group_key(i), {"send": [], "recv": []})
        if i.usage_page == SEND_USAGE_PAGE and i.feat_len >= FRAME_LEN:
            g["send"].append(i)
        elif i.usage_page == RECV_USAGE_PAGE and i.in_len >= FRAME_LEN:
            g["recv"].append(i)
    out = []
    for key in sorted(groups, key=str):
        g = groups[key]
        if g["send"] and g["recv"]:
            out.append((min(g["send"], key=lambda x: x.path), min(g["recv"], key=lambda x: x.path)))
    return out


_merge_logged = False


def _merge_dongle_and_cable(out: list[Device]) -> list[Device]:
    """One ``FA07`` dongle + one ``FA08`` cable entry = the same mouse read twice: keep one entry.

    Only when there is exactly one of each (no ``:hash`` suffix entries): with two Areson mice it is
    impossible to tell which cable belongs to which dongle, so nothing is merged then.
    The dongle entry keeps its id / name (alias, icon and history); the level comes from the cable
    reading (direct from the mouse), and it is charging if either path says so.
    """
    global _merge_logged
    dongle = [d for d in out if d.id.startswith(f"areson:{DONGLE_PID:04x}")]
    cable = [d for d in out if d.id.startswith(f"areson:{CABLE_PID:04x}")]
    if len(dongle) != 1 or len(cable) != 1:
        return out
    d, c = dongle[0], cable[0]
    d.battery = c.battery if c.battery is not None else d.battery
    d.charging = bool(d.charging or c.charging)
    if not _merge_logged:
        _merge_logged = True
        log.info("areson: dongle and cable report the same mouse, shown as one entry")
    return [x for x in out if x is not c]


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every Areson receiver in ``infos`` (beta). Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        mine = [i for i in (infos or ()) if _is_mine(i)]
        done: set[str] = set()
        rank: dict[str, int] = {}
        out: list[Device] = []
        for s, r in _pairs(mine):
            if _expired(deadline, cancel):
                break
            phash = path_hash(r.path)
            if phash in done:
                continue
            try:
                raw, parsed = _query(s, r, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                     cancel, handle_factory)
            except Exception as e:
                log.debug("areson read failed for %04x:%04x", r.vid, r.pid, exc_info=True)
                if phash not in _logged:        # once: e.g. Windows refused to open a collection
                    _logged.add(phash)
                    log.info("areson: receiver %s could not be read: %s", phash, e)
                continue
            _log_first(phash, raw)
            if parsed is None or parsed[0] != "ok":
                continue
            done.add(phash)
            dev = Device(
                id=f"areson:{r.pid:04x}",
                name=_display_name(getattr(r, "product", "") or getattr(s, "product", "") or ""),
                kind="mouse",
                battery=parsed[1],
                charging=parsed[2],
                connected=True,
                link="2.4ghz",
                source="hid",
            )
            nth = rank.get(dev.id, 0)
            rank[dev.id] = nth + 1
            if nth:
                dev.id = f"{dev.id}:{phash}"
            out.append(dev)
        return _merge_dongle_and_cable(out)
    except Exception:
        log.debug("areson scan failed", exc_info=True)
        return []
