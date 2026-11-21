import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import razer_odessa as ro
from taskbar_battery_widget import wireless
from taskbar_battery_widget.models import Device

REQ1 = bytes.fromhex("02 00 61 00 00 00 04 00 00 00 20 00 00".replace(" ", ""))
REQ3 = bytes.fromhex("02 00 63 00 00 00 04 00 00 80 2a 00 00".replace(" ", ""))
REQ2 = bytes.fromhex("02 00 62 00 00 00 04 00 00 80 21 00 00".replace(" ", ""))


def reply(txn, cmd, val, status=2):
    b = bytearray(64)
    b[0], b[1], b[2] = 2, status, txn + 0x60
    b[6], b[8], b[9] = 5, 0x80, 0x80 if cmd == 0x21 else 0
    b[10], b[11], b[12], b[13] = cmd, 1, 1, val
    b[62] = rxor(b)
    return bytes(b)


def rxor(b):
    x = 0
    for i, v in enumerate(b[:62]):
        if i != 1:
            x ^= v
    return x


def xor(b):
    x = 0
    for v in b[:62]:
        x ^= v
    return x


def test_build_matches_live_transcript():
    r1 = ro.build_request(bytes([0x20, 0, 0]), 1, False)
    r2 = ro.build_request(bytes([0x21, 0, 0]), 2, True)
    assert r1[:13] == REQ1 and r2[:13] == REQ2
    assert len(r1) == 64 and r1[62] == xor(r1) and r1[63] == 0
    assert r2[62] == xor(r2) == 0x02 ^ 0x62 ^ 0x04 ^ 0x80 ^ 0x21


def test_whitelist_refusal():
    for c in (b"\x22\x00\x00", b"\x01", b""):
        with pytest.raises(ValueError):
            ro.build_request(c, 1, False)


def test_parse_live_replies_and_rejections():
    # checksums (199, 36) captured from the live headset
    live1 = bytes.fromhex("02 02 61 00 00 00 05 00 80 00 20 01 01 01".replace(" ", "")) + bytes(48) + bytes([199, 0])
    live2 = bytes.fromhex("02 02 62 00 00 00 05 00 80 80 21 01 01 60".replace(" ", "")) + bytes(48) + bytes([36, 0])
    assert ro.parse_reply(live1, b"\x20\0\0", 1) == (2, b"\x01")
    assert ro.parse_reply(live2, b"\x21\0\0", 2) == (2, b"\x60")
    assert ro.parse_reply(live2, b"\x21\0\0", 3) is None          # txn mismatch
    assert ro.parse_reply(live2, b"\x20\0\0", 2) is None          # wrong echo
    assert ro.parse_reply(live2[:10], b"\x21\0\0", 2) is None
    bad = bytearray(live2)
    bad[11] = 0
    bad[62] = rxor(bad)
    assert ro.parse_reply(bytes(bad), b"\x21\0\0", 2) is None
    assert ro.parse_reply(reply(2, 0x21, 5, status=5), b"\x21\0\0", 2) == (5, b"\x05")


class FakeHandle:
    """Answers each request; `script` maps cmd -> list of (status, value) per attempt."""
    def __init__(self, script):
        self.script = script
        self.pending = None
        self.sent = []
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def write(self, pkt, timeout_ms=1000):
        self.timeouts.append(timeout_ms)
        self.sent.append(pkt)
        cmd, txn = pkt[10], pkt[2] - 0x60
        status, val = self.script[cmd].pop(0)
        self.pending = reply(txn, cmd, val, status)
        return True

    def read(self, timeout_ms=0):
        p, self.pending = self.pending, None
        return p


def info(pid=0x0565, path="p"):
    return SimpleNamespace(vid=0x1532, pid=pid, path=path, usage_page=0xFF14, usage=1,
                           in_len=64, out_len=64, product="")


def run(script, infos=None):
    h = FakeHandle(script)
    out = ro.read_odessa(infos or [info()], time.monotonic() + 3, None,
                         handle_factory=lambda path, in_len=64: h)
    return out, h


def test_read_connected():
    out, h = run({0x20: [(2, 1)], 0x21: [(2, 0x60)], 0x2A: [(2, 1)]})
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.kind, d.battery, d.connected, d.link, d.source) == \
        ("razer:1390", "headset", 96, True, "2.4ghz", "hid")
    assert d.charging is True
    assert [p[10] for p in h.sent] == [0x20, 0x21, 0x2A]
    assert all(1 <= t <= 1000 for t in h.timeouts)


