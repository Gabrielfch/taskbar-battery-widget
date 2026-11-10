"""Logitech HID++ (receiver / direct) battery reader.

Pure encode/decode helpers (unit tested with byte fixtures) plus `scan_logitech`, which only
sends read/query requests (ping, getFeature, device name/type, battery status).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass

from .hid_win import GuardedCall
from .models import Device
from .naming import sanitize_name

log = logging.getLogger(__name__)

LOGITECH_VID = 0x046D
SHORT_ID, LONG_ID = 0x10, 0x11
SHORT_LEN, LONG_LEN = 7, 20
SW_ID = 0x0B
HIDPP_PAGE = 0xFF00
USAGE_SHORT, USAGE_LONG = 0x0001, 0x0002

F_ROOT = 0x0000
F_DEVICE_NAME = 0x0005
F_BATTERY_STATUS = 0x1000
F_BATTERY_VOLTAGE = 0x1001
F_UNIFIED_BATTERY = 0x1004
F_ADC = 0x1F20
BATTERY_FEATURES = (F_UNIFIED_BATTERY, F_BATTERY_STATUS, F_BATTERY_VOLTAGE, F_ADC)

# Read-only whitelist: feature id -> functions this app may ever send. Anything else is refused.
ALLOWED_FUNCTIONS: dict[int, frozenset[int]] = {
    F_ROOT: frozenset({0, 1}),              # getFeature, ping
    F_DEVICE_NAME: frozenset({0, 1, 2}),    # count, name chunk, type
    F_UNIFIED_BATTERY: frozenset({1}),      # get_status
    F_BATTERY_STATUS: frozenset({0}),
    F_BATTERY_VOLTAGE: frozenset({0}),
    F_ADC: frozenset({0}),
}
MAX_FEATURE_INDEX = 0x7F                    # indexes >= 0x80 collide with the error markers
VALID_DEVICE_INDEXES = frozenset(range(0, 7)) | {0xFF}


class HidppSafetyError(ValueError):
    """Raised when a request is not on the read-only whitelist."""

# HID++ 2.0 device type (feature 0x0005 fn2) -> app kind
DEVICE_TYPES = {
    0: "keyboard", 1: "remote", 2: "keyboard", 3: "mouse", 4: "mouse", 5: "mouse",
    6: "remote", 8: "headset", 10: "gamepad", 11: "gamepad", 12: "gamepad",
    14: "speaker", 15: "microphone",
}

# Li-ion discharge curve (mV -> %), monotone.
_VOLT_CURVE = ((3500, 0), (3600, 8), (3700, 25), (3750, 40), (3800, 55),
               (3900, 70), (4000, 85), (4100, 95), (4200, 100))


# --------------------------------------------------------------------------- encode / decode
def encode_request(device_index: int, feature_index: int, function: int, params: bytes = b"",
                   sw_id: int = SW_ID, long: bool = True) -> bytes:
    """HID++ 2.0 request: [report_id, device_index, feature_index, (function<<4)|sw_id, params...]."""
    size = LONG_LEN if long else SHORT_LEN
    body = bytes([LONG_ID if long else SHORT_ID, device_index & 0xFF, feature_index & 0xFF,
                  ((function & 0x0F) << 4) | (sw_id & 0x0F)]) + bytes(params)
    if len(body) > size:
        raise ValueError("HID++ params too long")
    return body.ljust(size, b"\x00")


@dataclass
class Reply:
    report_id: int
    device_index: int
    feature_index: int      # for errors: the feature index of the failed request
    function: int
    sw_id: int
    params: bytes
    error: int | None = None
    error_kind: str | None = None   # "hidpp1" | "hidpp2"


def parse_report(data: bytes | None) -> Reply | None:
    if not data or len(data) < SHORT_LEN or data[0] not in (SHORT_ID, LONG_ID):
        return None
    rid, dev, b2, b3, b4 = data[0], data[1], data[2], data[3], data[4]
    if b2 == 0x8F:                                   # HID++ 1.0 error: [10 dev 8F sub addr err 00]
        return Reply(rid, dev, b3, b4 >> 4, b4 & 0x0F, bytes(data[6:]), data[5], "hidpp1")
    if b2 == 0xFF:                                   # HID++ 2.0 error: [11 dev FF feat fn|sw err]
        return Reply(rid, dev, b3, b4 >> 4, b4 & 0x0F, bytes(data[6:]), data[5], "hidpp2")
    return Reply(rid, dev, b2, b3 >> 4, b3 & 0x0F, bytes(data[4:]))


def build_guarded_request(device_index: int, feature_index: int, feature_id: int, function: int,
                          params: bytes = b"", sw_id: int = SW_ID, long: bool = True) -> bytes:
    """Validate against the read-only whitelist, then encode. Raises HidppSafetyError."""
    allowed = ALLOWED_FUNCTIONS.get(feature_id)
    if allowed is None or function not in allowed:
        raise HidppSafetyError(f"HID++ feature 0x{feature_id:04X} fn {function} is not whitelisted")
    if device_index not in VALID_DEVICE_INDEXES:
        raise HidppSafetyError(f"bad HID++ device index {device_index}")
    if feature_id == F_ROOT:
        if feature_index != 0:
            raise HidppSafetyError("root feature must use index 0")
        if function == 0 and (len(params) < 2 or ((params[0] << 8) | params[1]) not in ALLOWED_FUNCTIONS):
            raise HidppSafetyError("root getFeature for a non-whitelisted feature")
    elif not 1 <= feature_index <= MAX_FEATURE_INDEX:
        raise HidppSafetyError(f"bad HID++ feature index {feature_index}")
    if not 1 <= sw_id <= 15:
        raise HidppSafetyError("bad sw_id")
    return encode_request(device_index, feature_index, function, params, sw_id, long)


def reply_matches(rep: Reply, device_index: int, feature_index: int, function: int,
                  sw_id: int = SW_ID) -> bool:
    return (rep.device_index == device_index and rep.feature_index == feature_index
            and rep.function == function and rep.sw_id == sw_id)


def is_not_connected(rep: Reply) -> bool:
    """HID++ 1.0 error 0x08 (unknown device) / 0x09 (resource error: empty slot) reply to a ping."""
    return rep.error_kind == "hidpp1" and rep.error in (0x08, 0x09)


def decode_get_feature(params: bytes) -> int | None:
    """Root fn0 reply -> feature index (None if unsupported or not a valid index 1..0x7F)."""
    if not params or params[0] == 0 or params[0] > MAX_FEATURE_INDEX:
        return None
    return params[0]


def decode_ping(params: bytes) -> tuple[int, int] | None:
    """Root fn1 reply -> (protocol major, minor)."""
    if len(params) < 2:
        return None
    return params[0], params[1]


def voltage_to_percent(mv: int) -> int:
    if mv <= _VOLT_CURVE[0][0]:
        return 0
    if mv >= _VOLT_CURVE[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(_VOLT_CURVE, _VOLT_CURVE[1:]):
        if v0 <= mv <= v1:
            return round(p0 + (p1 - p0) * (mv - v0) / (v1 - v0))
    return 0


_LEVEL_FLAG_PCT = ((8, 100), (4, 55), (2, 15), (1, 5))     # full, good, low, critical


def decode_unified_battery(params: bytes) -> tuple[int, bool] | None:
    """0x1004 fn1 get_status: [soc %, level flags, charging status, external power]."""
    if len(params) < 3:
        return None
    soc, flags, status = params[0], params[1], params[2]
    if soc == 0:
        for bit, pct in _LEVEL_FLAG_PCT:
            if flags & bit:
                soc = pct
                break
    if soc > 100:
        return None
    return soc, status in (1, 2)        # 0 discharging, 1 charging, 2 slow, 3 full, 4 error


def decode_battery_status(params: bytes) -> tuple[int, bool] | None:
    """0x1000 fn0: [level %, next level %, status]; status 1 recharging, 2 almost full, 4 slow."""
    if len(params) < 3:
        return None
    level, status = params[0], params[2]
    if level > 100:
        return None
    return level, status in (1, 2, 4)


def decode_voltage(params: bytes) -> tuple[int, bool] | None:
    """0x1001 / 0x1F20 fn0: [mV hi, mV lo, flags]. Returns (percent, charging)."""
    if len(params) < 3:
        return None
    mv = (params[0] << 8) | params[1]
    if mv < 2500 or mv > 5000:
        return None
    return voltage_to_percent(mv), bool(params[2] & 0x80)


def decode_adc(params: bytes) -> tuple[int, bool] | None:
    """0x1F20 fn0: [mV hi, mV lo, flags]; flag bit 0 set while charging."""
    if len(params) < 3:
        return None
    mv = (params[0] << 8) | params[1]
    if mv < 2500 or mv > 5000:
        return None
    return voltage_to_percent(mv), bool(params[2] & 0x01)


# feature id -> (function, decoder)
_BATTERY_READERS = {
    F_UNIFIED_BATTERY: (1, decode_unified_battery),
    F_BATTERY_STATUS: (0, decode_battery_status),
    F_BATTERY_VOLTAGE: (0, decode_voltage),
    F_ADC: (0, decode_adc),
}


def decode_name_length(params: bytes) -> int:
    return params[0] if params else 0


def decode_name_chunk(params: bytes) -> str:
    return bytes(params).split(b"\x00", 1)[0].decode("ascii", "replace")


def assemble_name(chunks: list[bytes], length: int) -> str:
    raw = b"".join(bytes(c) for c in chunks)[:length]
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def kind_from_device_type(code: int | None) -> str | None:
    return DEVICE_TYPES.get(code) if code is not None else None


# --------------------------------------------------------------------------- grouping
_PARENT_RE = re.compile(r"#([0-9a-f]+&[0-9a-f]+)&\d+&", re.I)
_MI_RE = re.compile(r"&mi_([0-9a-f]{2})", re.I)


def group_key(info) -> tuple:
    """Same physical HID interface (the &colNN collections) share this key."""
    p = info.path
    m = _PARENT_RE.search(p)
    mi = _MI_RE.search(p)
    return (info.vid, info.pid, mi.group(1).lower() if mi else "",
            m.group(1).lower() if m else re.sub(r"&col\d+", "", p, flags=re.I))


def find_hidpp_groups(infos) -> list[dict]:
    """Group HID collections into HID++ interfaces: [{'key','vid','pid','short','long'}]."""
    groups: dict[tuple, dict] = {}
    for i in infos:
        if i.vid != LOGITECH_VID or i.usage_page != HIDPP_PAGE:
            continue
        if i.usage == USAGE_SHORT and i.out_len >= SHORT_LEN:
            slot = "short"
        elif i.usage == USAGE_LONG and i.out_len >= LONG_LEN:
            slot = "long"
        else:
            continue
        g = groups.setdefault(group_key(i), {"key": group_key(i), "vid": i.vid, "pid": i.pid,
                                              "product": i.product, "short": None, "long": None})
        g[slot] = i
    return [g for g in groups.values() if g["short"] or g["long"]]


def is_receiver(group: dict) -> bool:
    return "receiver" in (group.get("product") or "").lower() or group["pid"] in KNOWN_RECEIVERS


KNOWN_RECEIVERS = {0xC52B, 0xC532, 0xC534, 0xC539, 0xC53A, 0xC53D, 0xC53F, 0xC541, 0xC545,
                   0xC547, 0xC548, 0xC54D, 0xC52F, 0xC531, 0xC70E, 0xC713}


# --------------------------------------------------------------------------- transport
class Channel:
    """HID++ request/response over the short and/or long collection of one interface.

    `deadline` (absolute time.monotonic value) and `cancel` (threading.Event) bound every request,
    so a scan can be abandoned from outside. `broken` is set after a permanent read/write failure.
    """

    def __init__(self, group: dict, handle_factory=None, deadline: float | None = None,
                 cancel: threading.Event | None = None):
        if handle_factory is None:
            from .hid_win import HidHandle
            handle_factory = HidHandle
        self.deadline = deadline
        self.cancel = cancel
        self.broken = False
        self._sw = 0
        self._short = self._long = None
        try:
            if group.get("short"):
                self._short = handle_factory(group["short"].path, SHORT_LEN)
            if group.get("long"):
                self._long = handle_factory(group["long"].path, LONG_LEN)
        except Exception:
            self.close()
            raise
        if not (self._short or self._long):
            raise OSError("no HID++ collection")

    def close(self) -> None:
        for h in (self._short, self._long):
            if h is not None:
                try:
                    h.close()
                except Exception:
                    pass
        self._short = self._long = None

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()

    def _next_sw(self) -> int:
        self._sw = self._sw % 15 + 1            # rotate 1..15 so a late reply never matches
        return self._sw

    def _expired(self) -> bool:
        return (self.cancel is not None and self.cancel.is_set()) or \
            (self.deadline is not None and time.monotonic() >= self.deadline)

    def request(self, device_index: int, feature_index: int, function: int, params: bytes = b"",
                timeout: float = 1.0, *, feature_id: int, deadline: float | None = None) -> Reply | None:
        """Send one whitelisted query; return the matching reply (success or error) or None.

        None means timeout, deadline/cancel or a broken channel. Non-whitelisted requests raise
        HidppSafetyError before anything is written.
        """
        use_long = self._long is not None
        out = self._long if use_long else self._short
        sw = self._next_sw()
        pkt = build_guarded_request(device_index, feature_index, feature_id, function, params,
                                    sw, use_long)
        if self.broken or out is None or self._expired():
            return None
        t_end = time.monotonic() + timeout
        for d in (deadline, self.deadline):
            if d is not None:
                t_end = min(t_end, d)
        try:
            for h in (self._short, self._long):
                if h is not None:
                    h.drain()
            left = t_end - time.monotonic()
            if left <= 0 or not out.write(pkt, int(min(left, 1.0) * 1000)):
                return None
            while True:
                left = t_end - time.monotonic()
                if left <= 0 or (self.cancel is not None and self.cancel.is_set()):
                    return None
                for h in (self._long, self._short):
                    if h is None:
                        continue
                    slice_s = min(left, 0.02 if self._short and self._long else 0.1)
                    rep = parse_report(h.read(int(slice_s * 1000)))
                    if rep and reply_matches(rep, device_index, feature_index, function, sw):
                        return rep
                time.sleep(0.001)
        except OSError:
            self.broken = True
            return None

    # ---- typed helpers
    def get_feature_index(self, device_index: int, feature_id: int, timeout: float = 0.8) -> int | None:
        rep = self.request(device_index, 0x00, 0, bytes([feature_id >> 8, feature_id & 0xFF]), timeout,
                           feature_id=F_ROOT)
        if rep is None or rep.error is not None:
            return None
        return decode_get_feature(rep.params)

    def ping(self, device_index: int, timeout: float = 0.8):
        """-> ('ok', (maj, min)) | ('absent', None) | ('timeout', None) | ('hidpp1', None)"""
        rep = self.request(device_index, 0x00, 1, b"\x00\x00" + bytes([0xA5]), timeout,
                           feature_id=F_ROOT)
        if rep is None:
            return "timeout", None
        if rep.error is not None:
            return ("absent" if is_not_connected(rep) else "hidpp1"), None
        return "ok", decode_ping(rep.params)

    def read_name(self, device_index: int, timeout: float = 0.8) -> str | None:
        idx = self.get_feature_index(device_index, F_DEVICE_NAME, timeout)
        if idx is None:
            return None
        rep = self.request(device_index, idx, 0, b"", timeout, feature_id=F_DEVICE_NAME)
        if rep is None or rep.error is not None:
            return None
        length = decode_name_length(rep.params)
        chunks: list[bytes] = []
        got = 0
        while got < length and len(chunks) < 8:
            rep = self.request(device_index, idx, 1, bytes([got]), timeout, feature_id=F_DEVICE_NAME)
            if rep is None or rep.error is not None:
                break
            chunk = bytes(rep.params).split(b"\x00", 1)[0]
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
        return sanitize_name(assemble_name(chunks, length)) or None

    def read_type(self, device_index: int, timeout: float = 0.8) -> int | None:
        idx = self.get_feature_index(device_index, F_DEVICE_NAME, timeout)
        if idx is None:
            return None
        rep = self.request(device_index, idx, 2, b"", timeout, feature_id=F_DEVICE_NAME)
        if rep is None or rep.error is not None or not rep.params:
            return None
        return rep.params[0]

    def read_battery(self, device_index: int, known: dict | None = None, timeout: float = 0.8):
        """-> (percent, charging) | None. `known` caches the working battery feature index."""
        order = BATTERY_FEATURES
        cached = known.get("battery") if known else None
        if cached is not None:
            cfid, cidx = cached
            if cfid in _BATTERY_READERS and 1 <= cidx <= MAX_FEATURE_INDEX:
                order = (cfid,)
            else:                               # corrupt cache entry: ignore it
                known.pop("battery", None)
                cached = None
        for fid in order:
            idx = cached[1] if cached is not None else self.get_feature_index(device_index, fid, timeout)
            if idx is None:
                continue
            fn, decoder = _BATTERY_READERS[fid]
            rep = self.request(device_index, idx, fn, b"", timeout, feature_id=fid)
            if rep is None or rep.error is not None:
                continue
            res = decoder(rep.params)
            if res is not None:
                if known is not None:
                    known["battery"] = (fid, idx)
                return res
        if known is not None:
            known.pop("battery", None)
        return None


# --------------------------------------------------------------------------- scanning
# (receiver interface key, vid, pid, idx) -> {'name','type','battery','probed_at','bfail'}
_info_cache: dict[tuple, dict] = {}
_info_lock = threading.Lock()
GROUP_BUDGET_S = 2.5
INFO_RETRY_TTL_S = 600.0        # do not re-probe a name/type that failed for this long


def _path_hash(group: dict) -> str:
    return hashlib.sha1(repr(group["key"]).encode("utf-8", "replace")).hexdigest()[:6]


def _scan_group(group: dict, handle_factory=None, cancel: threading.Event | None = None,
                id_suffix: str = "") -> list[Device]:
    from .naming import kind_from_name
    devices: list[Device] = []
    receiver = is_receiver(group)
    indexes = range(1, 7) if receiver else (0xFF,)
    t_end = time.monotonic() + GROUP_BUDGET_S
    with Channel(group, handle_factory, deadline=t_end, cancel=cancel) as ch:
        misses = 0
        for idx in indexes:
            if time.monotonic() > t_end or ch.broken or (cancel is not None and cancel.is_set()):
                break
            state, _ver = ch.ping(idx, timeout=1.6)
            ckey = (group["key"], group["vid"], group["pid"], idx)
            if state == "timeout":
                misses += 1
                if receiver and misses >= 2:
                    break
                continue
            if state != "ok":
                if state == "absent":
                    with _info_lock:
                        _info_cache.pop(ckey, None)     # slot may be re-paired with another device
                continue
            with _info_lock:
                info = dict(_info_cache.get(ckey, {}))
            now = time.monotonic()
            if (not info.get("name") or info.get("type") is None) and \
                    now - info.get("probed_at", -INFO_RETRY_TTL_S - 1) > INFO_RETRY_TTL_S:
                info["probed_at"] = now             # failures are cached for INFO_RETRY_TTL_S
                info["name"] = ch.read_name(idx) or info.get("name")
                info["type"] = ch.read_type(idx)
            reading = ch.read_battery(idx, info)
            name = sanitize_name(info.get("name") or group.get("product") or "") or "Logitech device"
            kind = kind_from_device_type(info.get("type")) or kind_from_name(name)
            if reading is None:
                info["bfail"] = info.get("bfail", 0) + 1
                if info["bfail"] >= 2:              # identity may be stale: probe again next time
                    info = {}
            else:
                info["bfail"] = 0
            with _info_lock:
                if info:
                    _info_cache[ckey] = info
                else:
                    _info_cache.pop(ckey, None)
            link = "2.4ghz" if receiver else "usb"
            devices.append(Device(
                id=f"logi:{group['pid']:04x}:{idx:02x}{id_suffix}", name=name, kind=kind,
                battery=reading[0] if reading else None, connected=True,
                charging=bool(reading[1]) if reading else False, link=link))
    return devices


def scan_logitech(infos, handle_factory=None, cancel: threading.Event | None = None) -> list[Device]:
    """Query every HID++ interface in `infos`. Never raises; failing/overrunning groups are skipped."""
    groups = find_hidpp_groups(infos)
    if not groups:
        return []
    cancel = cancel or threading.Event()
    counts: dict[tuple, int] = {}
    for g in groups:
        counts[(g["vid"], g["pid"])] = counts.get((g["vid"], g["pid"]), 0) + 1

    def make(g):
        suffix = f":{_path_hash(g)}" if counts[(g["vid"], g["pid"])] > 1 else ""

        def one():
            try:
                return _scan_group(g, handle_factory, cancel, suffix)
            except Exception:
                log.debug("HID++ scan failed for %04x:%04x", g["vid"], g["pid"], exc_info=True)
                return []
        return GuardedCall(one, "hidpp-group")

    calls = [make(g) for g in groups]
    t_end = time.monotonic() + GROUP_BUDGET_S + 0.5
    out: list[Device] = []
    for call in calls:
        if call.wait(t_end - time.monotonic()):
            out.extend(call.value or [])
        else:
            cancel.set()                            # overran: drop its result, stop its requests
            log.warning("HID++ group scan overran its budget; result dropped")
    return out
