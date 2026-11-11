"""wireless.scan_wireless wiring of the vendor readers (fake enumeration, fake readers)."""
from types import SimpleNamespace

import threading
import time

import pytest

from taskbar_battery_widget import (audeze, corsair, hid_win, hidpp, hyperx, jabra, logitech_headsets,
                                    misc_headsets, sony, steelseries, wireless, xinput)
from taskbar_battery_widget.models import Device

real_scan = wireless.scan_wireless

EXPERIMENTAL_ONLY = {"steelseries", "corsair", "audeze", "jabra", "sony", "misc"}


def info(vid, pid=0x0001):
    return SimpleNamespace(vid=vid, pid=pid, path=f"hid#{vid:04x}_{pid:04x}", usage_page=0,
                           usage=0, in_len=0, out_len=0, feat_len=0, product="")


def dev(id_, name="Dev", kind="headset", battery=50, link="2.4ghz", connected=True):
    return Device(id=id_, name=name, kind=kind, battery=battery, connected=connected, link=link)


@pytest.fixture
def rig(monkeypatch):
    """Record which readers run; fake readers return nothing."""
    wireless.reset_cache()
    wireless._inflight.clear()
    monkeypatch.setattr(wireless, "_experimental", True)
    calls = []
    state = {"infos": [], "vids": None}

    def enum(v=None):
        state["vids"] = v
        return [i for i in state["infos"] if v is None or i.vid in v]

    monkeypatch.setattr(hid_win, "enumerate_hid", enum)

    def reader(name, mod, attr="read"):
        def fn(*a, **kw):
            calls.append((name, [(i.vid, i.pid) for i in a[0]]))
            return []
        monkeypatch.setattr(mod, attr, fn)

    reader("steelseries", steelseries)
    reader("corsair", corsair)
    reader("audeze", audeze)
    reader("jabra", jabra)
    reader("sony", sony)
    reader("misc", misc_headsets)
    reader("lgh", logitech_headsets)
    reader("hyperx", hyperx, "read_hyperx")
    reader("xinput", xinput)
    import taskbar_battery_widget.dualsense as ds
    import taskbar_battery_widget.razer as rz
    import taskbar_battery_widget.razer_odessa as od
    monkeypatch.setattr(hidpp, "scan_logitech", lambda i, cancel=None: calls.append(("hidpp", [])) or [])
    monkeypatch.setattr(rz, "scan_razer", lambda i, cancel=None: calls.append(("razer", [])) or [])
    monkeypatch.setattr(od, "read_odessa", lambda *a, **k: calls.append(("odessa", [])) or [])
    monkeypatch.setattr(ds, "read_dualsense", lambda *a, **k: calls.append(("ds", [])) or [])
    yield state, calls
    wireless.set_experimental_readers(False)
    wireless._inflight.clear()
    wireless.reset_cache()


def names(calls):
    return [c[0] for c in calls]


def test_unrelated_hid_runs_no_vendor_reader(rig):
    state, calls = rig
    state["infos"] = [info(0x9999)]
    real_scan()
    assert (EXPERIMENTAL_ONLY | {"hyperx"}).isdisjoint(names(calls))


@pytest.mark.parametrize("vid,reader", [
    (0x1038, "steelseries"), (0x1B1C, "corsair"), (0x3329, "audeze"), (0x0B0E, "jabra"),
    (0x054C, "sony"), (0x17EF, "misc"), (0x3837, "misc"), (0x291D, "misc"),
    (0x0951, "hyperx"), (0x03F0, "hyperx"),
])
def test_reader_called_only_when_vid_enumerated(rig, vid, reader):
    state, calls = rig
    state["infos"] = [info(vid, 0x16C4 if vid == 0x0951 else 0x0696 if vid == 0x03F0 else 1)]
    real_scan()
    assert reader in names(calls)
    assert ((EXPERIMENTAL_ONLY | {"hyperx"}) - {reader}).isdisjoint(names(calls))


def test_xinput_always_runs(rig):
    state, calls = rig
    state["infos"] = [info(0x9999)]
    real_scan()
    assert "xinput" in names(calls)