def test_charging_mapping_and_failure():
    for val, exp in ((0, False), (1, True), (2, False)):
        out, _ = run({0x20: [(2, 1)], 0x21: [(2, 50)], 0x2A: [(2, val)]})
        assert out[0].charging is exp and out[0].battery == 50
    out, _ = run({0x20: [(2, 1)], 0x21: [(2, 50)], 0x2A: [(4, 0)]})   # 0x2A fails
    assert len(out) == 1 and out[0].charging is False and out[0].battery == 50


def test_only_ff14_collection():
    other = info()
    other.usage_page = 0xFF13
    out, h = run({}, [other])
    assert out == [] and h.sent == []


def test_deadline_and_cancel_before_write():
    h = FakeHandle({})
    assert ro._query(h, b"!\0\0", True, time.monotonic() - 1, None) is None
    ev = threading.Event()
    ev.set()
    assert ro._query(h, b"!\0\0", True, time.monotonic() + 3, ev) is None
    assert h.sent == []


def test_drain_is_bounded():
    from taskbar_battery_widget import hid_win
    calls = []
    fake = SimpleNamespace(read=lambda t: calls.append(t) or b"x")
    hid_win.HidHandle.drain(fake)
    assert len(calls) == 64


def test_busy_retry_then_ok_and_gives_up():
    out, _ = run({0x20: [(2, 1)], 0x21: [(1, 0), (3, 0), (2, 50)], 0x2A: [(2, 0)]})
    assert out[0].battery == 50
    out, _ = run({0x20: [(2, 1)], 0x21: [(1, 0)] * 4})
    assert out == []
    out, _ = run({0x20: [(2, 1)], 0x21: [(4, 0)]})          # error status: no retry
    assert out == []


def test_disconnected():
    out, h = run({0x20: [(2, 0)], 0x21: []})
    assert out[0].connected is False and out[0].battery is None
    assert [p[10] for p in h.sent] == [0x20]


def test_ignores_other_pids_and_bad_collections():
    other = info(pid=0x0abc)
    small = info()
    small.in_len = 20
    out, h = run({}, [other, small])
    assert out == [] and h.sent == []


def test_cancel():
    ev = threading.Event()
    ev.set()
    assert ro.read_odessa([info()], time.monotonic() + 3, ev, handle_factory=None) == []


def _dev(id_, battery, source=""):
    return Device(id=id_, name="Razer BlackShark V2 HyperSpeed", kind="headset", battery=battery,
                  connected=True, link="2.4ghz", source=source)


real_scan = wireless.scan_wireless


def _scan(monkeypatch, direct):
    import taskbar_battery_widget.hid_win as hw
    import taskbar_battery_widget.hidpp as hp
    import taskbar_battery_widget.razer as rz
    wireless.reset_cache()
    monkeypatch.setattr(wireless, "_hid_enabled", True)
    monkeypatch.setattr(hw, "enumerate_hid", lambda v=None: [info()])
    monkeypatch.setattr(hp, "scan_logitech", lambda i, cancel=None: [])
    monkeypatch.setattr(rz, "scan_razer", lambda i, cancel=None: [])
    monkeypatch.setattr(ro, "read_odessa", lambda i, d, cancel=None: direct)
    try:
        return real_scan()
    finally:
        wireless.reset_cache()


def test_wireless_scan_returns_direct_reading(monkeypatch):
    out = _scan(monkeypatch, [_dev("razer:1390", 96, "hid")])
    assert [(d.id, d.battery, d.source) for d in out] == [("razer:1390", 96, "hid")]


def test_dongle_key_for_odessa():
    assert wireless.dongle_key("razer:1390") == "1532:0565"
    assert wireless.dongle_key("razer:0555") == "1532:0555"
    assert wireless.dongle_key("razer:9999") == "1532:9999"


# ------------------------------------------------ BlackShark V2 Pro 2023 (1532:0555, 'PA'/'PI')
PA_REQ_CONN = bytes.fromhex("02 80 08 00 00 50 41 08 00 03 20".replace(" ", "")).ljust(64, b"\0")
PA_REQ_BAT = bytes.fromhex("02 80 08 00 00 50 41 08 00 03 21".replace(" ", "")).ljust(64, b"\0")
PA_REQ_CHG = bytes.fromhex("02 80 08 00 00 50 41 08 00 03 2a".replace(" ", "")).ljust(64, b"\0")
PA_WAKE_HEX = bytes.fromhex("02 80 07 00 00 50 41 0e 00 02 e1 01".replace(" ", "")).ljust(64, b"\0")

# Live replies captured on 1532:0555 (RZ04-0453) — trailing bytes past byte[1]+2 are stale MCU buffer data
PA_LIVE_WAKE_ACK = (bytes.fromhex("02 0d 50 49 01 c0 ae ba 23 00 03 00 0e 80 00 57 49 01 c0 13".replace(" ", ""))
                    .ljust(64, b"\0"))
