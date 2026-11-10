import asyncio
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from taskbar_battery_widget import wireless
from taskbar_battery_widget.models import Device

real_scan = wireless.scan_wireless      # conftest replaces the module attribute per test


def dev(id_, name="Kbd", kind="keyboard", battery=50, connected=True, link="2.4ghz"):
    return Device(id=id_, name=name, kind=kind, battery=battery, connected=connected, link=link)


def test_dongle_key():
    assert wireless.dongle_key("logi:c539:01") == "046d:c539"
    assert wireless.dongle_key("razer:0565") == "1532:0565"


def test_cache_keeps_recent_value_on_transient_failure():
    cache = {}
    d = dev("logi:c539:01", battery=70)
    assert [x.battery for x in wireless.apply_cache(cache, [d], {"046d:c539"}, 0.0)] == [70]
    out = wireless.apply_cache(cache, [], {"046d:c539"}, 60.0)
    assert [x.battery for x in out] == [70]
    out[0].battery = 1                      # callers cannot corrupt the cache
    assert cache["logi:c539:01"][0].battery == 70


def test_cache_expires_after_ttl_and_when_dongle_unplugged():
    cache = {}
    wireless.apply_cache(cache, [dev("logi:c539:01")], {"046d:c539"}, 0.0)
    assert wireless.apply_cache(cache, [], {"046d:c539"}, wireless.CACHE_TTL_S + 1) == []
    assert cache == {}
    wireless.apply_cache(cache, [dev("logi:c539:01")], {"046d:c539"}, 0.0)
    assert wireless.apply_cache(cache, [], set(), 5.0) == []     # dongle gone: drop at once


def test_cache_fresh_value_replaces_old():
    cache = {}
    present = {"046d:c539"}
    wireless.apply_cache(cache, [dev("logi:c539:01", battery=70)], present, 0.0)
    out = wireless.apply_cache(cache, [dev("logi:c539:01", battery=65)], present, 30.0)
    assert [x.battery for x in out] == [65]
    assert cache["logi:c539:01"][0].battery == 65


def test_merge_keeps_both_distinct_devices():
    bt = [dev("bt1", "Headphones", "headset", 80, link="bluetooth")]
    wl = [dev("logi:c539:01", "G915", "keyboard", 71)]
    assert [d.id for d in wireless.merge_devices(bt, wl)] == ["bt1", "logi:c539:01"]


def test_merge_dedupes_same_name_and_kind_preferring_battery():
    bt = [dev("bt1", "G915 Keyboard", "keyboard", None, link="bluetooth")]
    wl = [dev("logi:c539:01", "g915  keyboard", "keyboard", 71)]
    assert [d.id for d in wireless.merge_devices(bt, wl)] == ["logi:c539:01"]
    bt2 = [dev("bt1", "G915 Keyboard", "keyboard", 60, link="bluetooth")]
    assert [d.id for d in wireless.merge_devices(bt2, wl)] == ["bt1"]


def test_scan_wireless_never_raises(monkeypatch):
    wireless.reset_cache()
    import taskbar_battery_widget.hid_win as hw

    def boom(v=None):
        raise RuntimeError("x")

    monkeypatch.setattr(hw, "enumerate_hid", boom)
    assert real_scan() == []
    monkeypatch.setattr(hw, "enumerate_hid", lambda v=None: [])
    assert real_scan() == []


def test_scan_wireless_combines_sources_and_caches(monkeypatch):
    wireless.reset_cache()
    import taskbar_battery_widget.hid_win as hw
    import taskbar_battery_widget.hidpp as hp
    import taskbar_battery_widget.razer as rz
    infos = [SimpleNamespace(vid=0x046D, pid=0xC539)]
    monkeypatch.setattr(hw, "enumerate_hid", lambda v=None: infos)
    monkeypatch.setattr(hp, "scan_logitech", lambda i, cancel=None: [dev("logi:c539:01", battery=70)])

    def razer_down(i, cancel=None):
        raise RuntimeError("razer down")

    monkeypatch.setattr(rz, "scan_razer", razer_down)
    assert [d.id for d in real_scan()] == ["logi:c539:01"]
    monkeypatch.setattr(hp, "scan_logitech", lambda i, cancel=None: [])
    assert [d.battery for d in real_scan()] == [70]       # cached after a miss
    wireless.reset_cache()


