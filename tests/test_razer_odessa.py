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
    assert wireless.dongle_key("razer:9999") == "1532:9999"
