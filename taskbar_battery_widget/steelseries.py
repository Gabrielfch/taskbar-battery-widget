"""Direct battery reader for SteelSeries wireless headsets (USB dongle / base station, HID).

Families (all vendor id 0x1038, vendor-defined collections, query/status requests only):

* nova    - Arctis 7+, Nova 3, Nova 5, Nova 7/7P/7X, GameBuds: request `00 B0` (status report),
            reply starts with `B0`; battery/connection layout differs per family.
* legacy  - Arctis 1/7X/7P, Arctis 7, Arctis 9: one short query, battery at a fixed offset.
* nova_pro - Arctis Nova Pro Wireless base station: request `06 B0`, battery 0..8 at byte 6.
* pro_wireless - Arctis Pro Wireless: two queries (`41 AA` connection, `40 AA` battery 0..4).

The first byte of each request is the HID report id, exactly as written by the reference tools
(0 = unnumbered report). Windows prefixes input reports with their report id, so a leading
0x00 byte is dropped before parsing (unnumbered reports). Requests are zero padded to the
collection's output report length. Only whitelisted queries are ever sent.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

STEELSERIES_VID = 0x1038
VENDOR_IDS = (STEELSERIES_VID,)
DEVICE_BUDGET_S = 1.5
NOVA_STATUS = 0xB0
MAX_REPLIES = 8                     # unrelated input reports tolerated per query

# Read-only requests (command bytes, trailing zero padding implied).
REQ_LEGACY_ARCTIS1 = bytes([0x06, 0x12])
REQ_LEGACY_ARCTIS7 = bytes([0x06, 0x18])
REQ_NOVA_STATUS = bytes([0x00, 0xB0])
REQ_ARCTIS9 = bytes([0x00, 0x20])
REQ_NOVA_PRO = bytes([0x06, 0xB0])
REQ_PRO_CONNECTION = bytes([0x41, 0xAA])
REQ_PRO_BATTERY = bytes([0x40, 0xAA])
ALLOWED_REQUESTS = frozenset({REQ_LEGACY_ARCTIS1, REQ_LEGACY_ARCTIS7, REQ_NOVA_STATUS,
                              REQ_ARCTIS9, REQ_NOVA_PRO, REQ_PRO_CONNECTION, REQ_PRO_BATTERY})

_MI_RE = re.compile(r"[&#]mi_([0-9a-f]{2})", re.IGNORECASE)


@dataclass(frozen=True)
class Reading:
    battery: int | None
    charging: bool = False
    connected: bool = True


def _clamp(v: float) -> int:
    return max(0, min(100, int(round(v))))


def _scale(raw: int, lo: int, hi: int) -> int:
    return _clamp((raw - lo) * 100 / (hi - lo))


# ---- parsers: payload list (one per query) -> Reading | None (None = unusable reply) --------------

def _parse_arctis1(r, pid):
    d = r[0]
    if d[2] == 0x01:                                # offline marker
        return Reading(None, connected=False)
    return Reading(_scale(d[3], 0, 100))


def _parse_arctis7(r, pid):
    return Reading(_scale(r[0][2], 0, 100))


def _parse_arctis9(r, pid):
    d = r[0]
    return Reading(_scale(d[3], 0x64, 0x9A), charging=d[4] == 0x01)


def _parse_nova7plus(r, pid):
    d = r[0]
    if d[1] == 0x01:
        return Reading(None, connected=False)
    return Reading(_scale(d[2], 0, 4), charging=d[3] == 0x01)


def _parse_nova3_5(r, pid):
    d = r[0]
    if d[1] == 0x02:
        return Reading(None, connected=False)
    return Reading(_clamp(d[3]), charging=d[4] == 0x01)


NOVA7_DISCRETE_PIDS = frozenset({0x2202, 0x2206, 0x220A, 0x223A, 0x227A, 0x22A4})


def _parse_nova7(r, pid):
    d = r[0]
    if d[3] == 0x00:
        return Reading(None, connected=False)
    pct = _scale(d[2], 0, 4) if pid in NOVA7_DISCRETE_PIDS else _clamp(d[2])
    return Reading(pct, charging=d[3] in (0x01, 0x02))


def _parse_gamebuds(r, pid):
    d = r[0]
    levels = [min(100, d[b]) for s, b in ((3, 5), (4, 6)) if d[s] == 0x03]
    if not levels:                                  # both buds docked in the case
        return Reading(None, connected=False)
    return Reading(min(levels))


def _parse_nova_pro(r, pid):
    d = r[0]
    if d[15] == 0x01:
        return Reading(None, connected=False)
    return Reading(_scale(d[6], 0, 8), charging=d[15] == 0x02)


def _parse_pro_wireless(r, pid):
    if r[0][0] == 0x02:
        return Reading(None, connected=False)
    if len(r) < 2:
        return None
    return Reading(_scale(r[1][0], 0, 4))


def _pro_wireless_stop(replies) -> bool:
    return bool(replies) and replies[0][0] == 0x02


@dataclass(frozen=True)
class Family:
    key: str
    pids: dict[int, str]                            # pid -> display name
    requests: tuple[bytes, ...]
    min_lens: tuple[int, ...]
    parse: Callable
    usage_page: int = 0                             # 0 = not constrained
    usage: int = 0
    interface: int | None = None                    # USB interface (path "mi_XX") when no usage page
    expect0: int | None = None                      # required first payload byte (status reports)
    stop_early: Callable | None = None


FAMILIES: tuple[Family, ...] = (
    Family("arctis1", {0x12B3: "SteelSeries Arctis 1", 0x12B6: "SteelSeries Arctis 1 Xbox",
                       0x12D7: "SteelSeries Arctis 7X", 0x12D5: "SteelSeries Arctis 7P"},
           (REQ_LEGACY_ARCTIS1,), (4,), _parse_arctis1, usage_page=0xFF43, usage=0x202),
    Family("arctis7", {0x1260: "SteelSeries Arctis 7", 0x12AD: "SteelSeries Arctis 7 (2019)"},
           (REQ_LEGACY_ARCTIS7,), (3,), _parse_arctis7, usage_page=0xFF43, usage=0x202),
    Family("arctis9", {0x12C2: "SteelSeries Arctis 9", 0x12C4: "SteelSeries Arctis 9"},
           (REQ_ARCTIS9,), (5,), _parse_arctis9, interface=0),
    Family("arctis7plus", {0x220E: "SteelSeries Arctis 7+", 0x2212: "SteelSeries Arctis 7+ PlayStation",
                           0x2216: "SteelSeries Arctis 7+ Xbox", 0x2236: "SteelSeries Arctis 7+ Destiny"},
           (REQ_NOVA_STATUS,), (4,), _parse_nova7plus, usage_page=0xFFC0, usage=0x1, expect0=NOVA_STATUS),
    Family("gamebuds", {0x230A: "SteelSeries Arctis GameBuds"},
           (REQ_NOVA_STATUS,), (7,), _parse_gamebuds, usage_page=0xFFC0, usage=0x1, expect0=NOVA_STATUS),
    Family("nova3", {0x2269: "SteelSeries Arctis Nova 3P Wireless",
                     0x226D: "SteelSeries Arctis Nova 3X Wireless"},
           (REQ_NOVA_STATUS,), (5,), _parse_nova3_5, usage_page=0xFFC0, usage=0x1, expect0=NOVA_STATUS),
    Family("nova5", {0x2232: "SteelSeries Arctis Nova 5", 0x2253: "SteelSeries Arctis Nova 5X"},
           (REQ_NOVA_STATUS,), (16,), _parse_nova3_5, usage_page=0xFFC0, usage=0x1, expect0=NOVA_STATUS),
    Family("nova7", {0x2202: "SteelSeries Arctis Nova 7", 0x22A1: "SteelSeries Arctis Nova 7",
                     0x227E: "SteelSeries Arctis Nova 7 Gen 2", 0x2206: "SteelSeries Arctis Nova 7X",
                     0x2258: "SteelSeries Arctis Nova 7X", 0x229E: "SteelSeries Arctis Nova 7X",
                     0x22AD: "SteelSeries Arctis Nova 7X", 0x223A: "SteelSeries Arctis Nova 7 Diablo IV",
                     0x22A9: "SteelSeries Arctis Nova 7 Diablo IV",
                     0x227A: "SteelSeries Arctis Nova 7 WoW", 0x22A4: "SteelSeries Arctis Nova 7X",
                     0x22A5: "SteelSeries Arctis Nova 7X", 0x220A: "SteelSeries Arctis Nova 7P",
                     0x22A7: "SteelSeries Arctis Nova 7P", 0x2298: "SteelSeries Arctis Nova 7P"},
           (REQ_NOVA_STATUS,), (4,), _parse_nova7, usage_page=0xFFC0, usage=0x1, expect0=NOVA_STATUS),
    Family("nova_pro", {0x12E0: "SteelSeries Arctis Nova Pro Wireless",
                        0x12E5: "SteelSeries Arctis Nova Pro Wireless X"},
           (REQ_NOVA_PRO,), (16,), _parse_nova_pro, interface=4),
    Family("pro_wireless", {0x1290: "SteelSeries Arctis Pro Wireless"},
           (REQ_PRO_CONNECTION, REQ_PRO_BATTERY), (1, 1), _parse_pro_wireless, interface=0,
           stop_early=_pro_wireless_stop),
)

# name, kind, pids table for callers/docs
MODELS: dict[int, tuple[str, str]] = {pid: (name, "headset") for f in FAMILIES
                                      for pid, name in f.pids.items()}
_BY_PID: dict[int, Family] = {pid: f for f in FAMILIES for pid in f.pids}


def build_request(cmd: bytes, out_len: int) -> bytes:
    """Zero padded output report for a whitelisted query."""
    cmd = bytes(cmd)
    if cmd not in ALLOWED_REQUESTS or out_len < len(cmd):
        raise ValueError(f"request {cmd.hex() or '<empty>'} is not allowed")
    return cmd + bytes(out_len - len(cmd))


def payload(buf: bytes | None) -> bytes | None:
    """Input report as the reference tools see it: an unnumbered report's leading 0x00 is dropped."""
    if not buf:
        return None
    buf = bytes(buf)
    return buf[1:] if buf[0] == 0 else buf


