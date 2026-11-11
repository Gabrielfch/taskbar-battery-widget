"""Razer wireless battery reader (standard 90-byte feature-report protocol).

Pure packet build / crc / decode helpers (unit tested) plus `scan_razer`, which only sends
read queries (get battery level, get charging status).

Known limitation: some newer headsets (e.g. BlackShark V2 HyperSpeed, 1532:0565) do not
implement this protocol (their vendor collections use 63-byte reports with a different
framing), so they are skipped.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass

from .models import Device
from .naming import sanitize_name
from .razer_odessa import ODESSA_DONGLES

log = logging.getLogger(__name__)

RAZER_VID = 0x1532
REPORT_LEN = 90
FEATURE_BUF_LEN = REPORT_LEN + 1        # + report id 0
CLASS_POWER = 0x07
CMD_GET_BATTERY = 0x80
CMD_GET_CHARGING = 0x84
TRANSACTION_IDS = (0x1F, 0x3F, 0xFF, 0x9F)
STATUS_SUCCESS = 0x02
SET_GET_DELAY_S = 0.05
RETRIES = 2
VENDOR_PAGE_MIN = 0xFF00
SCAN_BUDGET_S = 2.5
# Product strings that carry no device identity; a table name replaces them.
GENERIC_PRODUCTS = frozenset({"", "razer", "razer device", "razer hid", "hid", "hid device",
                              "usb input device", "human interface device"})


@dataclass(frozen=True)
class KnownDevice:
    name: str
    txn: int           # transaction id that this device answers to, tried first


# Protocol facts from public documentation (openrazer); independent implementation. PID -> name and exact transaction id.
KNOWN_DEVICES: dict[int, KnownDevice] = {
    0x00A4: KnownDevice("Razer Mouse Dock Pro", 0x1F),
    0x00AA: KnownDevice("Razer Basilisk V3 Pro Wired", 0x1F),
    0x00AB: KnownDevice("Razer Basilisk V3 Pro Wireless", 0x1F),
    0x00B9: KnownDevice("Razer Basilisk V3 X HyperSpeed", 0x1F),
    0x007C: KnownDevice("Razer DeathAdder V2 Pro Wired", 0x3F),
    0x007D: KnownDevice("Razer DeathAdder V2 Pro Wireless", 0x3F),
    0x009C: KnownDevice("Razer DeathAdder V2 X HyperSpeed", 0x1F),
    0x00B3: KnownDevice("Razer HyperPolling Wireless Dongle", 0x1F),
    0x00B6: KnownDevice("Razer DeathAdder V3 Pro Wired", 0x1F),
    0x00B7: KnownDevice("Razer DeathAdder V3 Pro Wireless", 0x1F),
    0x0083: KnownDevice("Razer Basilisk X HyperSpeed", 0x1F),
    0x0086: KnownDevice("Razer Basilisk Ultimate", 0x1F),
    0x0088: KnownDevice("Razer Basilisk Ultimate Dongle", 0x1F),
    0x008F: KnownDevice("Razer Naga V2 Pro Wired", 0x1F),
    0x0090: KnownDevice("Razer Naga V2 Pro Wireless", 0x1F),
    0x00A5: KnownDevice("Razer Viper V2 Pro Wired", 0x1F),
    0x00A6: KnownDevice("Razer Viper V2 Pro Wireless", 0x1F),
    0x007B: KnownDevice("Razer Viper Ultimate Wired", 0x3F),
    0x0078: KnownDevice("Razer Viper Ultimate Wireless", 0x3F),
    0x007A: KnownDevice("Razer Viper Ultimate Dongle", 0x3F),
    0x0555: KnownDevice("Razer BlackShark V2 Pro (RZ04-0453)", 0x3F),
    0x0528: KnownDevice("Razer BlackShark V2 Pro (RZ04-0322)", 0x3F),
    0x00AF: KnownDevice("Razer Cobra Pro Wired", 0x1F),
    0x00B0: KnownDevice("Razer Cobra Pro Wireless", 0x1F),
}


def _txn_order(pid: int) -> tuple[int, ...]:
    """Known transaction id first (if the PID is in the table), then the existing fallback order."""
    known = KNOWN_DEVICES.get(pid)
    if known is None:
        return TRANSACTION_IDS
    return (known.txn,) + tuple(t for t in TRANSACTION_IDS if t != known.txn)


def _is_generic(product: str) -> bool:
    return " ".join((product or "").lower().split()) in GENERIC_PRODUCTS


def crc(report: bytes) -> int:
    """XOR of bytes 2..87 of the 90-byte report."""
    c = 0
    for b in report[2:88]:
        c ^= b
    return c


def build_request(transaction_id: int, cmd_class: int, cmd_id: int, data_size: int,
                  args: bytes = b"") -> bytes:
    """91-byte buffer for HidD_SetFeature: report id 0 followed by the 90-byte packet."""
    if len(args) > 80:
        raise ValueError("args too long")
    pkt = bytearray(REPORT_LEN)
    pkt[1] = transaction_id & 0xFF
    pkt[5] = data_size & 0xFF
    pkt[6] = cmd_class & 0xFF
    pkt[7] = cmd_id & 0xFF
    pkt[8:8 + len(args)] = args
    pkt[88] = crc(bytes(pkt))
    return b"\x00" + bytes(pkt)


@dataclass
class Response:
    status: int
    transaction_id: int
    data_size: int
    cmd_class: int
    cmd_id: int
    args: bytes
    crc_ok: bool


def decode_response(buf: bytes | None) -> Response | None:
    """Parse the buffer returned by HidD_GetFeature (91 bytes with report id, or bare 90)."""
    if buf is None:
        return None
    if len(buf) == FEATURE_BUF_LEN:
        buf = buf[1:]
    if len(buf) != REPORT_LEN:
        return None
    return Response(status=buf[0], transaction_id=buf[1], data_size=buf[5], cmd_class=buf[6],
                    cmd_id=buf[7], args=bytes(buf[8:88]), crc_ok=crc(bytes(buf)) == buf[88])


def battery_percent(args: bytes) -> int | None:
    """Get-battery reply: args[1] is 0..255."""
    if len(args) < 2:
        return None
    return max(0, min(100, round(args[1] * 100 / 255)))


def is_charging(args: bytes) -> bool:
    return len(args) >= 2 and args[1] == 1


def kind_for(name: str) -> str:
    n = (name or "").lower()
    if any(w in n for w in ("blackshark", "kraken", "barracuda", "headset", "nari", "hammerhead")):
        return "headset"
    if any(w in n for w in ("blackwidow", "huntsman", "ornata", "deathstalker", "keyboard")):
        return "keyboard"
    from .naming import kind_from_name
    kind = kind_from_name(name)
    return kind if kind != "generic" else "mouse"


def link_for(name: str) -> str:
    n = (name or "").lower()
    if any(w in n for w in ("dongle", "hyperspeed", "receiver", "2.4", "wireless")):
        return "2.4ghz"
    return "usb"


def _query(handle, transaction_id: int, cmd_id: int, deadline: float | None = None) -> Response | None:
    req = build_request(transaction_id, CLASS_POWER, cmd_id, 0x02)
    for _ in range(RETRIES):
        if deadline is not None and time.monotonic() >= deadline:
            return None
        if not handle.set_feature(req):
            return None
        time.sleep(SET_GET_DELAY_S)
        resp = decode_response(handle.get_feature(0, FEATURE_BUF_LEN))
        if resp and resp.crc_ok and resp.transaction_id == transaction_id                 and resp.status == STATUS_SUCCESS and resp.cmd_class == CLASS_POWER                 and resp.cmd_id == cmd_id:
            return resp
        time.sleep(SET_GET_DELAY_S)
    return None


def read_battery(handle, deadline: float | None = None) -> tuple[int, bool] | None:
    """Try the known transaction ids; -> (percent, charging) or None."""
    return _read_battery_with(handle, TRANSACTION_IDS, deadline)


def _read_battery_with(handle, tids: tuple[int, ...], deadline: float | None) -> tuple[int, bool] | None:
    for tid in tids:
        resp = _query(handle, tid, CMD_GET_BATTERY, deadline)
        if resp is None:
            continue
        pct = battery_percent(resp.args)
        if pct is None:
            continue
        chg = _query(handle, tid, CMD_GET_CHARGING, deadline)
        return pct, bool(chg and is_charging(chg.args))
    return None


def _path_hash(path: str) -> str:
    stable = re.sub(r"&col\d+", "", (path or "").lower())
    return hashlib.sha1(stable.encode("utf-8", "replace")).hexdigest()[:6]


_last: dict[str, int] = {}      # device id -> last non-zero reading


def _plausible(dev_id: str, pct: int) -> int | None:
    """Razer firmwares sometimes answer 0 % transiently: a 0 right after a non-zero reading is
    treated as "no reading" (None). A 0 is believed when nothing >0 was seen before."""
    if pct == 0 and _last.get(dev_id, 0) > 0:
        return None
    _last[dev_id] = pct
    return pct


def scan_razer(infos, handle_factory=None, cancel: threading.Event | None = None) -> list[Device]:
    """Query vendor-page Razer collections that expose the 90-byte feature report. Never raises."""
    if handle_factory is None:
        from .hid_win import HidHandle
        handle_factory = HidHandle
    out: list[Device] = []
    seen: set[int] = set()
    deadline = time.monotonic() + SCAN_BUDGET_S
    for i in sorted((x for x in infos if x.vid == RAZER_VID), key=lambda x: x.path):
        if cancel is not None and cancel.is_set() or time.monotonic() >= deadline:
            break
        if i.usage_page < VENDOR_PAGE_MIN or i.feat_len < FEATURE_BUF_LEN:
            log.debug("razer %04x skip %04x:%04x feat=%d", i.pid, i.usage_page, i.usage, i.feat_len)
            continue
        if i.pid in seen or i.pid in ODESSA_DONGLES:     # ODESSA dongles: razer_odessa reads them
            continue
        try:
            with handle_factory(i.path) as h:
                res = _read_battery_with(h, _txn_order(i.pid), deadline)
        except Exception:
            log.debug("razer probe failed for %04x", i.pid, exc_info=True)
            continue
        if res is None:
            continue
        seen.add(i.pid)
        known = KNOWN_DEVICES.get(i.pid)
        product_name = sanitize_name(i.product)
        if known is not None and (not product_name or _is_generic(i.product)):
            name = sanitize_name(known.name) or "Razer device"
            kind = "headset" if "blackshark" in known.name.lower() else "mouse"
        else:
            name = product_name or "Razer device"
            kind = kind_for(name)
        dev_id = f"razer:{i.pid:04x}:{_path_hash(i.path)}"
        out.append(Device(id=dev_id, name=name, kind=kind,
                          battery=_plausible(dev_id, res[0]), connected=True, charging=res[1], link=link_for(name)))
    return out
