from types import SimpleNamespace

from taskbar_battery_widget import razer


def test_build_request_layout_and_crc():
    buf = razer.build_request(0x1F, 0x07, 0x80, 0x02)
    assert len(buf) == 91 and buf[0] == 0
    pkt = buf[1:]
    assert pkt[0] == 0 and pkt[1] == 0x1F
    assert pkt[2:4] == b"\x00\x00" and pkt[4] == 0
    assert pkt[5] == 2 and pkt[6] == 0x07 and pkt[7] == 0x80
    # crc = xor of bytes 2..87 = 0x02 ^ 0x07 ^ 0x80
    assert pkt[88] == 0x02 ^ 0x07 ^ 0x80 == razer.crc(pkt)
    assert pkt[89] == 0


def test_crc_includes_args():
    a = razer.build_request(0x3F, 0x07, 0x80, 0x02, bytes([0x00, 0xFF]))[1:]
    assert a[88] == 0x02 ^ 0x07 ^ 0x80 ^ 0xFF


def make_reply(status=2, tid=0x1F, cmd_id=0x80, level=0xBF):
    pkt = bytearray(90)
    pkt[0], pkt[1], pkt[5], pkt[6], pkt[7] = status, tid, 2, 7, cmd_id
    pkt[9] = level
    pkt[88] = razer.crc(bytes(pkt))
    return b"\x00" + bytes(pkt)


def test_decode_response():
    r = razer.decode_response(make_reply(level=191))
    assert r.status == 2 and r.cmd_class == 7 and r.cmd_id == 0x80 and r.crc_ok
    assert razer.battery_percent(r.args) == 75
    assert razer.decode_response(b"short") is None
    assert razer.decode_response(None) is None
    bad = bytearray(make_reply())
    bad[89 - 1] ^= 0xFF
    assert razer.decode_response(bytes(bad)).crc_ok is False


def test_battery_percent_scaling():
    assert razer.battery_percent(bytes([0, 0])) == 0
    assert razer.battery_percent(bytes([0, 255])) == 100
    assert razer.battery_percent(bytes([0, 128])) == 50
    assert razer.battery_percent(b"\x00") is None
    assert razer.is_charging(bytes([0, 1])) and not razer.is_charging(bytes([0, 0]))


class FakeRazer:
    def __init__(self, accept_tid=0x3F, level=0x80, charging=0):
        self.accept, self.level, self.charging = accept_tid, level, charging
        self.last = None

    def set_feature(self, data):
        self.last = bytes(data)
        return True

    def get_feature(self, rid, n):
        tid, cmd = self.last[2], self.last[8]
        if tid != self.accept:
            return make_reply(status=3, tid=tid, cmd_id=cmd)
        level = self.level if cmd == 0x80 else self.charging
        return make_reply(2, tid, cmd, level)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def rinfo(feat, pid=0x00A7, product="Razer Viper Ultimate Dongle", page=0xFF00, path="p"):
    return SimpleNamespace(path=path, vid=0x1532, pid=pid, usage_page=page, usage=2,
                           in_len=0, out_len=0, feat_len=feat, product=product)


def test_scan_razer_standard_protocol(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    h = FakeRazer(accept_tid=0x3F, level=0x80, charging=1)
    devs = razer.scan_razer([rinfo(91)], lambda path: h)
    assert len(devs) == 1
    d = devs[0]
    assert d.id.startswith("razer:00a7:") and d.battery == 50 and d.charging is True
    assert d.link == "2.4ghz" and d.kind == "mouse"


def test_scan_razer_skips_unsupported_and_failures(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    # collection without the 90-byte feature report (e.g. BlackShark V2 HyperSpeed)
    assert razer.scan_razer([rinfo(9, 0x0565, "Razer BlackShark V2 HS 2.4")], lambda p: FakeRazer()) == []
    # device answers with an error on every transaction id
    assert razer.scan_razer([rinfo(91)], lambda p: FakeRazer(accept_tid=0x00)) == []

    def boom(path):
        raise OSError("busy")
    assert razer.scan_razer([rinfo(91)], boom) == []


def test_kind_and_link():
    assert razer.kind_for("Razer BlackShark V2 HS 2.4") == "headset"
    assert razer.kind_for("Razer BlackWidow V3") == "keyboard"
    assert razer.link_for("Razer BlackShark V2 HS 2.4") == "2.4ghz"
    assert razer.link_for("Razer DeathAdder V2") == "usb"


class RecordingRazer(FakeRazer):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.sent_tids = []

    def set_feature(self, data):
        self.sent_tids.append(data[2])
        return super().set_feature(data)


def test_txn_order_known_pid_first():
    assert razer._txn_order(0x007C) == (0x3F, 0x1F, 0xFF, 0x9F)
    assert razer._txn_order(0x00A4) == (0x1F, 0x3F, 0xFF, 0x9F)
    assert razer._txn_order(0x0555) == (0x3F, 0x1F, 0xFF, 0x9F)


def test_txn_order_unknown_pid_unchanged():
    assert 0x00A7 not in razer.KNOWN_DEVICES
    assert razer._txn_order(0x00A7) == razer.TRANSACTION_IDS == (0x1F, 0x3F, 0xFF, 0x9F)


def test_scan_known_pid_sends_exact_txn_first(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    h = RecordingRazer(accept_tid=0x3F, level=0x80)
    devs = razer.scan_razer([rinfo(91, 0x007C, "")], lambda p: h)
    assert len(devs) == 1 and devs[0].battery == 50
    assert h.sent_tids[0] == 0x3F


def test_scan_unknown_pid_fallback_order_unchanged(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    h = RecordingRazer(accept_tid=0x9F, level=0x80)
    devs = razer.scan_razer([rinfo(91, 0x00A7, "Razer Viper Ultimate Dongle")], lambda p: h)
    assert len(devs) == 1
    assert list(dict.fromkeys(h.sent_tids)) == [0x1F, 0x3F, 0xFF, 0x9F]


def test_table_name_and_kind_when_product_generic(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    for product in ("", "Razer", "  HID  "):
        h = FakeRazer(accept_tid=0x1F)
        d = razer.scan_razer([rinfo(91, 0x00AB, product)], lambda p: h)[0]
        assert d.name == razer.sanitize_name("Razer Basilisk V3 Pro Wireless")
        assert d.kind == "mouse" and d.link == "2.4ghz"


def test_table_dock_is_mouse_and_blackshark_is_headset(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    dock = razer.scan_razer([rinfo(91, 0x00A4, "", path="a")], lambda p: FakeRazer(accept_tid=0x1F))[0]
    assert dock.name == razer.sanitize_name("Razer Mouse Dock Pro") and dock.kind == "mouse"
    hs = razer.scan_razer([rinfo(91, 0x0555, "", path="b")], lambda p: FakeRazer(accept_tid=0x3F))[0]
    assert hs.name == razer.sanitize_name("Razer BlackShark V2 Pro (RZ04-0453)") and hs.kind == "headset"


def test_specific_product_wins_over_table(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    d = razer.scan_razer([rinfo(91, 0x007A, "Razer Viper Ultimate Dongle")], lambda p: FakeRazer(accept_tid=0x3F))[0]
    assert d.name == razer.sanitize_name("Razer Viper Ultimate Dongle") and d.kind == "mouse"


def test_unknown_pid_generic_product_keeps_fallback_name(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    d = razer.scan_razer([rinfo(91, 0x00A7, "")], lambda p: FakeRazer(accept_tid=0x1F))[0]
    assert d.name == "Razer device" and d.kind == "mouse"
