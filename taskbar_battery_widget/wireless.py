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
CORE_VENDOR_IDS = (0x046D, 0x1532, 0x0951, 0x054C)   # always-on readers (verified on hardware)
EXPERIMENTAL_VENDOR_IDS = (
    0x1038,                     # SteelSeries
    0x03F0,                     # HP / HyperX Cloud II/Alpha Wireless
    0x1B1C,                     # Corsair
    0x3329,                     # Audeze
    0x0B0E,                     # Jabra
    0x17EF, 0x3837, 0x291D,     # Lenovo, MCHOSE
)
VENDOR_IDS = CORE_VENDOR_IDS + EXPERIMENTAL_VENDOR_IDS
FLIGHT_VID, FLIGHT_PID = 0x0951, 0x16C4     # the only HyperX model kept when experimental is off
# Sony PULSE 3D / INZONE Buds push reports only on change: their last value must outlive the grace
PUSH_ONLY_IDS = frozenset({"sony:0d5e", "sony:0ec2"})
ENUM_BUDGET_S = 1.2         # HID enumeration (synchronous HidD_* calls) must finish within this
VENDOR_BUDGET_S = 2.6       # Logitech / Razer scans run in parallel under this budget

_cache: dict[str, tuple[Device, float]] = {}
_lock = threading.Lock()                # guards _cache / _last_present (short critical sections)
_scan_gate = threading.Lock()           # serialises scans; a busy gate means "use the cache"
_last_present: set[str] = set()         # dongles seen by the last conclusive enumeration
_last_present_ts = 0.0
_inflight: dict[str, GuardedCall] = {}  # name -> possibly hung call; never start a second one
_dualsense_bt_extended = True           # allow the one-off feature read enabling BT battery reports
_hid_enabled = True                     # False: 2.4 GHz HID reads are off
_experimental = False                   # opt-in: unverified readers; False = only verified ones
IDLE_GRACE_DEFAULT_S = 30 * 60.0
_idle_grace_s = IDLE_GRACE_DEFAULT_S    # how long a silent device stays listed as idle


def set_hid_enabled(enabled: bool) -> None:
    global _hid_enabled
    _hid_enabled = bool(enabled)


def set_experimental_readers(enabled: bool) -> None:
    global _experimental
    _experimental = bool(enabled)
    if not _experimental:
        with _lock:
            _purge_experimental()


def is_experimental_id(device_id: str) -> bool:
    """True for ids produced only by the opt-in (unverified) readers."""
    parts = device_id.split(":")
    prefix = parts[0]
    if prefix in ("steelseries", "corsair", "audeze", "jabra", "lenovo", "mchose", "xinput"):
        return True
    if len(parts) != 2:
        return False
    if prefix == "logi":                    # 'logi:<pid>' = headset base; the generic scanner adds ':<idx>'
        return True
    try:
        pid = int(parts[1], 16)
    except ValueError:
        return False
    if prefix == "hyperx":
        return pid != FLIGHT_PID
    if prefix == "sony":
        from . import sony
        return pid in sony.NAMES
    return False


def _purge_experimental() -> None:
    """Forget cached experimental devices, PUSH_ONLY ones included (caller holds _lock)."""
    for dev_id in [k for k in _cache if is_experimental_id(k)]:
        del _cache[dev_id]


def set_dualsense_bt_extended(enabled: bool) -> None:
    global _dualsense_bt_extended
    _dualsense_bt_extended = bool(enabled)


def set_idle_grace_min(minutes: float) -> None:
    global _idle_grace_s
    _idle_grace_s = max(0.0, float(minutes)) * 60.0


def _misc_vid(prefix: str, pid: int) -> int | None:
    from . import misc_headsets
    for (vid, p), m in misc_headsets.MODELS.items():
        if p == pid and m.vendor == prefix:
            return vid
    return None


def dongle_key(device_id: str) -> str | None:
    """Device id -> the HID 'vid:pid' it came from ('logi:c539:01' -> '046d:c539'); None = no dongle.

    XInput controllers have no dongle: they are never carried by dongle presence.
    """
    parts = device_id.split(":")
    prefix = parts[0]
    if prefix == "xinput":
        return None
    if prefix == "logi" and len(parts) >= 2:
        return f"046d:{parts[1]}"
    if prefix == "hyperx" and len(parts) == 2:
        try:
            pid = int(parts[1], 16)
        except ValueError:
            return device_id
        from . import hyperx
        vid = next((m.vid for m in hyperx.MODELS if pid in m.pids), hyperx.HYPERX_VID)
        return f"{vid:04x}:{parts[1]}"
    if prefix == "razer" and len(parts) == 2:           # direct ODESSA headset ('razer:1390')
        from .razer_odessa import ODESSA_DONGLES
        for pid, (_name, hs) in ODESSA_DONGLES.items():
            if parts[1] == str(hs):
                return f"1532:{pid:04x}"
    if prefix == "razer" and len(parts) >= 2:
        return f"1532:{parts[1]}"
    fixed = {"sony": 0x054C, "steelseries": 0x1038, "corsair": 0x1B1C, "audeze": 0x3329,
             "jabra": 0x0B0E}
    if prefix in fixed and len(parts) == 2:
        return f"{fixed[prefix]:04x}:{parts[1]}"
    if prefix in ("lenovo", "mchose") and len(parts) == 2:
        try:
            vid = _misc_vid(prefix, int(parts[1], 16))
        except ValueError:
            vid = None
        if vid is not None:
            return f"{vid:04x}:{parts[1]}"
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
        key = dongle_key(dev_id)
        if key is None or key not in present or (age > keep and dev_id not in PUSH_ONLY_IDS):
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
    xin = [d for d in extra if d.id.startswith("xinput:")]
    if xin:
        from .xinput import drop_bluetooth_duplicates
        keep_xin = {id(d) for d in drop_bluetooth_duplicates(xin, list(primary))}
        extra = [d for d in extra if not d.id.startswith("xinput:") or id(d) in keep_xin]
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


