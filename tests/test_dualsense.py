import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import dualsense as ds
from taskbar_battery_widget import hid_win, wireless
from taskbar_battery_widget.models import Device

USB_PATH = r"\\?\hid#vid_054c&pid_0ce6#7&1&0&0000#{4d1e55b2}"
BT_PATH = r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&0ce6#8&1&0&0000#{4d1e55b2}"


def usb_report(status):
    r = bytearray(64)
    r[0] = 0x01
    r[53] = status
    return bytes(r)


def bt_extended(status):
    r = bytearray(78)
    r[0] = 0x31
    r[54] = status
    return bytes(r)


BT_SIMPLE = bytes([0x01]) + bytes(77)


def info(path=USB_PATH, n=64, pid=0x0CE6, page=1, usage=5, product="Wireless Controller"):
    return SimpleNamespace(vid=0x054C, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=n, out_len=63, feat_len=64, product=product)


class FakeHandle:
    """Serves `before` reports, then `after` once get_feature was called (or always `before`)."""

    def __init__(self, before, after=()):
        self.before, self.after = list(before), list(after)
        self.calls = []
        self.triggered = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        self.calls.append("drain")

    def get_feature(self, rid, length):
        self.calls.append(("get_feature", rid, length))
        self.triggered = True
        return bytes(length)

    def write(self, *a, **k):
        raise AssertionError("write must never be called")

    def set_feature(self, *a, **k):
        raise AssertionError("set_feature must never be called")

    def read(self, timeout_ms=0):
        self.calls.append("read")
        q = self.after if self.triggered else self.before
        if q:
            return q.pop(0) if len(q) > 1 or not self.triggered else q[0]
        time.sleep(min(timeout_ms, 10) / 1000)
        return None


@pytest.fixture(autouse=True)
def _reset():
    ds._triggered.clear()
    yield
    ds._triggered.clear()


def run(h, infos=None, budget=2.0, cancel=None, ext=True):
    return ds.read_dualsense(infos or [info()], time.monotonic() + budget, cancel,
                             enable_bt_extended=ext, handle_factory=lambda p, in_len=64: h)


@pytest.mark.parametrize("b,exp", [
    (0x11, (15, True, False)), (0x12, (25, True, False)), (0x00, (5, False, False)),
    (0x0A, (100, False, False)), (0x09, (95, False, False)), (0x2A, (100, False, True)),
    (0x20, (100, False, True)), (0xA3, (35, False, False)), (0xB5, (55, False, False)),
    (0xFF, (100, False, False)), (0x1A, (100, True, False)),
])
def test_decode_status(b, exp):
    assert ds.decode_status(b) == exp


def test_parse_report():
    assert ds.parse_report(usb_report(0x11), "usb") == 0x11
    assert ds.parse_report(bt_extended(0x22), "bluetooth") == 0x22
    assert ds.parse_report(BT_SIMPLE, "bluetooth") is None
    assert ds.parse_report(bytes([0x01]) + bytes(10), "usb") is None
    assert ds.parse_report(bytes([0x31]) + bytes(10), "bluetooth") is None
    assert ds.parse_report(usb_report(0x11), "bluetooth") is None
    assert ds.parse_report(None, "usb") is None


def test_usb_read():
    h = FakeHandle([usb_report(0x11)])
    [d] = run(h)
    assert (d.id, d.name, d.kind, d.battery, d.charging, d.link, d.source, d.connected) == \
        ("sony:0ce6", "Wireless Controller", "gamepad", 15, True, "usb", "hid", True)
    assert not any(isinstance(c, tuple) for c in h.calls)


def test_default_name_and_edge_pid():
    [d] = run(FakeHandle([usb_report(0x05)]), [info(pid=0x0DF2, product="")])
    assert d.id == "sony:0df2" and d.name == "DualSense Wireless Controller" and d.battery == 55


