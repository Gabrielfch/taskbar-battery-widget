"""Shared fixtures/helpers for the wireless.scan_wireless wiring tests (fake enumeration, fake readers)."""
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import (audeze, aula, corsair, hid_win, hidpp, hyperx, jabra,
                                    logitech_headsets, misc_headsets, sony, steelseries, wireless, xinput)
from taskbar_battery_widget.models import Device

real_scan = wireless.scan_wireless

EXTRA_ONLY = {"steelseries", "corsair", "audeze", "jabra", "sony", "misc", "aula"}


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
    monkeypatch.setattr(wireless, "_beta", True)
    monkeypatch.setattr(wireless, "_hid_enabled", True)
    monkeypatch.setattr(wireless, "_bluetooth", True)
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
    reader("aula", aula)
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
    wireless.set_beta_readers(False)
    wireless._inflight.clear()
    wireless.reset_cache()


def names(calls):
    return [c[0] for c in calls]
