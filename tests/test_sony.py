import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import sony

USB = r"\\?\hid#vid_054c&pid_05c4#7&1&0&0000#{x}"
BT = r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&05c4#8&1&0&0000#{x}"


def info(pid=0x05C4, path=USB, n=64, page=1, usage=5, product="Wireless Controller"):
    return SimpleNamespace(vid=0x054C, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=n, out_len=n, feat_len=0, product=product)


def ds4_usb(status, s1=0):
    r = bytearray(64)
    r[0] = 1
    r[30], r[31] = status, s1
    return bytes(r)


def ds4_bt(status):
    r = bytearray(78)
    r[0] = 0x11
    r[32] = status
    return bytes(r)


class Fake:
    def __init__(self, before=(), after=(), write_ok=True):
        self.before, self.after = list(before), list(after)
        self.writes, self.features, self.triggered, self.write_ok = [], [], False, write_ok

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def write(self, data, timeout_ms=0):
        self.writes.append(bytes(data))
        return self.write_ok

    def get_feature(self, rid, length):
        self.features.append((rid, length))
        self.triggered = True
        return bytes(length)

    def read(self, timeout_ms=0):
        q = self.after if self.triggered and self.after else self.before
        if q:
            return q.pop(0)
        time.sleep(0.005)
        return None


def run(infos, fake, **kw):
    kw.setdefault("beta", True)
    return sony.read(infos, time.monotonic() + 1.0, kw.pop("cancel", None),
                     handle_factory=lambda p, in_len=64: fake, **kw)


def hci_reply(percent, charger=0, tid=2, eid=4, etype=0x10, bad_sum=False):
    payload = bytes((charger, percent))
    n = 12 + len(payload)
    b = bytearray(64)
    b[0], b[1], b[2], b[3], b[5] = 2, n, 4, 0xFF, 0
    b[6], b[7], b[8], b[9], b[10] = 0x96, 0xC3, 0x14, eid, etype
    b[11], b[12] = tid & 0xFF, tid >> 8
    b[13:13 + len(payload)] = payload
    b[n + 1] = (sum(b[5:n + 1]) + (1 if bad_sum else 0)) & 0xFF
    return bytes(b)


def buds(r, l, bad=False):
    b = bytearray(64)
    b[0], b[1], b[2] = 2, 0x12, 4
    b[14], b[16] = r, l
    b[19] = ((r + l + 117) + (1 if bad else 0)) & 0xFF
    return bytes(b)


def pulse(level, flags=0x08):
    return bytes((0xB0, 0, 0, level, flags, 0, 0, 0))


@pytest.fixture(autouse=True)
def _reset():
    sony._triggered.clear()


# ---- DualShock 4 ----
@pytest.mark.parametrize("status,exp", [(0x04, (45, False)), (0x00, (5, False)), (0x09, (95, False)),
                                        (0x0A, (100, False)), (0x14, (45, True)), (0x1A, (100, True)),
                                        (0x1B, (100, False))])
def test_ds4_decode(status, exp):
    assert sony.decode_ds4_status(status) == exp


def test_ds4_usb():
    d = run([info()], Fake([ds4_usb(0x14)]))
    assert len(d) == 1
    x = d[0]
    assert (x.id, x.kind, x.battery, x.charging, x.link, x.connected, x.source) == \
           ("sony:05c4", "gamepad", 45, True, "usb", True, "hid")


def test_ds4_v2_name_fallback():
    d = run([info(pid=0x09CC, product="")], Fake([ds4_usb(0x03)]))
    assert d[0].name == "DualShock 4" and d[0].battery == 35


def test_ds4_bt_needs_feature_once():
    simple = bytes([1]) + bytes(9)
    f = Fake([simple], [ds4_bt(0x06)])
    d = run([info(path=BT, n=78)], f)
    assert d[0].battery == 65 and d[0].link == "bluetooth"
    assert f.features == [(0x02, 37)]
    f2 = Fake([simple], [ds4_bt(0x06)])
    run([info(path=BT, n=78)], f2)           # already triggered for this path
    assert f2.features == []


def test_ds4_bt_extended_disabled():
    f = Fake([bytes([1]) + bytes(9)], [ds4_bt(0x06)])
    assert run([info(path=BT, n=78)], f, enable_bt_extended=False) == []
    assert f.features == []


def test_ds4_short_and_unrelated():
    assert run([info()], Fake([bytes([1, 2, 3]), bytes([0x77]) + bytes(63)])) == []


def test_ds4_dongle_without_pad():
    d = run([info(pid=0x0BA0)], Fake([ds4_usb(0x04, 0x04)]))
    assert d[0].connected is False and d[0].battery is None
    d = run([info(pid=0x0BA0)], Fake([ds4_usb(0x04, 0x00)]))
    assert d[0].connected is True and d[0].battery == 45


def test_ds4_wrong_collection_skipped():
    assert run([info(page=0xFF00, usage=1)], Fake([ds4_usb(4)])) == []


def test_dualsense_not_handled():
    assert run([info(pid=0x0CE6)], Fake([ds4_usb(4)])) == []


# ---- PULSE 3D ----
def test_pulse_levels():
    pi = info(pid=0x0D5E, page=0xFF01, usage=0x20, n=8)
    d = run([pi], Fake([pulse(30), pulse(70)]))
    assert d[0].battery == 70 and d[0].connected and d[0].link == "2.4ghz" and d[0].kind == "headset"