def test_logitech_headsets_run_after_scan_logitech(rig):
    state, calls = rig
    state["infos"] = [info(0x046D, 0xC539)]
    real_scan()
    n = names(calls)
    assert n.index("hidpp") < n.index("lgh")


def test_bt600_is_not_handled_at_all(rig):
    state, calls = rig
    state["infos"] = [info(0x047F, 0x02EE)]
    real_scan()
    assert "misc" not in names(calls)
    assert 0x047F not in wireless.VENDOR_IDS
    assert not hasattr(misc_headsets, "SCAN_EXCLUDED")
    state["infos"] = [info(0x047F, 0x02EE), info(0x17EF, 0xA07D)]
    calls.clear()
    real_scan()
    misc_calls = [c for c in calls if c[0] == "misc"]
    assert misc_calls and misc_calls[0][1] == [(0x17EF, 0xA07D)]


def test_experimental_off_runs_only_verified_readers(rig):
    state, calls = rig
    wireless.set_experimental_readers(False)
    state["infos"] = [info(v, 0x16C4 if v == 0x0951 else 1) for v in
                      (0x1038, 0x1B1C, 0x3329, 0x0B0E, 0x054C, 0x17EF, 0x0951, 0x046D, 0x03F0)]
    real_scan()
    n = set(names(calls))
    assert (EXPERIMENTAL_ONLY | {"xinput", "lgh"}).isdisjoint(n)
    assert {"hidpp", "razer", "odessa", "ds", "hyperx"} <= n
    assert set(state["vids"]) == set(wireless.CORE_VENDOR_IDS)


def test_experimental_off_hyperx_only_flight(rig):
    state, calls = rig
    wireless.set_experimental_readers(False)
    state["infos"] = [info(0x0951, 0x1723), info(0x0951, 0x16C4)]
    real_scan()
    assert [c[1] for c in calls if c[0] == "hyperx"] == [[(0x0951, 0x16C4)]]


def test_xinput_wired_dropped_and_bt_dedupe(monkeypatch):
    wireless.reset_cache()
    monkeypatch.setattr(wireless, "_experimental", True)
    monkeypatch.setattr(xinput, "_query", lambda i: {0: (1, 0), 1: (2, 2)}.get(i))
    monkeypatch.setattr(hid_win, "enumerate_hid", lambda v=None: [])
    out = real_scan()
    assert [d.id for d in out] == ["xinput:1"]          # wired slot 0 has no battery: dropped
    bt = [dev("bt:x", "Xbox Wireless Controller", "gamepad", 80, link="bluetooth")]
    assert [d.id for d in wireless.merge_devices(bt, out)] == ["bt:x"]
    assert [d.id for d in wireless.merge_devices([], out)] == ["xinput:1"]
    wireless.reset_cache()


def test_xinput_never_idle_cached(monkeypatch):
    wireless.reset_cache()
    monkeypatch.setattr(wireless, "_experimental", True)
    monkeypatch.setattr(hid_win, "enumerate_hid", lambda v=None: [info(0x046D)])
    monkeypatch.setattr(hidpp, "scan_logitech", lambda i, cancel=None: [])
    monkeypatch.setattr(xinput, "_query", lambda i: (2, 2) if i == 0 else None)
    assert [d.id for d in real_scan()] == ["xinput:0"]
    monkeypatch.setattr(xinput, "_query", lambda i: None)
    assert real_scan() == []
    wireless.reset_cache()


@pytest.mark.parametrize("dev_id,key", [
    ("steelseries:12e0", "1038:12e0"), ("corsair:0a2b", "1b1c:0a2b"), ("audeze:4b19", "3329:4b19"),
    ("jabra:2e56", "0b0e:2e56"), ("sony:0ce6", "054c:0ce6"), ("sony:0d5e", "054c:0d5e"),
    ("sony:0ebf", "054c:0ebf"), ("sony:0ec2", "054c:0ec2"),
    ("logi:0af7", "046d:0af7"), ("logi:c539:01", "046d:c539"),
    ("hyperx:16c4", "0951:16c4"), ("hyperx:1723", "0951:1723"), ("hyperx:1718", "0951:1718"),
    ("hyperx:0696", "03f0:0696"), ("hyperx:018b", "03f0:018b"), ("hyperx:098d", "03f0:098d"),
    ("hyperx:08be", "03f0:08be"),
    ("lenovo:a07d", "17ef:a07d"), ("mchose:6045", "3837:6045"), ("mchose:385d", "291d:385d"),
    ("razer:0565", "1532:0565"),
])
def test_dongle_key_every_prefix(dev_id, key):
    assert wireless.dongle_key(dev_id) == key