def test_bt_simple_triggers_one_feature_then_extended():
    h = FakeHandle([BT_SIMPLE] * 50, [bt_extended(0x03)])
    [d] = run(h, [info(BT_PATH, 78)])
    assert (d.battery, d.charging, d.link) == (35, False, "bluetooth")
    assert [c for c in h.calls if isinstance(c, tuple)] == [("get_feature", 5, 64)]
    # same connection again: never re-triggers
    h2 = FakeHandle([BT_SIMPLE] * 50, [bt_extended(0x03)])
    assert run(h2, [info(BT_PATH, 78)], budget=1.0) == []
    assert not any(isinstance(c, tuple) for c in h2.calls)
    # extended mode persists: plain reading works without a trigger
    h3 = FakeHandle([bt_extended(0x13)])
    [d3] = run(h3, [info(BT_PATH, 78)])
    assert d3.battery == 35 and d3.charging
    assert not any(isinstance(c, tuple) for c in h3.calls)


def test_bt_retrigger_after_device_disappears():
    run(FakeHandle([BT_SIMPLE] * 50, [bt_extended(0x03)]), [info(BT_PATH, 78)])
    assert BT_PATH in ds._triggered
    run(FakeHandle([]), [info("other", 64, pid=0x1234)])
    assert BT_PATH not in ds._triggered
    h = FakeHandle([BT_SIMPLE] * 50, [bt_extended(0x03)])
    run(h, [info(BT_PATH, 78)])
    assert ("get_feature", 5, 64) in h.calls


def test_toggle_off_never_requests_feature():
    h = FakeHandle([BT_SIMPLE] * 50, [bt_extended(0x03)])
    assert run(h, [info(BT_PATH, 78)], budget=1.5, ext=False) == []
    assert not any(isinstance(c, tuple) for c in h.calls)


def test_whitelist_blocks_other_calls():
    w = ds._ReadOnly(FakeHandle([]))
    with pytest.raises(ValueError):
        w.get_feature(0x09, 64)
    for name in ("write", "set_feature", "get_input_report"):
        assert not hasattr(w, name)


def test_ignores_non_gamepad_collection_and_other_vendors():
    h = FakeHandle([usb_report(0x11)])
    assert run(h, [info(page=0xFF00, usage=1), info(usage=4)]) == []
    other = info()
    other.vid = 0x046D
    assert run(h, [other]) == []


def test_deadline_and_cancel():
    h = FakeHandle([])
    t = time.monotonic()
    assert run(h, budget=0.3) == []
    assert time.monotonic() - t < 1.0
    c = threading.Event()
    c.set()
    h2 = FakeHandle([usb_report(0x11)])
    assert run(h2, cancel=c) == []
    assert "read" not in h2.calls


def test_never_raises():
    def boom(path, in_len=64):
        raise OSError("nope")
    assert ds.read_dualsense([info()], time.monotonic() + 1, None, handle_factory=boom) == []
    assert ds.read_dualsense(None, time.monotonic() + 1) == []


def test_path_filter_accepts_usb_and_bluetooth():
    f = hid_win._path_matches_vendor
    assert f(USB_PATH, (0x054C,))
    assert f(BT_PATH, (0x054C,))
    assert f(r"\\?\hid#vid&0001054c_pid&0ce6#x", (0x054C,))
    assert not f(r"\\?\hid#vid_046d&pid_c539#x", (0x054C,))
    assert not f(BT_PATH, (0x046D, 0x1532))


def test_dongle_key_and_vendor():
    assert wireless.dongle_key("sony:0ce6") == "054c:0ce6"
    assert 0x054C in wireless.VENDOR_IDS


def test_merge_replaces_bluetooth_entry_without_battery():
    bt = Device(id="bt:AA", name="DualSense Wireless Controller", kind="gamepad", battery=None,
                connected=True, address="AA")
    hid = Device(id="sony:0ce6", name="DualSense Wireless Controller", kind="gamepad", battery=15,
                 connected=True, charging=True, link="usb", source="hid")
    out = wireless.merge_devices([bt], [hid])
    assert len(out) == 1 and out[0].battery == 15 and out[0].source == "hid"