PA_LIVE_CONN = (bytes.fromhex("02 0e 50 49 08 f9 ec 4d 1c 00 04 00 20 01 01 01 49 01 c0 13".replace(" ", ""))
                .ljust(64, b"\0"))
PA_LIVE_BAT = (bytes.fromhex("02 0e 50 49 08 fa fe 4d 1c 00 04 00 21 01 01 57 49 01 c0 13".replace(" ", ""))
               .ljust(64, b"\0"))
PA_LIVE_CHG = (bytes.fromhex("02 0e 50 49 08 fb 07 4e 1c 00 04 00 2a 01 01 00 49 01 c0 13".replace(" ", ""))
               .ljust(64, b"\0"))


def pa_reply(cmd_id: int, val: int, flag: int = 1) -> bytes:
    b = bytearray(64)
    b[0], b[1] = 0x02, 0x0E
    b[2:4] = b"PI"
    b[4] = 0x08
    b[9:12] = b"\x00\x04\x00"
    b[12], b[13], b[14], b[15] = cmd_id, flag, 1, val
    return bytes(b)


def pa_info(path="p555", page=0xFF00, usage=0x0001, in_len=64, out_len=64):
    return SimpleNamespace(vid=0x1532, pid=0x0555, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, feat_len=0,
                           product="Razer BlackShark V2 Pro 2.4")


class FakePaHandle:
    """Simulates a 1532:0555 dongle: `armed` controls whether pure GETs answer before `PA_WAKE`."""

    def __init__(self, responses, armed=True, rf_awake=True):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.armed = armed
        self.rf_awake = rf_awake
        self.queue: list[bytes] = []
        self.sent: list[bytes] = []
        self.wakes = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        self.queue.clear()

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        if pkt == ro.PA_WAKE:
            self.wakes += 1
            if self.wakes >= 2:
                self.armed = True
                self.rf_awake = True
            self.queue.append(PA_LIVE_WAKE_ACK)
            return True
        if not self.armed:
            return True
        cmd = pkt[10]
        if cmd in (0x21, 0x2A) and not self.rf_awake:
            return True
        items = self.responses.get(cmd, [])
        if items:
            nxt = items.pop(0)
            if nxt is not None:
                if isinstance(nxt, list):
                    self.queue.extend(nxt)
                else:
                    self.queue.append(nxt)
        return True

    def read(self, timeout_ms=0):
        return self.queue.pop(0) if self.queue else None


def test_pa_build_and_whitelist():
    assert ro.build_pa_request(0x20) == PA_REQ_CONN
    assert ro.build_pa_request(0x21) == PA_REQ_BAT
    assert ro.build_pa_request(0x2A) == PA_REQ_CHG
    assert ro.PA_WAKE == PA_WAKE_HEX
    for bad in (0x00, 0x13, 0x15, 0x93, 0x95, 0x9E, 0xAC, 0xE1):
        with pytest.raises(ValueError):
            ro.build_pa_request(bad)
    # _write_pa also refuses any non-whitelisted frame (e.g. remote off, EQ/config writes)
    h = FakePaHandle({})
    bad_wake = bytearray(ro.PA_WAKE)
    bad_wake[11] = 0x00
    for frame in (bytes(bad_wake), b"\x02" + bytes(63), b""):
        with pytest.raises(ValueError):
            ro._write_pa(h, frame, time.monotonic() + 2, None)
    assert h.sent == []


def test_parse_pa_live_replies_and_rejections():
    assert ro.parse_pa_reply(PA_LIVE_CONN, 0x20) == b"\x01"
    assert ro.parse_pa_reply(PA_LIVE_BAT, 0x21) == b"\x57"
    assert ro.parse_pa_reply(PA_LIVE_CHG, 0x2A) == b"\x00"
    assert ro.parse_pa_reply(PA_LIVE_WAKE_ACK, 0x20) is None            # type 0x01 wake ACK
    assert ro.parse_pa_reply(PA_LIVE_BAT, 0x20) is None                 # wrong command echo
    assert ro.parse_pa_reply(pa_reply(0x21, 80, flag=2), 0x21) is None  # unsolicited push (flag 2)
    assert ro.parse_pa_reply(None, 0x21) is None
    assert ro.parse_pa_reply(PA_LIVE_BAT[:20], 0x21) is None
    short_len = bytearray(PA_LIVE_BAT)
    short_len[1] = 10                                                   # end = 12 < 15
    assert ro.parse_pa_reply(bytes(short_len), 0x21) is None
    huge_len = bytearray(PA_LIVE_BAT)
    huge_len[1] = 70                                                    # end = 72 > 64
    assert ro.parse_pa_reply(bytes(huge_len), 0x21) is None
    bad_n = bytearray(PA_LIVE_BAT)
    bad_n[14] = 2                                                       # 15 + 2 > 16
    assert ro.parse_pa_reply(bytes(bad_n), 0x21) is None


