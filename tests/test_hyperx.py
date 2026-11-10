import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import hyperx as hx
from taskbar_battery_widget import wireless

LIVE = bytes.fromhex("21ff050ed7") + bytes(15)


class FakeHandle:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        return True

    def read(self, timeout_ms=0):
        if self.replies:
            return self.replies.pop(0)
        time.sleep(min(timeout_ms, 20) / 1000)
        return None


def info(pid=0x16C4, page=0xFF53, n=20, path="p", product=""):
    return SimpleNamespace(vid=0x0951, pid=pid, path=path, usage_page=page, usage=0x0303,
                           in_len=n, out_len=n, product=product)


def run(replies, infos=None, budget=1.0, cancel=None):
    h = FakeHandle(replies)
    out = hx.read_hyperx(infos or [info()], time.monotonic() + budget, cancel,
                         handle_factory=lambda path, in_len=20: h)
    return out, h


def test_request_bytes():
    r = hx.build_request()
    assert len(r) == 20 and r[:3] == b"\x21\xff\x05" and r[3:] == bytes(17)


def test_parse_live_reply():
    assert hx.parse_reply(LIVE) == 3799


@pytest.mark.parametrize("buf", [None, b"", b"\x21\xff\x05\x0e", b"\x21\xff\x06\x0e\xd7",
                                 b"\x22\xff\x05\x0e\xd7", bytes(20)])
def test_parse_ignores_other_reports(buf):
    assert hx.parse_reply(buf) is None


def test_curve_monotone_clamped():
    vals = [hx.voltage_to_percent(mv) for mv in range(3000, 4200, 5)]
    assert vals == sorted(vals)
    assert min(vals) == 0 and max(vals) == 100
    assert hx.voltage_to_percent(0) == 0 and hx.voltage_to_percent(9999) == 100
    assert 30 <= hx.voltage_to_percent(3799) <= 70


def test_whitelist():
    h = FakeHandle([])
    with pytest.raises(ValueError):
        hx._send(h, b"\x21\xff\x06" + bytes(17), 100)
    with pytest.raises(ValueError):
        hx._send(h, b"\x21\xff\x05", 100)
    assert h.sent == []


def test_read_ok_ignores_unsolicited():
    junk = b"\x01\x02" + bytes(18)
    out, h = run([junk, LIVE])
    assert h.sent == [hx.build_request()]
    d, = out
    assert d.id == "hyperx:16c4" and d.kind == "headset" and d.connected and not d.charging
    assert d.battery == hx.voltage_to_percent(3799) and d.link == "2.4ghz" and d.source == "hid"
    assert d.name == hx.DEFAULT_NAME


def test_charging_threshold():
    hx._last_battery.clear()
    out, _ = run([bytes([0x21, 0xFF, 0x05, 0x10, 0x0B]) + bytes(15)])
    assert out[0].battery == hx.voltage_to_percent(0x100B) and not out[0].charging
    hx._last_battery.clear()
    out, _ = run([bytes([0x21, 0xFF, 0x05, 0x10, 0x0C]) + bytes(15)])
    assert out[0].charging and out[0].battery is None and out[0].connected
    run([LIVE])
    out, _ = run([bytes([0x21, 0xFF, 0x05, 0x10, 0x0C]) + bytes(15)])
    assert out[0].charging and out[0].battery == hx.voltage_to_percent(3799)


def test_no_reply():
    out, h = run([], budget=0.4)
    d, = out
    assert not d.connected and d.battery is None and len(h.sent) == 1


def test_deadline_and_cancel_before_write():
    out, h = run([LIVE], budget=-1)
    assert out == [] and h.sent == []
    c = threading.Event()
    c.set()
    out, h = run([LIVE], cancel=c)
    assert out == [] and h.sent == []
    h = FakeHandle([LIVE])
    assert hx._query(h, time.monotonic() - 1, None) is None and h.sent == []
    assert hx._query(h, time.monotonic() + 1, c) is None and h.sent == []


def test_only_ff53_20_byte_collection():
    infos = [info(page=0xFF00, path="a"), info(n=64, path="b"), info(page=0x000C, path="c"),
             SimpleNamespace(vid=0x046D, pid=0x16C4, path="d", usage_page=0xFF53, usage=3,
                             in_len=20, out_len=20, product="")]
    out, h = run([LIVE], infos=infos)
    assert out == [] and h.sent == []
    out, h = run([LIVE], infos=infos + [info(path="e"), info(path="f")])
    assert len(out) == 1 and len(h.sent) == 1


def test_never_raises():
    def boom(path, in_len=20):
        raise OSError("x")
    assert hx.read_hyperx([info()], time.monotonic() + 1, None, handle_factory=boom) == []
    assert hx.read_hyperx(None, time.monotonic() + 1) == []


def test_dongle_key():
    assert wireless.dongle_key("hyperx:16c4") == "0951:16c4"
    assert 0x0951 in wireless.VENDOR_IDS
