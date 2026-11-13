"""Safety / robustness tests for the 2.4 GHz HID code (whitelist, sw_id, deadlines, escaping...)."""
import logging
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from taskbar_battery_widget import hid_win, hidpp, wireless
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.naming import sanitize_name
from taskbar_battery_widget.hidpp import HidppSafetyError

real_scan = wireless.scan_wireless      # conftest replaces the module attribute per test


# ---- helpers --------------------------------------------------------------------------------
class ScriptedHandle:
    """Long-collection handle whose replies are produced by `responder(pkt) -> list[bytes]`."""

    def __init__(self, responder=None, read_error=None, read_delay=0.0):
        self.responder, self.read_error, self.read_delay = responder, read_error, read_delay
        self.q, self.writes = [], []

    def write(self, pkt, timeout_ms=1000):
        self.writes.append(bytes(pkt))
        if self.responder:
            self.q.extend(self.responder(bytes(pkt)))
        return True

    def read(self, timeout_ms=0):
        if self.read_error:
            raise self.read_error
        if self.q:
            return self.q.pop(0)
        if self.read_delay:
            time.sleep(min(self.read_delay, timeout_ms / 1000))
        return None

    def drain(self):
        pass

    def close(self):
        pass


def channel_for(handle, **kw):
    group = {"short": None, "long": SimpleNamespace(path="long")}
    return hidpp.Channel(group, lambda path, n=64: handle, **kw)


def reply(pkt, sw=None, params=b"\x01\x02\x03"):
    fs = pkt[3] if sw is None else ((pkt[3] & 0xF0) | sw)
    return (bytes([0x11, pkt[1], pkt[2], fs]) + params).ljust(20, b"\x00")


# ---- item 1: whitelist / feature index -------------------------------------------------------
def test_feature_index_ge_0x80_is_rejected_everywhere():
    assert hidpp.decode_get_feature(bytes([0x80, 0, 0])) is None
    assert hidpp.decode_get_feature(bytes([0xFF, 0, 0])) is None
    assert hidpp.decode_get_feature(bytes([0x7F, 0, 0])) == 0x7F
    h = ScriptedHandle(lambda pkt: [reply(pkt)])
    with channel_for(h) as ch:
        with pytest.raises(HidppSafetyError):
            ch.request(1, 0x80, 0, feature_id=hidpp.F_DEVICE_NAME)
        with pytest.raises(HidppSafetyError):
            ch.request(1, 0, 0, feature_id=hidpp.F_DEVICE_NAME)       # index 0 only for root
        # a poisoned cached index is ignored instead of being sent
        known = {"battery": (hidpp.F_ADC, 0x90)}
        ch.read_battery(1, known)
    assert all(w[2] < 0x80 for w in h.writes)
    assert "battery" not in known or known["battery"][1] < 0x80


def test_non_whitelisted_function_or_feature_is_rejected_without_writing():
    h = ScriptedHandle(lambda pkt: [reply(pkt)])
    with channel_for(h) as ch:
        for fid, fn in ((hidpp.F_UNIFIED_BATTERY, 0), (hidpp.F_UNIFIED_BATTERY, 2),
                        (hidpp.F_BATTERY_VOLTAGE, 1), (hidpp.F_DEVICE_NAME, 3),
                        (hidpp.F_ROOT, 2), (0x1814, 0), (0x0003, 0)):
            with pytest.raises(HidppSafetyError):
                ch.request(1, 5, fn, feature_id=fid)
        with pytest.raises(HidppSafetyError):                       # getFeature of a non-listed id
            ch.request(1, 0, 0, bytes([0x18, 0x14]), feature_id=hidpp.F_ROOT)
    assert h.writes == []
    assert hidpp.build_guarded_request(1, 5, hidpp.F_UNIFIED_BATTERY, 1)[2] == 5


# ---- item 2: sw_id --------------------------------------------------------------------------
def test_late_reply_with_other_sw_id_is_ignored():
    def responder(pkt):
        stale_sw = (pkt[3] & 0x0F) % 15 + 1
        return [reply(pkt, sw=stale_sw, params=b"\xAA\xAA\xAA"), reply(pkt, params=b"\x04\x02\xA5")]

    with channel_for(ScriptedHandle(responder)) as ch:
        state, ver = ch.ping(1)
    assert state == "ok" and ver == (4, 2)