def test_monitor_emits_wireless_devices(qapp, monkeypatch):
    from taskbar_battery_widget import scanner
    from taskbar_battery_widget.monitor import DeviceMonitor

    async def radio_on():
        return True

    async def no_bt():
        return []

    monkeypatch.setattr(scanner, "is_radio_on", radio_on)     # hermetic: no real WinRT in the thread
    monkeypatch.setattr(scanner, "scan", no_bt)
    got = []
    mon = DeviceMonitor(use_watchers=False, wireless_scan=lambda: [dev("logi:c539:01", battery=72)])
    mon.devices_changed.connect(got.append)
    mon.start()
    try:
        end = time.monotonic() + 8
        while time.monotonic() < end and not any(d.id == "logi:c539:01" for lst in got for d in lst):
            QCoreApplication.processEvents()
            time.sleep(0.02)
    finally:
        mon.stop()
    assert got and any(d.id == "logi:c539:01" and d.battery == 72 for d in got[-1])


def test_monitor_respects_wireless_disabled(qapp):
    from taskbar_battery_widget.monitor import DeviceMonitor

    class Cfg:
        def get(self, key):
            return False

    def fail():
        pytest.fail("wireless scan must not run when disabled")

    mon = DeviceMonitor(use_watchers=False, cfg=Cfg(), wireless_scan=fail)
    assert asyncio.run(mon._scan_wireless()) == []


# ---------------------------------------------------------------- idle grace (sleeping devices)
def test_absent_device_within_grace_is_idle_with_last_battery():
    cache, present, grace = {}, {"046d:c539"}, 1800.0
    wireless.apply_cache(cache, [dev("logi:c539:01", kind="mouse", battery=64)], present, 0.0, grace=grace)
    out = wireless.apply_cache(cache, [], present, 60.0, grace=grace)      # within ttl: not idle yet
    assert [(x.battery, x.idle) for x in out] == [(64, False)]
    out = wireless.apply_cache(cache, [], present, 600.0, grace=grace)
    assert [(x.battery, x.idle, x.connected) for x in out] == [(64, True, True)]
    assert out[0].updated is not None
    assert cache["logi:c539:01"][0].idle is False                        # cache itself stays clean


def test_idle_device_dropped_after_grace_and_when_receiver_gone():
    cache, present = {}, {"046d:c539"}
    wireless.apply_cache(cache, [dev("logi:c539:01")], present, 0.0, grace=300.0)
    assert wireless.apply_cache(cache, [], present, 301.0, grace=300.0) == []
    assert cache == {}
    wireless.apply_cache(cache, [dev("logi:c539:01")], present, 0.0, grace=1800.0)
    assert wireless.apply_cache(cache, [], set(), 600.0, grace=1800.0) == []


def test_idle_device_wakes_up_with_fresh_reading():
    cache, present = {}, {"046d:c539"}
    wireless.apply_cache(cache, [dev("logi:c539:01", battery=64)], present, 0.0, grace=1800.0)
    assert wireless.apply_cache(cache, [], present, 600.0, grace=1800.0)[0].idle
    out = wireless.apply_cache(cache, [dev("logi:c539:01", battery=63)], present, 700.0, grace=1800.0)
    assert [(x.battery, x.idle) for x in out] == [(63, False)]


def test_set_idle_grace_min(monkeypatch):
    monkeypatch.setattr(wireless, "_idle_grace_s", wireless._idle_grace_s)
    wireless.set_idle_grace_min(5)
    assert wireless._idle_grace_s == 300.0
