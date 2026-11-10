"""Core (non-UI) review fixes: monitor signature, caches, privacy keys."""
import asyncio
import json
import os
import stat
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import monitor as mon, scanner, wireless
from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device, is_visibility_key, visibility_key
from taskbar_battery_widget.visibility import visible_devices


@pytest.fixture(autouse=True)
def _clear():
    wireless.reset_cache()
    yield
    wireless.reset_cache()


def D(id_="x", name="Dev", kind="headset", battery=50, connected=True, **kw):
    return Device(id_, name, kind, battery, connected, **kw)



# ---- 1. monitor signature / idle transition -------------------------------------------------
def test_signature_includes_idle_and_age_bucket():
    now = 10_000.0
    a = D(updated=now - 30)
    b = D(updated=now - 30, idle=True)
    c = D(updated=now - 90)
    sig = mon.DeviceMonitor._signature
    assert sig([a], now) != sig([b], now)
    assert sig([a], now) != sig([c], now)
    assert sig([a], now) == sig([D(updated=now - 45)], now)       # same minute bucket
    assert sig([D(updated=None)], now) == sig([D(updated=None)], now)


def test_monitor_publishes_idle_transition(qapp, monkeypatch):
    async def is_on():
        return True

    async def scan():
        return []

    monkeypatch.setattr(scanner, "is_radio_on", is_on)
    monkeypatch.setattr(scanner, "scan", scan)
    state = {"idle": False}

    def fake_wireless():
        return [D("razer:0565:a", "Mouse", "mouse", 40, True, link="2.4ghz", idle=state["idle"],
                  updated=time.time() - 10)]

    m = mon.DeviceMonitor(use_watchers=False, wireless_scan=fake_wireless)
    got = []
    m.devices_changed.connect(got.append)

    async def run():
        await m._cycle()
        assert got[-1][0].idle is False
        sig_before = m._last_sig
        state["idle"] = True
        await m._cycle()
        assert m._last_sig != sig_before        # the post-scan publish fires on idle alone

    asyncio.run(run())
    assert got[-1][0].idle is True


# ---- 13. monitor sources follow the config -------------------------------------------------
class Cfg:
    def __init__(self, **kw):
        self.d = {"wireless_enabled": True, "idle_grace_min": 30, **kw}

    def get(self, k):
        return self.d[k]


def test_monitor_hides_wireless_when_source_disabled(qapp, monkeypatch):
    async def is_on():
        return True

    async def scan():
        return []

    monkeypatch.setattr(scanner, "is_radio_on", is_on)
    monkeypatch.setattr(scanner, "scan", scan)
    hid = D("logi:c539:01", "Kbd", "keyboard", 70, link="2.4ghz")
    cfg = Cfg()
    seen = []
    m = mon.DeviceMonitor(use_watchers=False, cfg=cfg,
                          wireless_scan=lambda: seen.append(1) or [hid])
    got = []
    m.devices_changed.connect(got.append)

    async def run():
        await m._cycle()
        assert [d.id for d in got[-1]] == ["logi:c539:01"]
        cfg.d["wireless_enabled"] = False
        n = len(seen)
        await m._cycle()
        assert len(seen) == n and got[-1] == []

    asyncio.run(run())


# ---- 2. apply_cache grace ----------------------------------------------------------------
def test_apply_cache_carries_value_through_grace_and_flags_idle():
    cache, present = {}, {"046d:c539"}
    wireless.apply_cache(cache, [D("logi:c539:01", battery=70, link="2.4ghz")], present, 0.0)
    ttl = wireless.CACHE_TTL_S
    grace = 30 * 60.0
    silent = [D("logi:c539:01", battery=None, link="2.4ghz")]
    out = wireless.apply_cache(cache, silent, present, ttl + 10, grace=grace)
    assert out[0].battery == 70 and out[0].idle is True
    assert cache["logi:c539:01"][1] == 0.0                  # original stamp kept
    out = wireless.apply_cache(cache, silent, present, 30.0, grace=grace)
    assert out[0].battery == 70 and out[0].idle is False
    out = wireless.apply_cache(cache, silent, present, grace + 1, grace=grace)
    assert out[0].battery is None


# ---- 10. visibility keys / config ----------------------------------------------------------
def test_visibility_key_format_and_stability():
    k = visibility_key("bt:AA:BB:CC:DD:EE:FF")
    assert is_visibility_key(k) and len(k) == 18 and "AA:BB" not in k
    assert k == visibility_key("bt:AA:BB:CC:DD:EE:FF") != visibility_key("other")


def test_config_migrates_raw_hidden_ids_and_scrubs_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"hidden_devices": ["bt:AA:BB:CC:DD:EE:FF", visibility_key("z"),
                                                "bt:AA:BB:CC:DD:EE:FF"]}), encoding="utf-8")
    c = Config(p)
    assert c.get("hidden_devices") == [visibility_key("bt:AA:BB:CC:DD:EE:FF"), visibility_key("z")]
    assert "AA:BB" not in p.read_text(encoding="utf-8")
    assert Config(p).get("hidden_devices") == c.get("hidden_devices")


def test_config_save_uses_unique_tmp_and_cleans_up(tmp_path, monkeypatch):
    c = Config(tmp_path / "c.json")
    c.set("low_threshold", 33)
    assert [f.name for f in tmp_path.iterdir()] == ["c.json"]
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real(fd))
    c.set("low_threshold", 34)
    assert synced
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("x")))
    c.set("low_threshold", 35)                              # failure is swallowed, no tmp left
    assert [f.name for f in tmp_path.iterdir()] == ["c.json"]


def test_config_ignores_removed_synapse_keys(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"synapse_enabled": True, "synapse_stale_min": 99,
                             "low_threshold": 33}), encoding="utf-8")
    c = Config(f)
    assert c.get("low_threshold") == 33
    assert c.get("synapse_enabled") is None and c.get("synapse_stale_min") is None
    with pytest.raises(ValueError):
        c.set("synapse_enabled", True)


# ---- 14. hidden devices: no alerts -----------------------------------------------------------
def test_visible_devices_and_alerts_ignore_hidden(tmp_path):
    cfg = Config(tmp_path / "c.json")
    low = D("bt:1", "Low", "mouse", 5)
    other = D("bt:2", "Other", "mouse", 4)
    cfg.set("hidden_devices", [visibility_key("bt:1")])
    vis = visible_devices([low, other], cfg)
    assert vis == [other]
    out = AlertManager(cfg).check(vis, 0.0)
    assert len(out) == 1 and "Other" in out[0][1]
    assert visible_devices([low], SimpleNamespace(get=lambda k: 1 / 0)) == [low]
