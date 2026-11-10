from types import SimpleNamespace

import pytest

from taskbar_battery_widget import hidpp


def info(usage, path, pid=0xC539, page=0xFF00, out=None, product="USB Receiver"):
    out = out if out is not None else (7 if usage == 1 else 20)
    return SimpleNamespace(path=path, vid=0x046D, pid=pid, usage_page=page, usage=usage,
                           in_len=out, out_len=out, feat_len=0, product=product)


def test_encode_long_and_short():
    p = hidpp.encode_request(1, 0x06, 1, b"\x00", sw_id=0x0B)
    assert len(p) == 20 and p[:5] == bytes([0x11, 1, 6, 0x1B, 0])
    s = hidpp.encode_request(2, 0, 0, b"\x10\x04", long=False)
    assert s == bytes([0x10, 2, 0, 0x0B, 0x10, 0x04, 0])
    with pytest.raises(ValueError):
        hidpp.encode_request(1, 0, 0, bytes(20), long=True)


def test_parse_reply_and_match():
    rep = hidpp.parse_report(bytes([0x11, 1, 0x06, 0x1B, 0x10, 0x33] + [0] * 14))
    assert rep.error is None and rep.feature_index == 6 and rep.function == 1 and rep.sw_id == 0x0B
    assert rep.params[:2] == b"\x10\x33"
    assert hidpp.reply_matches(rep, 1, 6, 1)
    assert not hidpp.reply_matches(rep, 2, 6, 1)
    assert hidpp.parse_report(b"\x02\x00\x00\x00\x00\x00\x00") is None
    assert hidpp.parse_report(b"") is None


def test_error_replies():
    e1 = hidpp.parse_report(bytes.fromhex("10028f001b0900"))        # HID++ 1.0, unknown device
    assert e1.error_kind == "hidpp1" and e1.error == 9 and hidpp.is_not_connected(e1)
    assert hidpp.reply_matches(e1, 2, 0, 1)
    e2 = hidpp.parse_report(bytes([0x11, 1, 0xFF, 6, 0x1B, 0x02] + [0] * 14))
    assert e2.error_kind == "hidpp2" and e2.error == 2 and e2.feature_index == 6
    assert not hidpp.is_not_connected(e2)


def test_get_feature_and_ping_decode():
    assert hidpp.decode_get_feature(bytes([6, 0, 1])) == 6
    assert hidpp.decode_get_feature(bytes([0, 0, 0])) is None
    assert hidpp.decode_ping(bytes([4, 2, 0xA5])) == (4, 2)
    assert hidpp.decode_ping(b"") is None


def test_unified_battery():
    assert hidpp.decode_unified_battery(bytes([72, 4, 0, 0])) == (72, False)
    assert hidpp.decode_unified_battery(bytes([55, 4, 1, 1])) == (55, True)
    assert hidpp.decode_unified_battery(bytes([100, 8, 3, 1])) == (100, False)   # full: not charging
    assert hidpp.decode_unified_battery(bytes([0, 2, 0, 0])) == (15, False)      # level flag only
    assert hidpp.decode_unified_battery(bytes([200, 4, 0])) is None
    assert hidpp.decode_unified_battery(b"\x01") is None


def test_battery_status():
    assert hidpp.decode_battery_status(bytes([80, 50, 0])) == (80, False)
    assert hidpp.decode_battery_status(bytes([40, 50, 1])) == (40, True)
    assert hidpp.decode_battery_status(bytes([99, 100, 3])) == (99, False)
    assert hidpp.decode_battery_status(bytes([120, 0, 0])) is None


def test_voltage_curve_is_monotone_and_bounded():
    assert hidpp.voltage_to_percent(3300) == 0
    assert hidpp.voltage_to_percent(3500) == 0
    assert hidpp.voltage_to_percent(4200) == 100
    assert hidpp.voltage_to_percent(4400) == 100
    vals = [hidpp.voltage_to_percent(mv) for mv in range(3400, 4300, 10)]
    assert vals == sorted(vals)
    assert 60 <= hidpp.voltage_to_percent(3905) <= 75


def test_voltage_and_adc_decode():
    assert hidpp.decode_voltage(bytes([0x0F, 0x41, 0x00])) == (hidpp.voltage_to_percent(3905), False)
    assert hidpp.decode_voltage(bytes([0x10, 0x33, 0x80]))[1] is True
    assert hidpp.decode_voltage(bytes([0x00, 0x10, 0])) is None          # implausible mV
    assert hidpp.decode_adc(bytes([0x0F, 0xA0, 0x01])) == (hidpp.voltage_to_percent(4000), True)


def test_name_chunks():
    assert hidpp.decode_name_length(bytes([9, 0, 0])) == 9
    assert hidpp.decode_name_chunk(b"G915 WI\x00\x00") == "G915 WI"
    assert hidpp.assemble_name([b"G915 WI", b"RELESS"], 13) == "G915 WIRELESS"
    assert hidpp.assemble_name([b"MX Master 3"], 5) == "MX Ma"