def test_pulse_charging_and_off():
    pi = info(pid=0x0D5E, page=0xFF01, usage=0x20, n=8)
    d = run([pi], Fake([pulse(0x80)]))
    assert d[0].charging and d[0].battery is None and d[0].connected
    d = run([pi], Fake([pulse(50, flags=0)]))
    assert d[0].connected is False and d[0].battery is None


def test_pulse_ignores_other_reports_and_collection():
    pi = info(pid=0x0D5E, page=0xFF01, usage=0x20, n=8)
    assert run([pi], Fake([bytes([0x01, 2, 3, 4, 5, 6, 7, 8]), bytes([0xB0, 1])])) == []
    assert run([info(pid=0x0D5E, page=12, usage=1, n=8)], Fake([pulse(50)])) == []


# ---- INZONE H5 / H9 II ----
def test_request_bytes_and_whitelist():
    r = sony.build_request(0x41, 0x04, 0x01)
    assert len(r) == 64 and r[:13] == bytes([2, 12, 1, 0, 0xFC, 8, 0x96, 0xC3, 0x41, 4, 1, 2, 0])
    assert r[13] == sum(r[6:13]) & 0xFF
    with pytest.raises(ValueError):
        sony.build_request(0x41, 0x23, 0x02)
    with pytest.raises(ValueError):
        sony.build_request(0x41, 0x04, 0x01, b"\x01")
    with pytest.raises(ValueError):
        sony._ReadOnly(Fake()).write(b"\x02" + bytes(63))
    with pytest.raises(ValueError):
        sony._ReadOnly(Fake()).get_feature(0x05, 41)


@pytest.mark.parametrize("pid,usage", [(0x0EBF, 2), (0x0FA8, 1)])
def test_inzone_battery(pid, usage):
    f = Fake([hci_reply(10, tid=1), hci_reply(77, charger=1)])
    d = run([info(pid=pid, page=0xFF04, usage=usage)], f)
    assert f.writes == [sony.build_request(0x41, 4, 1)]
    assert (d[0].battery, d[0].charging, d[0].connected, d[0].link) == (77, True, True, "2.4ghz")


def test_inzone_offline_and_invalid():
    i = info(pid=0x0EBF, page=0xFF04, usage=2)
    d = run([i], Fake([hci_reply(0xFF)]))
    assert d[0].connected is False and d[0].battery is None
    assert run([i], Fake([hci_reply(77, bad_sum=True), hci_reply(77, eid=9), hci_reply(150),
                          bytes(64)])) == []


def test_inzone_collection_selection():
    assert run([info(pid=0x0EBF, page=0xFF04, usage=1)], Fake([hci_reply(50)])) == []
    assert run([info(pid=0x0FA8, page=0xFF04, usage=2)], Fake([hci_reply(50)])) == []


def test_inzone_write_failure():
    f = Fake([hci_reply(50)], write_ok=False)
    assert run([info(pid=0x0EBF, page=0xFF04, usage=2)], f) == []


# ---- Buds ----
def test_buds():
    bi = info(pid=0x0EC2, page=0xFF00, usage=1)
    d = run([bi], Fake([buds(90, 60)]))
    assert d[0].battery == 60 and d[0].link == "2.4ghz"
    assert run([bi], Fake([buds(90, 60, bad=True), buds(101, 101)])) == []


# ---- guards ----
def test_deadline_and_cancel_before_write():
    i = info(pid=0x0EBF, page=0xFF04, usage=2)
    f = Fake([hci_reply(50)])
    assert sony.read([i], time.monotonic() - 1, None, handle_factory=lambda p, in_len=64: f) == []
    ev = threading.Event()
    ev.set()
    assert sony.read([i], time.monotonic() + 5, ev, handle_factory=lambda p, in_len=64: f) == []
    assert f.writes == []


def test_factory_error_never_raises():
    def boom(*a, **k):
        raise OSError("x")
    assert sony.read([info()], time.monotonic() + 1, None, handle_factory=boom) == []


def test_vendor_ids():
    assert sony.VENDOR_IDS == (0x054C,)


def test_pulse_and_buds_passive_window_is_short():
    import time as _t
    pi = info(pid=0x0D5E, page=0xFF01, usage=0x20, n=8)
    bi = info(pid=0x0EC2, page=0xFF00, usage=1)
    assert sony.PUSH_PASSIVE_S <= 0.3
    for i in (pi, bi):
        t = _t.monotonic()
        assert run([i], Fake([])) == []
        assert _t.monotonic() - t < 0.6


def test_ds4_bt_is_beta_but_usb_is_stable():
    f = Fake([bytes([1]) + bytes(9)], [ds4_bt(0x06)])
    assert run([info(path=BT, n=78)], f, beta=False) == []
    assert f.features == []                         # skipped before any device access
    assert run([info(path=BT, n=78)], Fake([bytes([1]) + bytes(9)], [ds4_bt(0x06)]),
               beta=True)[0].link == "bluetooth"
    usb = [d for d in run([info(path=USB, n=64)], Fake([], []), beta=False)]
    assert sony._is_beta_info(info(path=BT, n=78)) is True
    assert sony._is_beta_info(info(path=USB, n=64)) is False
    assert usb == [] or usb[0].link == "usb"
