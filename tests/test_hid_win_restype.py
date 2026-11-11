import ctypes
import sys
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import hid_win

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 HID layer")

USB = r"\?\hid#vid_046d&pid_c539&mi_02#7&1&0&0000#{x}"
BT = r"\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002046d_pid&b034#8&1&0&0000#{x}"


def test_ok_looks_only_at_the_low_byte():
    assert hid_win._ok(0x4A3BB000) is False         # stale upper bytes, BOOLEAN FALSE
    assert hid_win._ok(0x4A3BB001) is True
    assert hid_win._ok(1) is True and hid_win._ok(0) is False


@win_only
def test_every_hidd_function_declares_a_boolean_restype():
    from ctypes import wintypes
    for name in ("HidD_GetAttributes", "HidD_GetPreparsedData", "HidD_FreePreparsedData",
                 "HidD_GetProductString", "HidD_SetFeature", "HidD_GetFeature",
                 "HidD_GetInputReport"):
        assert getattr(hid_win._hid, name).restype is wintypes.BOOLEAN, name
    assert hid_win._hid.HidP_GetCaps.restype is ctypes.c_long


class FakeHid:
    def __init__(self, attr_ret=1):
        self.attr_ret, self.product_calls = attr_ret, 0

    def HidD_GetAttributes(self, h, ref):
        a = ref._obj
        a.VendorID, a.ProductID = 0x046D, 0xC539
        return self.attr_ret

    def HidD_GetPreparsedData(self, h, ref):
        return 0

    def HidD_FreePreparsedData(self, pp):
        return 1

    def HidD_GetProductString(self, h, buf, size):
        self.product_calls += 1
        buf.value = "Product"
        return 1


@pytest.fixture
def fake(monkeypatch):
    hid_win._info_cache.clear()
    state = SimpleNamespace(paths=[USB, BT], opens=[], hid=FakeHid())
    monkeypatch.setattr(hid_win, "_WIN", True)
    monkeypatch.setattr(hid_win, "_interface_paths", lambda: list(state.paths))
    monkeypatch.setattr(hid_win, "_open_raw", lambda p, a, o: state.opens.append(p) or 1)
    monkeypatch.setattr(hid_win, "_k32", SimpleNamespace(CloseHandle=lambda h: None), raising=False)
    monkeypatch.setattr(hid_win, "_hid", state.hid, raising=False)
    yield state
    hid_win._info_cache.clear()


def test_false_with_garbage_upper_bytes_is_a_failure(fake):
    fake.hid.attr_ret = 0x4A3BB000
    assert hid_win.enumerate_hid() == []


def test_bluetooth_product_string_not_queried_and_infos_cached(fake):
    out = hid_win.enumerate_hid()
    by_path = {i.path: i for i in out}
    assert by_path[USB].product == "Product" and by_path[BT].product == ""
    assert fake.hid.product_calls == 1                  # USB only
    opened = len(fake.opens)
    again = hid_win.enumerate_hid()
    assert len(fake.opens) == opened and fake.hid.product_calls == 1     # served from cache
    assert [i.path for i in again] == [i.path for i in out]
    again[0].product = "mutated"                        # the cache hands out copies
    assert hid_win.enumerate_hid()[0].product != "mutated"
    fake.paths = [BT]                                   # USB device unplugged: entry dropped
    hid_win.enumerate_hid()
    assert USB not in hid_win._info_cache
    fake.paths = [USB, BT]
    hid_win.enumerate_hid()
    assert fake.hid.product_calls == 2                  # replugged: probed again