def test_sw_id_rotates_between_requests():
    h = ScriptedHandle(lambda pkt: [reply(pkt)])
    with channel_for(h) as ch:
        for _ in range(20):
            ch.ping(1)
    sws = [w[3] & 0x0F for w in h.writes]
    assert 0 not in sws and len(set(sws)) == 15 and sws[0] != sws[1]


# ---- items 3, 5: deadline + permanent failure -----------------------------------------------
def test_deadline_is_honoured_with_slow_channel():
    h = ScriptedHandle(None, read_delay=5)
    with channel_for(h, deadline=time.monotonic() + 0.3) as ch:
        t = time.monotonic()
        assert ch.request(1, 0, 1, b"\x00\x00\xA5", timeout=5.0, feature_id=hidpp.F_ROOT) is None
        assert time.monotonic() - t < 1.0
        t = time.monotonic()                                         # deadline already passed
        assert ch.ping(1, timeout=5.0) == ("timeout", None)
        assert time.monotonic() - t < 0.2


def test_cancel_event_stops_request():
    cancel = threading.Event()
    h = ScriptedHandle(None, read_delay=5)
    with channel_for(h, cancel=cancel) as ch:
        threading.Timer(0.1, cancel.set).start()
        t = time.monotonic()
        assert ch.ping(1, timeout=5.0) == ("timeout", None)
        assert time.monotonic() - t < 1.0


def test_read_permanent_failure_stops_loop_quickly():
    h = ScriptedHandle(None, read_error=OSError("device gone"))
    with channel_for(h) as ch:
        t = time.monotonic()
        assert ch.ping(1, timeout=5.0) == ("timeout", None)
        assert time.monotonic() - t < 0.5 and ch.broken
        n = len(h.writes)
        assert ch.ping(1, timeout=5.0) == ("timeout", None)
        assert len(h.writes) == n                                    # nothing more is sent


# ---- item 7: ids / info cache ---------------------------------------------------------------
def test_two_receivers_with_same_pid_get_distinct_ids():
    from test_hidpp import FakeDongle, info
    hidpp._info_cache.clear()
    def pair(parent):
        base = r"\\?\HID#VID_046D&PID_C539&MI_02&Col0{n}#9&" + parent + r"&0&000{n}#{{guid}}"
        return [info(1, base.format(n=1) + "short"), info(2, base.format(n=2) + "long")]
    infos = pair("aa") + pair("bb")
    devs = hidpp.scan_logitech(infos, FakeDongle().handle)
    ids = sorted(d.id for d in devs)
    assert len(ids) == 2 and len(set(ids)) == 2 and all(i.count(":") == 3 for i in ids)
    hidpp._info_cache.clear()


def test_name_probe_failure_is_cached_with_ttl():
    from test_hidpp import FakeDongle, receiver_infos
    hidpp._info_cache.clear()
    d = FakeDongle()
    d.on_write_orig = d.on_write
    name_requests = []

    def on_write(pkt):
        if pkt[2] == 0 and (pkt[3] >> 4) == 0 and ((pkt[4] << 8) | pkt[5]) == hidpp.F_DEVICE_NAME:
            name_requests.append(1)
            return                                                   # never answers
        d.on_write_orig(pkt)
    d.on_write = on_write
    for _ in range(3):
        hidpp.scan_logitech(receiver_infos(), d.handle)
    assert len(name_requests) <= 2          # one failed name + one failed type probe, not per scan
    hidpp._info_cache.clear()


# ---- item 11: sanitising / tooltip ---------------------------------------------------------
def test_sanitize_name():
    assert sanitize_name("<img src=x>Mouse&\x00\x07 Pro") == "img src=xMouse Pro"
    assert len(sanitize_name("x" * 200)) == 48


def test_tooltip_does_not_render_device_markup(qapp):
    from taskbar_battery_widget import widget as w
    wd = w.TaskbarWidget(None)
    wd.set_devices([Device("1", "<img src=x> <b>evil</b>", "mouse", 80, True, link="2.4ghz")])
    tip = wd._pills[0].tooltip
    assert "<" not in tip and ">" not in tip
    html = w.tooltip_html("<img src=x>\n80%")
    assert "<img" not in html and "&lt;img" in html


