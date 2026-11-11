import threading
import time

from taskbar_battery_widget import xinput
from taskbar_battery_widget.models import Device


def run(monkeypatch, table):
    monkeypatch.setattr(xinput, "_query", lambda i: table.get(i))
    return xinput.read([], time.monotonic() + 1, None)


def test_levels(monkeypatch):
    d = run(monkeypatch, {0: (2, 0), 1: (3, 1), 2: (2, 2), 3: (3, 3)})
    assert [x.battery for x in d] == [5, 30, 65, 100]
    assert [x.id for x in d] == ["xinput:0", "xinput:1", "xinput:2", "xinput:3"]
    assert d[0].name == "Xbox Controller" and d[0].kind == "gamepad" and d[0].link == "2.4ghz"


def test_wired_and_disconnected_and_unknown(monkeypatch):
    d = run(monkeypatch, {0: (1, 3), 1: (0, 0), 2: (0xFF, 3)})
    assert [(x.id, x.battery, x.link) for x in d] == [("xinput:0", None, "usb"), ("xinput:2", None, "2.4ghz")]


def test_query_error_and_cancel(monkeypatch):
    def q(i):
        if i == 0:
            raise OSError("boom")
        return (2, 3)
    monkeypatch.setattr(xinput, "_query", q)
    assert [x.id for x in xinput.read([], time.monotonic() + 1, None)] == ["xinput:1", "xinput:2", "xinput:3"]
    ev = threading.Event()
    ev.set()
    assert xinput.read([], time.monotonic() + 1, ev) == []
    assert xinput.read([], time.monotonic() - 1, None) == []


def test_dll_function_mocked(monkeypatch):
    def fn(user, devtype, ref):
        assert devtype == 0
        if user == 1:
            ref._obj.BatteryType, ref._obj.BatteryLevel = 3, 2
            return 0
        return 1167
    monkeypatch.setattr(xinput, "_fn", fn)
    d = xinput.read([], time.monotonic() + 1, None)
    assert [(x.id, x.battery) for x in d] == [("xinput:1", 65)]


def test_no_dll(monkeypatch):
    monkeypatch.setattr(xinput, "_fn", None)
    monkeypatch.setattr(xinput, "_load", lambda: None)
    assert xinput.read([], time.monotonic() + 1, None) == []


def test_drop_bluetooth_duplicates():
    x = [Device("xinput:0", "Xbox Controller", "gamepad", 65, True, link="2.4ghz"),
         Device("xinput:1", "Xbox Controller", "gamepad", None, True, link="usb"),
         Device("xinput:2", "Xbox Controller", "gamepad", 30, True, link="2.4ghz")]
    bt = Device("bt:1", "Xbox Wireless Controller", "gamepad", 80, True, link="bluetooth")
    assert [d.id for d in xinput.drop_bluetooth_duplicates(x, [bt])] == ["xinput:1", "xinput:2"]
    nobat = Device("bt:1", "Xbox Wireless Controller", "gamepad", None, True, link="bluetooth")
    assert len(xinput.drop_bluetooth_duplicates(x, [nobat])) == 3
    assert len(xinput.drop_bluetooth_duplicates(x, [])) == 3


def test_constants():
    assert xinput.VENDOR_IDS == ()


def test_dll_loaded_from_system32_by_full_path(monkeypatch):
    import ctypes
    import os
    seen = []

    def fake(path, winmode=None):
        seen.append((path, winmode))
        raise OSError("x")
    monkeypatch.setattr(xinput, "_fn", None)
    monkeypatch.setattr(xinput.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", fake, raising=False)
    assert xinput._load() is None
    assert seen and all(os.path.isabs(p) and p.lower().split(os.sep)[-2] == "system32" and m == 0x800
                        for p, m in seen)
