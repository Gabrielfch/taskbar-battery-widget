"""2.4 GHz / USB-dongle devices: Logitech HID++ and Razer, with a short-lived per-device cache."""
from __future__ import annotations

import copy
import logging
import threading
import time

from .hid_win import GuardedCall
from .models import Device

log = logging.getLogger(__name__)

CACHE_TTL_S = 120.0
VENDOR_IDS = (0x046D, 0x1532, 0x0951)
ENUM_BUDGET_S = 1.2         # HID enumeration (synchronous HidD_* calls) must finish within this
VENDOR_BUDGET_S = 2.6       # Logitech / Razer scans run in parallel under this budget

_cache: dict[str, tuple[Device, float]] = {}
_lock = threading.Lock()                # guards _cache / _last_present (short critical sections)
_scan_gate = threading.Lock()           # serialises scans; a busy gate means "use the cache"
_last_present: set[str] = set()         # dongles seen by the last conclusive enumeration
_last_present_ts = 0.0
_inflight: dict[str, GuardedCall] = {}  # name -> possibly hung call; never start a second one
_hid_enabled = True                     # False: 2.4 GHz HID reads are off
IDLE_GRACE_DEFAULT_S = 30 * 60.0
_idle_grace_s = IDLE_GRACE_DEFAULT_S    # how long a silent device stays listed as idle


def set_hid_enabled(enabled: bool) -> None:
    global _hid_enabled
    _hid_enabled = bool(enabled)


def set_idle_grace_min(minutes: float) -> None:
    global _idle_grace_s
    _idle_grace_s = max(0.0, float(minutes)) * 60.0


def dongle_key(device_id: str) -> str:
    """'logi:c539:01' -> '046d:c539'; 'razer:0565' -> '1532:0565' (the HID vid:pid it came from)."""
    parts = device_id.split(":")
    if len(parts) >= 2 and parts[0] == "logi":
        return f"046d:{parts[1]}"
    if len(parts) == 2 and parts[0] == "hyperx":        # 'hyperx:16c4' -> '0951:16c4'
        return f"0951:{parts[1]}"
    if len(parts) == 2 and parts[0] == "razer":         # direct ODESSA headset ('razer:1390')
        from .razer_odessa import ODESSA_DONGLES
        for pid, (_name, hs) in ODESSA_DONGLES.items():
            if parts[1] == str(hs):
                return f"1532:{pid:04x}"
    if len(parts) >= 2 and parts[0] == "razer":
        return f"1532:{parts[1]}"
    return device_id


def apply_cache(cache: dict, fresh: list[Device], present: set[str], now: float,
                ttl: float = CACHE_TTL_S, grace: float = 0.0) -> list[Device]:
    """Update `cache` with fresh readings; re-use recent values of devices that did not answer.

    A fresh reading without a battery keeps the cached battery (and its original age) while that
    is younger than max(ttl, grace). A cached device is dropped when its dongle is no longer
    enumerated or it is older than max(ttl, grace). Carried values older than ttl (seconds) are
    flagged `idle` (asleep) with `updated` set to when they were really read.
    """
    seen = set()
    out: list[Device] = []
    keep = max(ttl, grace)
    for d in fresh:
        d = copy.copy(d)
        stamp = now
        old = cache.get(d.id)
        carried_age = None
        if d.battery is None and old is not None and old[0].battery is not None \
                and now - old[1] <= keep:
            d.battery, d.charging = old[0].battery, old[0].charging
            d.updated = old[0].updated
            stamp = old[1]                  # the carried value keeps its original age
            carried_age = now - old[1]
        cache[d.id] = (copy.copy(d), stamp)
        if carried_age is not None and carried_age > ttl:
            d.idle = True
            d.updated = time.time() - carried_age
        seen.add(d.id)
        out.append(d)
    for dev_id in list(cache):
        if dev_id in seen:
            continue
        dev, ts = cache[dev_id]
        age = now - ts
        if age > keep or dongle_key(dev_id) not in present:
            del cache[dev_id]
            continue
        dev = copy.copy(dev)
        if age > ttl:
            dev.idle = True
            dev.updated = time.time() - age         # when the carried value was really read
        out.append(dev)
    return out


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def _same_device(a: Device, b: Device) -> bool:
    if a.kind != b.kind:
        return False
    na, nb = _norm(a.name), _norm(b.name)
    return bool(na) and bool(nb) and (na == nb or na in nb or nb in na)