# ---- item 5: hid_win close ordering (fake kernel32) ----------------------------------------
class FakeK32:
    def __init__(self, event_results=(11, 12)):
        self.calls, self.events = [], list(event_results)

    def CreateEventW(self, *a):
        v = self.events.pop(0)
        self.calls.append(("CreateEventW", v))
        return v

    def CancelIoEx(self, h, o):
        self.calls.append(("CancelIoEx", h))
        return 1

    def GetOverlappedResult(self, h, o, n, wait):
        self.calls.append(("GetOverlappedResult", h, bool(wait)))
        return 0

    def CloseHandle(self, h):
        self.calls.append(("CloseHandle", h))
        return 1


@pytest.mark.skipif(not hid_win._WIN, reason="Windows only")
def test_close_cancels_pending_read_before_closing_handles(monkeypatch):
    k = FakeK32()
    monkeypatch.setattr(hid_win, "_k32", k)
    monkeypatch.setattr(hid_win, "_open_raw", lambda path, access, ovl: 99)
    h = hid_win.HidHandle("p")
    h._pending = True
    h.close()
    h.close()                                                         # idempotent
    names = [c[0] for c in k.calls if c[0] != "CreateEventW"]
    assert names == ["CancelIoEx", "GetOverlappedResult", "CloseHandle", "CloseHandle", "CloseHandle"]
    assert ("GetOverlappedResult", 99, True) in k.calls
    assert [c[1] for c in k.calls if c[0] == "CloseHandle"][-1] == 99    # file handle closed last
    with pytest.raises(OSError):
        h.read(0)


@pytest.mark.skipif(not hid_win._WIN, reason="Windows only")
def test_failed_event_creation_closes_what_was_opened(monkeypatch):
    k = FakeK32(event_results=(11, 0))
    monkeypatch.setattr(hid_win, "_k32", k)
    monkeypatch.setattr(hid_win, "_open_raw", lambda path, access, ovl: 99)
    with pytest.raises(OSError):
        hid_win.HidHandle("p")
    closed = [c[1] for c in k.calls if c[0] == "CloseHandle"]
    assert sorted(closed) == [11, 99]


def test_guarded_call_does_not_block_on_hung_function():
    release = threading.Event()
    call = hid_win.GuardedCall(lambda: release.wait(30))
    t = time.monotonic()
    assert call.wait(0.2) is False and time.monotonic() - t < 1.0
    release.set()
    assert call.wait(2.0) and call.value is True


# ---- item 4: monitor ----------------------------------------------------------------------
def test_monitor_emits_bluetooth_first_and_stops_fast_when_wireless_hangs(qapp, monkeypatch):
    from taskbar_battery_widget import monitor as monitor_mod
    from taskbar_battery_widget.monitor import DeviceMonitor

    async def fake_bt_scan():
        return [Device("bt1", "Headphones", "headset", 80, True, link="bluetooth")]

    async def radio_on():
        return True

    monkeypatch.setattr(monitor_mod.scanner, "scan", fake_bt_scan)
    monkeypatch.setattr(monitor_mod.scanner, "is_radio_on", radio_on)
    monkeypatch.setattr(monitor_mod, "WIRELESS_TIMEOUT_S", 0.5)
    release = threading.Event()
    timed_out = threading.Event()

    class _Spy(logging.Handler):
        def emit(self, record):
            if "wireless scan exceeded" in record.getMessage():
                timed_out.set()

    spy = _Spy()
    monitor_mod.log.addHandler(spy)
    got = []
    mon = DeviceMonitor(use_watchers=False, wireless_scan=lambda: release.wait(30) or [])
    mon.devices_changed.connect(got.append)
    mon.start()
    t = time.monotonic()
    try:
        end = time.monotonic() + 5
        while not got and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert got and [d.id for d in got[0]] == ["bt1"]
        end = time.monotonic() + 10                                   # wait for the timeout to fire (event)
        while not timed_out.is_set() and time.monotonic() < end:
            QCoreApplication.processEvents()
            timed_out.wait(0.02)
        assert timed_out.is_set()
        t = time.monotonic()
    finally:
        monitor_mod.log.removeHandler(spy)
        mon.stop()
        stop_s = time.monotonic() - t
        release.set()
    assert stop_s < 2.0


# ---- items 8, 9 (cache / merge) ------------------------------------------------------------
def _dev(id_, name="Kbd", kind="keyboard", battery=50, link="2.4ghz"):
    return Device(id=id_, name=name, kind=kind, battery=battery, connected=True, link=link)