def test_pa_armed_fast_path_sends_no_wake():
    h = FakePaHandle({
        0x20: [PA_LIVE_CONN],
        0x21: [[PA_LIVE_WAKE_ACK, pa_reply(0x21, 80, flag=2), PA_LIVE_BAT]],  # skips unrelated frames
        0x2A: [pa_reply(0x2A, 1)],
    }, armed=True)
    out = ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                         handle_factory=lambda path, in_len=64: h)
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.name, d.kind, d.battery, d.charging, d.connected, d.link, d.source) == (
        "razer:0555", "Razer BlackShark V2 Pro", "headset", 87, True, True, "2.4ghz", "hid"
    )
    assert h.wakes == 0
    assert h.sent == [PA_REQ_CONN, PA_REQ_BAT, PA_REQ_CHG]


def test_pa_unarmed_cold_plug_wakes_and_reads(monkeypatch):
    monkeypatch.setattr(ro, "PA_WAKE_DELAY_S", 0.0)
    h = FakePaHandle({
        0x20: [pa_reply(0x20, 1)],
        0x21: [pa_reply(0x21, 63)],
        0x2A: [pa_reply(0x2A, 2)],      # 2 = full on charger -> charging False
    }, armed=False)
    out = ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                         handle_factory=lambda path, in_len=64: h)
    assert len(out) == 1 and out[0].battery == 63 and out[0].charging is False
    assert h.wakes == 2


def test_pa_dozing_rf_link_wakes_when_battery_get_dropped(monkeypatch):
    monkeypatch.setattr(ro, "PA_WAKE_DELAY_S", 0.0)
    h = FakePaHandle({
        0x20: [pa_reply(0x20, 1)],
        0x21: [pa_reply(0x21, 44)],
        0x2A: [None],                   # charging query fails -> still returns battery with False
    }, armed=True, rf_awake=False)
    out = ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                         handle_factory=lambda path, in_len=64: h)
    assert len(out) == 1 and out[0].battery == 44 and out[0].charging is False
    assert h.wakes == 2


def test_pa_disconnected_and_invalid_battery(monkeypatch):
    monkeypatch.setattr(ro, "PA_WAKE_DELAY_S", 0.0)
    h = FakePaHandle({0x20: [pa_reply(0x20, 0)]}, armed=True)
    out = ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                         handle_factory=lambda path, in_len=64: h)
    assert len(out) == 1 and out[0].connected is False and out[0].battery is None
    assert h.sent == [PA_REQ_CONN]

    h_bad = FakePaHandle({0x20: [pa_reply(0x20, 1)], 0x21: [pa_reply(0x21, 255)]}, armed=True)
    assert ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                          handle_factory=lambda path, in_len=64: h_bad) == []


def test_pa_collection_selection_and_deadline_cancel():
    h = FakePaHandle({0x20: [PA_LIVE_CONN], 0x21: [PA_LIVE_BAT], 0x2A: [PA_LIVE_CHG]})
    bad_cols = [
        pa_info(page=0x000C, usage=0x0001, in_len=3, out_len=0),    # col01 consumer control
        pa_info(page=0xFF14, usage=0x0001),                         # wrong vendor page for 0555
        pa_info(page=0xFF00, usage=0x0002),                         # wrong usage
        pa_info(page=0xFF00, usage=0x0001, in_len=91, out_len=91),  # wrong report length
    ]
    assert ro.read_odessa(bad_cols, time.monotonic() + 3, None,
                          handle_factory=lambda path, in_len=64: h) == []
    assert h.sent == []

    assert ro._query_pa_once(h, 0x20, 200, time.monotonic() - 1, None) is None
    assert ro._wake_pa(h, time.monotonic() - 1, None) is False
    ev = threading.Event()
    ev.set()
    assert ro._query_pa_once(h, 0x20, 200, time.monotonic() + 3, ev) is None
    assert ro._wake_pa(h, time.monotonic() + 3, ev) is False
    assert h.sent == []


def test_bt_and_dongle_blackshark_v2_pro_merge_when_bt_disconnected():
    bt = Device(id="bt:bs", name="Razer BlackShark V2 Pro BT", kind="headset",
                battery=None, connected=False, link="bluetooth")
    dongle = Device(id="razer:0555", name="Razer BlackShark V2 Pro", kind="headset",
                    battery=87, connected=True, link="2.4ghz", source="hid")
    merged = wireless.merge_devices([bt], [dongle])
    assert len(merged) == 1 and merged[0].id == "razer:0555" and merged[0].battery == 87
