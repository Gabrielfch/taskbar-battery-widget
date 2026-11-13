"""Disabled sources must not cause device I/O (not just filtered output)."""
from types import SimpleNamespace

from taskbar_battery_widget import hid_win, wireless
from taskbar_battery_widget.models import Device
from tests.test_wireless_wiring import names, real_scan, rig  # noqa: F401  (rig is a fixture)

BT_PATH = "\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_dev"
USB_PATH = "\\\\?\\hid#vid_054c&pid_0ce6#usb"


def ds_info(path, in_len):
    return SimpleNamespace(vid=0x054C, pid=0x0CE6, path=path, usage_page=1, usage=5,
                           in_len=in_len, out_len=0, feat_len=41, product="")


def test_bluetooth_off_does_not_pass_bt_dualsense_to_reader(rig, monkeypatch):
    state, calls = rig
    import taskbar_battery_widget.dualsense as ds
    seen = []
    monkeypatch.setattr(ds, "read_dualsense", lambda sel, *a, **k: seen.append([i.path for i in sel]) or [])
    state["infos"] = [ds_info(BT_PATH, 78), ds_info(USB_PATH, 64)]
    wireless.set_sources(True, False)
    real_scan()
    assert seen == [[USB_PATH]]
    seen.clear()
    wireless.set_sources(False, True)
    real_scan()
    assert seen == [[BT_PATH]]
    wireless.set_sources(True, True)


def test_bluetooth_off_never_opens_bt_handle_with_real_reader(monkeypatch):
    """Real dualsense reader, fake handle: no open / get_feature for the BT info."""
    wireless.reset_cache()
    wireless._inflight.clear()
    opened = []

    class H:
        def __init__(self, path, **kw):
            opened.append(path)
            raise OSError("no device")
    monkeypatch.setattr(hid_win, "HidHandle", H, raising=False)
    monkeypatch.setattr(hid_win, "enumerate_hid",
                        lambda v=None: [ds_info(BT_PATH, 78), ds_info(USB_PATH, 64)])
    monkeypatch.setattr(wireless, "_beta", False)
    try:
        wireless.set_sources(True, False)
        real_scan()
        assert opened and all("00001124" not in p for p in opened)
        opened.clear()
        wireless.reset_cache()
        wireless.set_sources(False, True)
        real_scan()
        assert opened and all("00001124" in p for p in opened)
    finally:
        wireless.set_sources(True, True)
        wireless.reset_cache()


def test_dongles_off_skips_usb_dualsense_and_xinput(rig, monkeypatch):
    state, calls = rig
    import taskbar_battery_widget.dualsense as ds
    seen = []
    monkeypatch.setattr(ds, "read_dualsense", lambda sel, *a, **k: seen.append(len(sel)) or [])
    state["infos"] = [ds_info(USB_PATH, 64)]
    wireless.set_sources(False, True)
    real_scan()
    assert not any(seen)
    assert "xinput" not in names(calls)
    wireless.set_sources(True, True)
    real_scan()
    assert seen[-1] == 1 and "xinput" in names(calls)


def test_is_beta_device_ignores_foreign_ids():
    for foreign in ("BTHENUM\\DEV_046d0af7", "046d:0af7", "xinput:0", "bt:1b1c:2a08", "unknown:0af7"):
        d = Device(id=foreign, name="x", kind="headset", battery=50, connected=True, link="bluetooth")
        assert not wireless.is_beta_device(d), foreign