def test_cache_keeps_recent_battery_when_fresh_reading_has_none():
    cache, present = {}, {"046d:c539"}
    wireless.apply_cache(cache, [_dev("logi:c539:01", battery=70)], present, 0.0)
    out = wireless.apply_cache(cache, [_dev("logi:c539:01", battery=None)], present, 30.0)
    assert [x.battery for x in out] == [70]
    out = wireless.apply_cache(cache, [_dev("logi:c539:01", battery=None)], present,
                               wireless.CACHE_TTL_S + 1)
    assert [x.battery for x in out] == [None]                         # carried value expired


def test_empty_enumeration_after_seen_dongles_is_inconclusive(monkeypatch):
    import taskbar_battery_widget.hid_win as hw
    import taskbar_battery_widget.hidpp as hp
    import taskbar_battery_widget.razer as rz
    wireless.reset_cache()
    state = {"infos": [SimpleNamespace(vid=0x046D, pid=0xC539)], "n": 0}

    def enum(v=None):
        state["n"] += 1
        return state["infos"]

    monkeypatch.setattr(hw, "enumerate_hid", enum)
    monkeypatch.setattr(hp, "scan_logitech", lambda i, cancel=None: [_dev("logi:c539:01", battery=70)])
    monkeypatch.setattr(rz, "scan_razer", lambda i, cancel=None: [])
    assert [d.battery for d in real_scan()] == [70]
    state["infos"], state["n"] = [], 0
    assert [d.battery for d in real_scan()] == [70]                        # cache survives
    assert state["n"] == 3                  # core, extra vendors, and one retry of the core enumeration
    wireless.reset_cache()


def test_scan_wireless_is_bounded_when_enumeration_hangs(monkeypatch):
    import taskbar_battery_widget.hid_win as hw
    wireless.reset_cache()
    release = threading.Event()
    monkeypatch.setattr(wireless, "ENUM_BUDGET_S", 0.2)
    monkeypatch.setattr(hw, "enumerate_hid", lambda v=None: release.wait(10) or [])
    try:
        for _ in range(2):                                            # 2nd call: previous still hung
            t = time.monotonic()
            assert real_scan() == []
            assert time.monotonic() - t < 1.5
    finally:
        release.set()
        wireless._inflight.clear()
        wireless.reset_cache()


def test_merge_keeps_two_identical_wireless_mice():
    wl = [_dev("logi:c539:01", "G903", "mouse", 80), _dev("logi:c539:02", "G903", "mouse", 60)]
    assert [d.id for d in wireless.merge_devices([], wl)] == ["logi:c539:01", "logi:c539:02"]


def test_merge_dedupes_bluetooth_vs_wireless_by_containment():
    bt = [_dev("bt1", "MX Master 3", "mouse", None, link="bluetooth")]
    wl = [_dev("logi:c52b:01", "MX Master 3 Wireless Mouse", "mouse", 55)]
    assert [d.id for d in wireless.merge_devices(bt, wl)] == ["logi:c52b:01"]
    other_kind = [_dev("logi:c52b:01", "MX Master 3", "keyboard", 55)]
    assert len(wireless.merge_devices(bt, other_kind)) == 2


def test_hid_paths_are_redacted_in_logs():
    from taskbar_battery_widget.hid_win import redact_path
    bt = r"\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_dev_vid&0002054c_pid&0ce6_rev&0100_a1b2c3d4e5f6&col02#8&1f&0&0002#{x}"
    usb = r"\?\hid#vid_046d&pid_c539&mi_02&col01#7&1&0&0000#{x}"
    assert redact_path(bt) == "054c:0ce6 col02" and "a1b2c3d4e5f6" not in redact_path(bt)
    assert redact_path(usb) == "046d:c539 col01"
    assert redact_path("garbage") == "<hid>"


def test_shutdown_always_releases_lock_even_if_stop_steps_fail():
    from taskbar_battery_widget import app as app_mod

    class Ok:
        def stop(self): pass
        def hide(self): pass
        def close(self): pass

    class Boom(Ok):
        def stop(self): raise RuntimeError("x")
        def hide(self): raise RuntimeError("x")

    class Lock:
        n = 0
        def unlock(self): Lock.n += 1

    for widget, tray in ((Boom(), Ok()), (Ok(), Boom())):
        Lock.n = 0
        with pytest.raises(RuntimeError):
            app_mod.run_shutdown(Ok(), widget, tray, Ok(), Ok(), Lock())
        assert Lock.n == 1
    Lock.n = 0
    app_mod.run_shutdown(Ok(), Ok(), Ok(), Boom(), Ok(), Lock())      # monitor.stop failure is logged
    assert Lock.n == 1
