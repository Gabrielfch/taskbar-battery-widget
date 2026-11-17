"""Receiver entry + USB-cable entry of the same physical unit collapse into one device."""
import logging
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import hidpp

LOGI = 0x046D


def info(usage, path, pid, product):
    n = 7 if usage == 1 else 20
    return SimpleNamespace(path=path, vid=LOGI, pid=pid, usage_page=0xFF00, usage=usage,
                           in_len=n, out_len=n, feat_len=0, product=product)


def infos_for(pid, product, tag):
    base = rf"\\?\HID#VID_046D&PID_{pid:04X}&MI_02&Col0{{n}}#9&{tag}&0&000{{m}}#"
    return [info(1, base.format(n=1, m=0) + "short", pid, product),
            info(2, base.format(n=2, m=1) + "long", pid, product)]


class Unit:
    """One HID++ 2.0 device behind device index `idx` of a fake interface."""

    def __init__(self, idx, name, unit, mv=3905, charging=False, has_info=True):
        self.idx, self.name, self.unit = idx, name.encode(), unit
        self.mv, self.charging, self.has_info = mv, charging, has_info


class Iface:
    def __init__(self, units):
        self.units = {u.idx: u for u in units}
        self.long_q, self.short_q, self.writes = [], [], []

    def on_write(self, pkt):
        self.writes.append(bytes(pkt))
        dev, feat, fs = pkt[1], pkt[2], pkt[3]
        fn = fs >> 4
        u = self.units.get(dev)
        if u is None:
            self.short_q.append(bytes([0x10, dev, 0x8F, feat, fs, 0x09, 0]))
            return
        if feat == 0:
            if fn == 1:
                body = bytes([4, 2, pkt[6]])
            else:
                fid = (pkt[4] << 8) | pkt[5]
                idx = {0x0005: 1, 0x1001: 2, 0x0003: 3 if u.has_info else 0}.get(fid, 0)
                body = bytes([idx, 0, 0])
        elif feat == 1:
            body = bytes([len(u.name)]) if fn == 0 else u.name[pkt[4]:pkt[4] + 16] if fn == 1 else bytes([0])
        elif feat == 2:
            body = bytes([u.mv >> 8, u.mv & 0xFF, 0x80 if u.charging else 0])
        elif feat == 3 and fn == 0:
            body = bytes([3]) + (bytes.fromhex(u.unit) if u.unit else bytes(4)) + bytes([0, 1, 0xAA, 0xBB])
        else:
            return
        self.long_q.append((bytes([0x11, dev, feat, fs]) + body).ljust(20, b"\x00"))


class Handle:
    def __init__(self, iface, is_long):
        self.i, self.is_long = iface, is_long

    def write(self, pkt, timeout_ms=1000):
        self.i.on_write(pkt)
        return True

    def read(self, timeout_ms=0):
        q = self.i.long_q if self.is_long else self.i.short_q
        return q.pop(0) if q else None

    def drain(self):
        pass

    def close(self):
        pass


def factory(ifaces):
    def make(path, in_len=64):
        pid = int(path.split("PID_")[1][:4], 16)
        return Handle(ifaces[pid], path.endswith("long"))
    return make


G915_UNIT, G903_UNIT = "a1b2c3d4", "0badf00d"


def keyboard_setup(rx_unit=G915_UNIT, usb_unit=G915_UNIT, usb_has_info=True, rx_has_info=True):
    infos = infos_for(0xC547, "USB Receiver", "a1") + infos_for(0xC33E, "G915 WIRELESS RGB", "a2")
    ifaces = {
        0xC547: Iface([Unit(1, "G915 WIRELESS RGB MECHANICAL GAMING KEYBOARD", rx_unit, mv=3800,
                            has_info=rx_has_info)]),
        0xC33E: Iface([Unit(0xFF, "G915 WIRELESS RGB MECHANICAL GAMING KEYBOARD", usb_unit, mv=4000,
                            charging=True, has_info=usb_has_info)]),
    }
    return infos, factory(ifaces), ifaces


def test_device_info_whitelist():
    assert hidpp.build_guarded_request(1, 3, hidpp.F_DEVICE_INFO, 0)[3] >> 4 == 0
    for fn in (1, 2, 3, 15):
        with pytest.raises(hidpp.HidppSafetyError):
            hidpp.build_guarded_request(1, 3, hidpp.F_DEVICE_INFO, fn)
    # root getFeature for 0x0003 is now allowed (and only for whitelisted ids)
    hidpp.build_guarded_request(1, 0, hidpp.F_ROOT, 0, b"\x00\x03")
    with pytest.raises(hidpp.HidppSafetyError):
        hidpp.build_guarded_request(1, 0, hidpp.F_ROOT, 0, b"\x00\x04")