def test_dongle_key_xinput_has_no_dongle():
    assert wireless.dongle_key("xinput:0") is None


def test_push_only_sony_devices_survive_idle_grace():
    cache = {}
    both = {"054c:0d5e", "054c:0ec2"}
    for dev_id in ("sony:0d5e", "sony:0ec2"):
        wireless.apply_cache(cache, [dev(dev_id, battery=64)], both, 0.0)
    far = wireless.IDLE_GRACE_DEFAULT_S * 10
    out = wireless.apply_cache(cache, [], {"054c:0d5e", "054c:0ec2"}, far,
                               grace=wireless.IDLE_GRACE_DEFAULT_S)
    assert sorted((d.id, d.battery, d.idle) for d in out) == [("sony:0d5e", 64, True),
                                                              ("sony:0ec2", 64, True)]
    assert wireless.apply_cache(cache, [], set(), far + 1) == []     # dongle gone: dropped


def test_other_devices_still_expire():
    cache = {}
    wireless.apply_cache(cache, [dev("corsair:0a2b", battery=40)], {"1b1c:0a2b"}, 0.0)
    assert wireless.apply_cache(cache, [], {"1b1c:0a2b"}, 10_000.0, grace=1800.0) == []


def test_config_experimental_validated(tmp_path):
    from taskbar_battery_widget.config import Config
    c = Config(tmp_path / "c.json")
    assert c.get("experimental_readers") is False           # opt-in
    c.set("experimental_readers", True)
    assert c.get("experimental_readers") is True


def test_module_default_is_off():
    import importlib
    src = open(wireless.__file__, encoding="utf-8").read()
    assert "_experimental = False" in src


# ---------------------------------------------------------------- isolation of slow readers
def _dev_logi(id_="logi:c539:01", battery=70, name="G903", kind="mouse"):
    return dev(id_, name, kind, battery)


def test_slow_headset_reader_never_drops_core_logitech(rig, monkeypatch):
    state, _calls = rig
    state["infos"] = [info(0x046D, 0xC539), info(0x046D, 0x0A66)]

    def slow_core(i, cancel=None):
        time.sleep(1.2)
        return [_dev_logi()]

    def slow_headsets(*a, **k):
        time.sleep(1.5)
        return [dev("logi:0a66", "G533")]
    monkeypatch.setattr(hidpp, "scan_logitech", slow_core)
    monkeypatch.setattr(logitech_headsets, "read", slow_headsets)
    t = time.monotonic()
    out = real_scan()
    assert [d.id for d in out] == ["logi:c539:01"]
    assert time.monotonic() - t < wireless.VENDOR_BUDGET_S + 0.8


def test_hung_headset_reader_does_not_block_next_cycle(rig, monkeypatch):
    state, _calls = rig
    state["infos"] = [info(0x046D, 0xC539), info(0x046D, 0x0A66)]
    release = threading.Event()
    seen = {}

    def core(i, cancel=None):
        return [_dev_logi()]

    def hung(infos, deadline, cancel=None, **k):
        seen["cancel"] = cancel
        release.wait(10)
        return []
    monkeypatch.setattr(hidpp, "scan_logitech", core)
    monkeypatch.setattr(logitech_headsets, "read", hung)
    monkeypatch.setattr(wireless, "VENDOR_BUDGET_S", 0.8)
    try:
        for _ in range(2):
            wireless.reset_cache()
            t = time.monotonic()
            assert [d.id for d in real_scan()] == ["logi:c539:01"]
            assert time.monotonic() - t < 2.5
        assert seen["cancel"] is not None
    finally:
        release.set()
        wireless._inflight.clear()