def merge_devices(primary: list[Device], extra: list[Device]) -> list[Device]:
    """Combine Bluetooth (primary) and wireless (extra) lists.

    A wireless device is the same as a Bluetooth one when the kinds match and one normalised name
    contains the other; then the one with a battery reading wins (ties keep the primary one, in
    its position). Wireless devices are never merged with each other (two identical mice stay).
    """
    result = list(primary)
    n_primary = len(result)
    taken: set[int] = set()
    for d in extra:
        match = next((i for i in range(n_primary) if i not in taken and _same_device(result[i], d)),
                     None)
        if match is None:
            result.append(d)
            continue
        taken.add(match)
        cur = result[match]
        if (cur.battery is None or not cur.connected) and d.battery is not None and d.connected:
            result[match] = d
    return result


def _guarded(name: str, fn, cancel: threading.Event) -> GuardedCall | None:
    """Start fn in a daemon thread unless the previous call of the same name is still hung."""
    prev = _inflight.get(name)
    if prev is not None and not prev.done.is_set():
        log.warning("%s scan still hung from a previous cycle; skipping", name)
        return None
    call = GuardedCall(fn, f"wireless-{name}")
    _inflight[name] = call
    return call
    return out


def _snapshot(now: float, present: set[str] | None = None) -> list[Device]:
    with _lock:
        pres = present if present is not None else set(_last_present)
        return apply_cache(_cache, [], pres, now, grace=_idle_grace_s) if _hid_enabled else []


def cached_devices() -> list[Device]:
    """Recent cached wireless devices (used when a scan cannot run or overran)."""
    return _snapshot(time.monotonic())


def _enumerate(cancel: threading.Event):
    from .hid_win import enumerate_hid
    vids = VENDOR_IDS
    call = _guarded("enum", lambda: enumerate_hid(vids), cancel)
    if call is None or not call.wait(ENUM_BUDGET_S):
        log.warning("HID enumeration overran %.1f s", ENUM_BUDGET_S)
        return None
    if call.error is not None:
        log.error("HID enumeration failed: %r", call.error)
        return None
    return call.value or []


def _scan_locked() -> list[Device]:
    global _last_present, _last_present_ts
    cancel = threading.Event()
    infos = _enumerate(cancel)
    now = time.monotonic()
    if infos is None:                       # hung or failed: inconclusive
        return _snapshot(now)
    if not infos and _last_present:         # dongles vanished? retry once before believing it
        infos = _enumerate(cancel)
        now = time.monotonic()
        if infos is None:
            return _snapshot(now)
    present = {f"{i.vid:04x}:{i.pid:04x}" for i in infos}
    if not present and _last_present and now - _last_present_ts <= CACHE_TTL_S:
        return _snapshot(now)               # still empty: keep cached values until their TTL
    with _lock:
        _last_present, _last_present_ts = set(present), now

    calls: list[GuardedCall] = []
    if not _hid_enabled:                    # 2.4 GHz reads off
        return []
    try:
        from .hidpp import scan_logitech
        c = _guarded("logitech", lambda: scan_logitech(infos, cancel=cancel), cancel)
        if c:
            calls.append(c)
    except Exception:
        log.exception("Logitech HID++ scan failed")
    try:
        from .razer import scan_razer
        c = _guarded("razer", lambda: scan_razer(infos, cancel=cancel), cancel)
        if c:
            calls.append(c)
    except Exception:
        log.exception("Razer scan failed")

    try:
        from .razer_odessa import read_odessa
        c = _guarded("odessa", lambda: read_odessa(infos, time.monotonic() + VENDOR_BUDGET_S,
                                                   cancel), cancel)
        if c:
            calls.append(c)
    except Exception:
        log.exception("ODESSA scan failed")

    try:
        from .hyperx import read_hyperx
        c = _guarded("hyperx", lambda: read_hyperx(infos, time.monotonic() + VENDOR_BUDGET_S,
                                                   cancel), cancel)
        if c:
            calls.append(c)
    except Exception:
        log.exception("HyperX scan failed")

    fresh: list[Device] = []
    t_end = time.monotonic() + VENDOR_BUDGET_S
    for c in calls:
        if not c.wait(t_end - time.monotonic()):
            cancel.set()                    # drop the late result and stop its requests
            log.warning("wireless vendor scan overran %.1f s; result dropped", VENDOR_BUDGET_S)
        elif c.error is not None:
            log.error("wireless vendor scan failed: %r", c.error)
        else:
            fresh += list(c.value or [])
    with _lock:
        out = apply_cache(_cache, fresh, present, time.monotonic(), grace=_idle_grace_s)
    return out


def scan_wireless() -> list[Device]:
    """Scan all dongles within a bounded time. Never raises; hung drivers cannot block the caller."""
    if not _scan_gate.acquire(timeout=0.2):
        return cached_devices()             # another scan is running
    try:
        return _scan_locked()
    except Exception:
        log.exception("wireless scan failed")
        return []
    finally:
        _scan_gate.release()


def reset_cache() -> None:
    global _last_present, _last_present_ts
    with _lock:
        _cache.clear()
        _last_present = set()
        _last_present_ts = 0.0