def _send(h, req: bytes, timeout_ms: int) -> bool:
    if bytes(req).rstrip(b"\0") not in ALLOWED_REQUESTS:
        raise ValueError("request is not allowed")
    return h.write(bytes(req), timeout_ms=timeout_ms)


def _stopped(deadline: float, cancel) -> bool:
    return (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline


def _query(h, family: Family, cmd: bytes, min_len: int, out_len: int, deadline: float,
           cancel) -> bytes | None:
    if _stopped(deadline, cancel):
        return None
    h.drain()
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0 or not _send(h, build_request(cmd, out_len), max(1, min(1000, remaining_ms))):
        return None
    unrelated = 0
    while not _stopped(deadline, cancel):
        left = int((deadline - time.monotonic()) * 1000)
        data = payload(h.read(timeout_ms=max(1, min(250, left))))     # last read capped to the budget
        if data is None:
            continue
        if len(data) >= min_len and (family.expect0 is None or data[0] == family.expect0):
            return data
        unrelated += 1
        if unrelated >= MAX_REPLIES:
            return None
    return None


def _device(info, family: Family, reading: Reading) -> Device:
    name = sanitize_name(getattr(info, "product", "") or "") or family.pids[info.pid]
    dev_id = f"steelseries:{info.pid:04x}"
    if not reading.connected:                       # explicit "headset off" report
        return Device(id=dev_id, name=name, kind="headset", battery=None, connected=False,
                      link="2.4ghz", source="hid")
    return Device(id=dev_id, name=name, kind="headset", battery=reading.battery, connected=True,
                  charging=reading.charging, link="2.4ghz", source="hid")


def _read_one(info, family: Family, deadline: float, cancel, handle_factory) -> Device | None:
    replies: list[bytes] = []
    with handle_factory(info.path, in_len=info.in_len) as h:
        for cmd, min_len in zip(family.requests, family.min_lens):
            data = _query(h, family, cmd, min_len, info.out_len, deadline, cancel)
            if data is None:
                break
            replies.append(data)
            if family.stop_early is not None and family.stop_early(replies):
                break
    reading = None
    if len(replies) == len(family.requests) or (family.stop_early and replies
                                                 and family.stop_early(replies)):
        reading = family.parse(replies, info.pid)
    if reading is None:
        return None                                 # silent / unparsable: keep the cached value
    return _device(info, family, reading)


def _matches(info, family: Family) -> bool:
    if info.in_len <= 0 or info.out_len <= 0:
        return False
    if family.usage_page:
        return info.usage_page == family.usage_page and info.usage == family.usage
    if info.usage_page < 0xFF00:                    # vendor-defined collections only
        return False
    m = _MI_RE.search(info.path or "")
    return m is not None and int(m.group(1), 16) == family.interface


def read(infos, deadline: float, cancel: threading.Event | None = None,
         handle_factory=None) -> list[Device]:
    """Read every SteelSeries wireless headset dongle in `infos`. Never raises."""
    try:
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        out: list[Device] = []
        done: set[int] = set()
        for i in sorted((x for x in infos if x.vid == STEELSERIES_VID and x.pid in _BY_PID),
                        key=lambda x: x.path):
            if _stopped(deadline, cancel):
                break
            family = _BY_PID[i.pid]
            if i.pid in done or not _matches(i, family):
                continue
            try:
                dev = _read_one(i, family, min(deadline, time.monotonic() + DEVICE_BUDGET_S),
                                cancel, handle_factory)
            except Exception:
                log.debug("steelseries read failed for %04x", i.pid, exc_info=True)
                continue
            if dev is None:
                continue
            done.add(i.pid)
            out.append(dev)
        return out
    except Exception:
        log.debug("steelseries scan failed", exc_info=True)
        return []