def test_decode_unit_id():
    assert hidpp.decode_unit_id(bytes([3, 0xA1, 0xB2, 0xC3, 0xD4, 0, 1])) == "a1b2c3d4"
    assert hidpp.decode_unit_id(bytes([3, 0, 0, 0, 0])) is None
    assert hidpp.decode_unit_id(bytes([3, 0xFF, 0xFF, 0xFF, 0xFF])) is None
    assert hidpp.decode_unit_id(b"\x03\x01") is None


def test_read_unit_id_over_channel():
    hidpp._info_cache.clear()
    infos, fac, ifaces = keyboard_setup()
    g = next(g for g in hidpp.find_hidpp_groups(infos) if g["pid"] == 0xC547)
    with hidpp.Channel(g, fac) as ch:
        assert ch.read_unit_id(1) == G915_UNIT
    # the only 0x0003 request sent is fn0
    info_writes = [w for w in ifaces[0xC547].writes if w[2] == 3]
    assert info_writes and all(w[3] >> 4 == 0 for w in info_writes)


def test_receiver_plus_usb_same_unit_is_one_device():
    hidpp._info_cache.clear()
    infos, fac, _ = keyboard_setup()
    devs = hidpp.scan_logitech(infos, fac)
    assert len(devs) == 1
    d = devs[0]
    assert d.id == "logi:c547:01" and d.name.startswith("G915 WIRELESS")
    assert d.link == "usb" and d.connected and d.charging is True
    assert d.battery == hidpp.voltage_to_percent(4000)
    assert hidpp.take_absorbed_ids() == {"logi:c33e:ff"}
    assert not hasattr(d, "_unit_id")


def test_mouse_other_receiver_and_pid_merges_too():
    hidpp._info_cache.clear()
    infos = infos_for(0xC539, "USB Receiver", "b1") + infos_for(0xC086, "G903 LIGHTSPEED", "b2")
    ifaces = {0xC539: Iface([Unit(2, "G903 LIGHTSPEED Wireless Gaming Mouse", G903_UNIT, mv=3700)]),
              0xC086: Iface([Unit(0xFF, "G903 LIGHTSPEED Wireless Gaming Mouse", G903_UNIT, mv=4100,
                                  charging=True)])}
    devs = hidpp.scan_logitech(infos, factory(ifaces))
    assert [d.id for d in devs] == ["logi:c539:02"]
    assert devs[0].link == "usb" and devs[0].charging and devs[0].battery == hidpp.voltage_to_percent(4100)


def test_keyboard_and_mouse_together_merge_across_groups():
    hidpp._info_cache.clear()
    kb_infos, _, kb_if = keyboard_setup()
    infos = kb_infos + infos_for(0xC539, "USB Receiver", "b1") + infos_for(0xC086, "G903 LIGHTSPEED", "b2")
    ifaces = dict(kb_if)
    ifaces[0xC539] = Iface([Unit(2, "G903 LIGHTSPEED Wireless Gaming Mouse", G903_UNIT)])
    ifaces[0xC086] = Iface([Unit(0xFF, "G903 LIGHTSPEED Wireless Gaming Mouse", G903_UNIT, charging=True)])
    devs = hidpp.scan_logitech(infos, factory(ifaces))
    assert sorted(d.id for d in devs) == ["logi:c539:02", "logi:c547:01"]
    assert all(d.link == "usb" for d in devs)


def test_different_unit_stays_two():
    hidpp._info_cache.clear()
    infos, fac, _ = keyboard_setup(usb_unit="11223344")
    devs = hidpp.scan_logitech(infos, fac)
    assert sorted(d.id for d in devs) == ["logi:c33e:ff", "logi:c547:01"]
    assert {d.link for d in devs} == {"usb", "2.4ghz"}


@pytest.mark.parametrize("which", ["usb", "rx"])
def test_missing_unit_feature_keeps_two(which):
    hidpp._info_cache.clear()
    infos, fac, _ = keyboard_setup(usb_has_info=which != "usb", rx_has_info=which != "rx")
    assert len(hidpp.scan_logitech(infos, fac)) == 2


def test_usb_only_is_unchanged():
    hidpp._info_cache.clear()
    infos, fac, _ = keyboard_setup()
    devs = hidpp.scan_logitech([i for i in infos if i.pid == 0xC33E], fac)
    assert [d.id for d in devs] == ["logi:c33e:ff"] and devs[0].link == "usb"


def test_unit_id_cached_and_not_logged(caplog):
    hidpp._info_cache.clear()
    infos, fac, ifaces = keyboard_setup()
    with caplog.at_level(logging.DEBUG):
        hidpp.scan_logitech(infos, fac)
        hidpp.scan_logitech(infos, fac)
    assert G915_UNIT not in caplog.text
    n = sum(1 for w in ifaces[0xC547].writes if w[2] == 0 and w[3] >> 4 == 0 and w[4:6] == b"\x00\x03")
    assert n == 1                                   # resolved once, then served from the cache
    dev = hidpp.scan_logitech(infos, fac)[0]
    assert G915_UNIT not in repr(dev)