def test_device_type_kind():
    assert hidpp.kind_from_device_type(0) == "keyboard"
    assert hidpp.kind_from_device_type(3) == "mouse"
    assert hidpp.kind_from_device_type(8) == "headset"
    assert hidpp.kind_from_device_type(99) is None
    assert hidpp.kind_from_device_type(None) is None


def test_group_collections_by_interface():
    base = r"\\?\HID#VID_046D&PID_C539&MI_02&Col0{n}#9&45f1c32&0&000{m}#{{guid}}"
    infos = [
        info(1, base.format(n=1, m=0)), info(2, base.format(n=2, m=1)),
        info(4, base.format(n=3, m=2), out=32),                      # DJ collection: ignored
        info(1, r"\\?\HID#VID_046D&PID_C539&MI_00#9&1220a35&0&0000#{guid}", page=1),   # keyboard
        info(1, r"\\?\HID#VID_046D&PID_C547&MI_02&Col01#9&244bbd02&0&0000#{guid}", pid=0xC547),
    ]
    groups = hidpp.find_hidpp_groups(infos)
    assert len(groups) == 2
    g = next(g for g in groups if g["pid"] == 0xC539)
    assert g["short"] is infos[0] and g["long"] is infos[1]
    assert hidpp.is_receiver(g)


# ---- fake device driving Channel / scan_logitech ------------------------------------------
class FakeDongle:
    """Receiver with one device at index 1: HID++ 2.0 root + name + 0x1001 battery feature."""

    def __init__(self, battery_mv=3905, asleep=False):
        self.long_q, self.short_q = [], []
        self.mv = battery_mv
        self.asleep = asleep
        self.writes = []

    def handle(self, path, in_len=64):
        return FakeHandle(self, path.endswith("long"))

    def on_write(self, pkt):
        self.writes.append(bytes(pkt))
        dev, feat, fs = pkt[1], pkt[2], pkt[3]
        fn = fs >> 4
        if dev != 1:
            self.short_q.append(bytes([0x10, dev, 0x8F, feat, fs, 0x09, 0]))
            return
        if self.asleep:
            return
        hdr = bytes([0x11, dev])
        if feat == 0:
            if fn == 1:
                body = bytes([4, 2, pkt[6]])
            else:
                fid = (pkt[4] << 8) | pkt[5]
                body = bytes([{0x0005: 1, 0x1001: 2}.get(fid, 0), 0, 0])
        elif feat == 1:     # name feature
            name = b"G915 TEST KEYBOARD"
            if fn == 0:
                body = bytes([len(name)])
            elif fn == 1:
                body = name[pkt[4]:pkt[4] + 16]
            else:
                body = bytes([0])
        elif feat == 2:
            body = bytes([self.mv >> 8, self.mv & 0xFF, 0])
        else:
            return
        self.long_q.append((hdr + bytes([feat, fs]) + body).ljust(20, b"\x00"))


class FakeHandle:
    def __init__(self, dongle, is_long):
        self.d, self.is_long = dongle, is_long

    def write(self, pkt, timeout_ms=1000):
        self.d.on_write(pkt)
        return True

    def read(self, timeout_ms=0):
        q = self.d.long_q if self.is_long else self.d.short_q
        return q.pop(0) if q else None

    def drain(self):
        pass

    def close(self):
        pass


def receiver_infos():
    return [info(1, r"\\?\HID#VID_046D&PID_C539&MI_02&Col01#9&aa&0&0000#short"),
            info(2, r"\\?\HID#VID_046D&PID_C539&MI_02&Col02#9&aa&0&0001#long")]


def test_scan_logitech_fake_receiver():
    hidpp._info_cache.clear()
    dongle = FakeDongle()
    devs = hidpp.scan_logitech(receiver_infos(), dongle.handle)
    assert len(devs) == 1
    d = devs[0]
    assert d.id == "logi:c539:01" and d.name == "G915 TEST KEYBOARD" and d.kind == "keyboard"
    assert d.link == "2.4ghz" and d.connected and d.charging is False
    assert d.battery == hidpp.voltage_to_percent(3905)
    # only queries were sent: root getFeature (fn0) / ping (fn1) and read-only feature functions
    assert all((w[3] >> 4) <= 2 for w in dongle.writes)


def test_scan_logitech_asleep_device_is_skipped(monkeypatch):
    hidpp._info_cache.clear()
    monkeypatch.setattr(hidpp.Channel, "ping",
                        lambda self, idx, timeout=0.8: ("timeout", None) if idx == 1 else ("absent", None))
    assert hidpp.scan_logitech(receiver_infos(), FakeDongle(asleep=True).handle) == []


def test_scan_logitech_open_failure_returns_empty():
    def boom(path, in_len=64):
        raise OSError("nope")
    assert hidpp.scan_logitech(receiver_infos(), boom) == []
    assert hidpp.scan_logitech([], boom) == []