def dedupe_logitech(devices: list[Device]) -> list[Device]:
    """Drop a 'logi:<pid>' headset-base device when the generic scanner lists the same dongle.

    Pair (generic 'logi:<pid>:<idx>', headset 'logi:<pid>') on the same dongle: keep the one with a
    battery reading, the generic one on a tie. Prevents a flapping duplicate entry.
    """
    generic: dict[str, list[Device]] = {}
    for d in devices:
        parts = d.id.split(":")
        if parts[0] == "logi" and len(parts) >= 3:
            generic.setdefault(parts[1], []).append(d)
    if not generic:
        return devices
    headset_wins = {k for k, peers in generic.items() if all(p.battery is None for p in peers)
                    and any(d.id == f"logi:{k}" and d.battery is not None for d in devices)}
    out: list[Device] = []
    for d in devices:
        parts = d.id.split(":")
        if parts[0] == "logi" and len(parts) == 2 and parts[1] in generic                 and parts[1] not in headset_wins:
            continue
        if parts[0] == "logi" and len(parts) >= 3 and parts[1] in headset_wins:
            continue
        out.append(d)
    return out


def _snapshot(now: float, present: set[str] | None = None) -> list[Device]:
    with _lock:
        if not _experimental:
            _purge_experimental()
        pres = present if present is not None else set(_last_present)
        if not _hid_enabled:
            return []
        return dedupe_logitech(apply_cache(_cache, [], pres, now, grace=_idle_grace_s))


def cached_devices() -> list[Device]:
    """Recent cached wireless devices (used when a scan cannot run or overran)."""
    return _snapshot(time.monotonic())


def _start_enum(name: str, vids, cancel: threading.Event):
    from . import hid_win
    return _guarded(name, lambda: hid_win.enumerate_hid(vids), cancel)


def _enum_result(call, name: str, until: float):
    """Wait for an enumeration call until the absolute `until`; None = hung / failed (inconclusive)."""
    if call is None or not call.wait(until - time.monotonic()):
        log.warning("HID enumeration (%s) overran %.1f s", name, ENUM_BUDGET_S)
        return None
    if call.error is not None:
        log.error("HID enumeration (%s) failed: %r", name, call.error)
        return None
    return call.value or []


def _enumerate(cancel: threading.Event, vids=None, name: str = "enum"):
    """Enumerate HID interfaces of `vids` (default: the core vendors) within ENUM_BUDGET_S."""
    call = _start_enum(name, CORE_VENDOR_IDS if vids is None else vids, cancel)
    return _enum_result(call, name, time.monotonic() + ENUM_BUDGET_S)


def _vendor_of(key: str) -> int:
    return int(key.split(":")[0], 16)


def _headsets_runner(core: GuardedCall, logi_infos, t_end: float, cancel: threading.Event):
    """Logitech headset reader: starts once the core Logitech scan is done, on its own cancel token."""
    def run(_sel):
        if not core.wait(t_end - time.monotonic()) or core.error is not None:
            return []                       # core hung/failed: do not touch the same collections
        taken = set()
        for d in core.value or []:
            p = d.id.split(":")
            if p[0] == "logi" and len(p) >= 3:
                try:
                    taken.add(int(p[1], 16))
                except ValueError:
                    pass
        sel = [i for i in logi_infos if i.pid not in taken]
        if not sel or time.monotonic() >= t_end:
            return []
        from . import logitech_headsets
        return logitech_headsets.read(sel, t_end, cancel)
    return run