def test_headset_cancel_token_is_not_the_core_one(rig, monkeypatch):
    state, _calls = rig
    state["infos"] = [info(0x046D, 0xC539), info(0x046D, 0x0A66)]
    tokens = {}
    monkeypatch.setattr(hidpp, "scan_logitech",
                        lambda i, cancel=None: tokens.__setitem__("core", cancel) or [])
    monkeypatch.setattr(logitech_headsets, "read",
                        lambda i, d, cancel=None, **k: tokens.__setitem__("hs", cancel) or [])
    real_scan()
    assert tokens["core"] is not tokens["hs"]


def test_slow_experimental_enumeration_does_not_stop_core_readers(rig, monkeypatch):
    state, calls = rig
    state["infos"] = [info(0x046D, 0xC539), info(0x1B1C, 0x0A14)]
    release = threading.Event()

    def enum(v=None):
        if v is not None and 0x1B1C in v:
            release.wait(10)
        return [i for i in state["infos"] if v is None or i.vid in v]
    monkeypatch.setattr(hid_win, "enumerate_hid", enum)
    monkeypatch.setattr(wireless, "ENUM_BUDGET_S", 0.3)
    monkeypatch.setattr(hidpp, "scan_logitech", lambda i, cancel=None: [_dev_logi()])
    try:
        out = real_scan()
        assert [d.id for d in out] == ["logi:c539:01"]
        assert "corsair" not in names(calls)
    finally:
        release.set()
        wireless._inflight.clear()


def test_core_vendors_enumerated_without_experimental_ids(rig):
    state, _ = rig
    seen = []
    orig = hid_win.enumerate_hid
    hid_win.enumerate_hid = lambda v=None: seen.append(tuple(v)) or []
    try:
        wireless.set_experimental_readers(False)
        real_scan()
        assert seen == [wireless.CORE_VENDOR_IDS]
        seen.clear()
        wireless.set_experimental_readers(True)
        real_scan()
        assert set(seen) == {wireless.CORE_VENDOR_IDS, wireless.EXPERIMENTAL_VENDOR_IDS}
    finally:
        hid_win.enumerate_hid = orig


# ---------------------------------------------------------------- cache purge / dedupe
def test_toggling_experimental_off_purges_cached_experimental_ids():
    wireless.reset_cache()
    cache_ids = ["corsair:0a14", "sony:0d5e", "sony:0ec2", "xinput:0", "hyperx:1718", "jabra:2e56",
                 "logi:0a66"]
    keep = ["logi:c539:01", "sony:0ce6", "hyperx:16c4", "razer:0565"]
    try:
        with wireless._lock:
            for i in cache_ids + keep:
                wireless._cache[i] = (dev(i), time.monotonic())
        wireless.set_experimental_readers(False)
        assert sorted(wireless._cache) == sorted(keep)
    finally:
        wireless.reset_cache()


def test_flapping_logitech_headset_is_deduplicated():
    generic = dev("logi:0a66:ff", "G533 Wireless Headset", battery=60)
    headset = dev("logi:0a66", "Logitech G533", battery=60)
    assert [d.id for d in wireless.dedupe_logitech([generic, headset])] == ["logi:0a66:ff"]
    assert [d.id for d in wireless.dedupe_logitech([headset, generic])] == ["logi:0a66:ff"]
    no_batt = dev("logi:0a66:ff", "G533", battery=None)
    assert [d.id for d in wireless.dedupe_logitech([no_batt, headset])] == ["logi:0a66"]
    other = dev("logi:0aa7", "G PRO", battery=50)
    assert len(wireless.dedupe_logitech([generic, other])) == 2


def test_headset_reader_skips_pids_the_generic_scanner_returned(rig, monkeypatch):
    state, _ = rig
    state["infos"] = [info(0x046D, 0x0A66)]
    got = {}
    monkeypatch.setattr(hidpp, "scan_logitech",
                        lambda i, cancel=None: [dev("logi:0a66:ff", "G533", battery=None)])
    monkeypatch.setattr(logitech_headsets, "read",
                        lambda infos, d, cancel=None, **k: got.__setitem__("n", len(infos)) or [])
    real_scan()
    assert "n" not in got