def _scan_locked() -> list[Device]:
    global _last_present, _last_present_ts
    cancel = threading.Event()
    experimental = _experimental
    if not experimental:
        with _lock:
            _purge_experimental()
    t_enum = time.monotonic() + ENUM_BUDGET_S
    # core and experimental vendors are enumerated by separate guarded calls with their own budget
    core_call = _start_enum("enum", CORE_VENDOR_IDS, cancel)
    exp_call = _start_enum("enum-exp", EXPERIMENTAL_VENDOR_IDS, cancel) if experimental else None
    infos = _enum_result(core_call, "enum", t_enum)
    now = time.monotonic()
    if infos is None:                       # hung or failed: inconclusive
        return _snapshot(now)
    with _lock:
        prev_present, prev_ts = set(_last_present), _last_present_ts
    prev_core = {k for k in prev_present if _vendor_of(k) in CORE_VENDOR_IDS}
    if not infos and prev_core:             # dongles vanished? retry once before believing it
        infos = _enumerate(cancel)
        now = time.monotonic()
        if infos is None:
            return _snapshot(now)
    core_present = {f"{i.vid:04x}:{i.pid:04x}" for i in infos}
    carried = False
    if not core_present and prev_core and now - prev_ts <= CACHE_TTL_S:
        core_present, carried = prev_core, True      # still empty: keep cached values until their TTL
    if not _hid_enabled:                    # 2.4 GHz reads off
        return []

    bt_ext = _dualsense_bt_extended
    t_end = now + VENDOR_BUDGET_S           # one absolute deadline for every reader of this cycle
    calls: list[tuple[GuardedCall, threading.Event]] = []
    named: dict[str, GuardedCall] = {}

    def add(name, vids, fn, source, own_cancel=None):
        """Start fn(matching infos) guarded when at least one HID matches `vids` (None: always)."""
        try:
            sel = list(source) if vids is None else [i for i in source if i.vid in vids]
            if vids is not None and not sel:
                return
            ev = own_cancel or cancel
            c = _guarded(name, lambda: fn(sel), ev)
            if c:
                calls.append((c, ev))
                named[name] = c
        except Exception:
            log.exception("%s scan failed", name)

    def lazy(module, **kw):
        def run(sel):
            import importlib
            m = importlib.import_module(f".{module}", __package__)
            return m.read(sel, t_end, cancel, **kw)
        return run

    def hyperx_read(sel):
        from .hyperx import read_hyperx
        if not experimental:
            sel = [i for i in sel if (i.vid, i.pid) == (FLIGHT_VID, FLIGHT_PID)]
        return read_hyperx(sel, t_end, cancel) if sel else []

    def xinput(_sel):
        from . import xinput as xi
        devs = xi.read([], t_end, cancel)
        return [d for d in devs if not (d.battery is None and d.link == "usb")]

    def odessa(sel):
        from .razer_odessa import read_odessa
        return read_odessa(sel, t_end, cancel)

    def razer(sel):
        from .razer import scan_razer
        return scan_razer(sel, cancel=cancel)

    def logitech(sel):
        from .hidpp import scan_logitech
        return list(scan_logitech(sel, cancel=cancel) or [])

    def dualsense(sel):
        from .dualsense import read_dualsense
        return read_dualsense(sel, t_end, cancel, enable_bt_extended=bt_ext)

    add("logitech", None, logitech, infos)
    add("razer", None, razer, infos)
    add("odessa", None, odessa, infos)
    add("hyperx", (0x0951,), hyperx_read, infos)
    add("dualsense", None, dualsense, infos)

    exp_present: set[str] = set()
    if experimental:
        # the core readers above are already running: a slow experimental enumeration only delays
        # (or skips) the experimental ones
        prev_exp = prev_present - prev_core
        exp_infos = _enum_result(exp_call, "enum-exp", t_enum)
        if exp_infos is not None and not exp_infos and prev_exp:
            exp_infos = _enumerate(cancel, EXPERIMENTAL_VENDOR_IDS, "enum-exp")
        if exp_infos is None:
            exp_present = prev_exp          # inconclusive: keep what was cached
        else:
            exp_present = {f"{i.vid:04x}:{i.pid:04x}" for i in exp_infos}
            add("hyperx-exp", (0x03F0,), hyperx_read, exp_infos)
            add("steelseries", (0x1038,), lazy("steelseries"), exp_infos)
            add("corsair", (0x1B1C,), lazy("corsair"), exp_infos)
            add("audeze", (0x3329,), lazy("audeze"), exp_infos)
            add("jabra", (0x0B0E,), lazy("jabra"), exp_infos)
            add("misc", (0x17EF, 0x3837, 0x291D), lazy("misc_headsets"), exp_infos)
            add("sony", (0x054C,), lazy("sony", enable_bt_extended=bt_ext), infos)
            add("xinput", None, xinput, infos)
            logi = [i for i in infos if i.vid == 0x046D]
            if logi and "logitech" in named:
                hs_cancel = threading.Event()       # never cancels the core Logitech readers
                add("logitech-headsets", None,
                    _headsets_runner(named["logitech"], logi, t_end, hs_cancel), logi, hs_cancel)

    present = core_present | exp_present
    with _lock:
        _last_present = set(present)
        if not carried:
            _last_present_ts = now

    fresh: list[Device] = []
    for c, ev in calls:
        if not c.wait(t_end - time.monotonic()):
            ev.set()                        # drop the late result and stop its requests
            log.warning("wireless vendor scan overran %.1f s; result dropped", VENDOR_BUDGET_S)
        elif c.error is not None:
            log.error("wireless vendor scan failed: %r", c.error)
        else:
            fresh += list(c.value or [])
    with _lock:
        out = apply_cache(_cache, fresh, present, time.monotonic(), grace=_idle_grace_s)
    return dedupe_logitech(out)


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
