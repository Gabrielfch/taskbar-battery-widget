import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import jabra


def reply(seq, flags=0, level=80, cmd=0x12, sub=0x02, n=4, typ=3):
    b = bytearray(64)
    b[0], b[1], b[2], b[3] = 5, 0, 4, seq
    b[4] = (typ << 6) | (6 + n)
    b[5], b[6] = cmd, sub
    b[7], b[8] = flags, level
    return bytes(b)


def nack(seq, reason):
    return reply(seq, cmd=0xFE, sub=reason, n=0)


class FakeHandle:
    """Answers every write with make(seq) -> list of reports (seq echoed from the request)."""

    def __init__(self, make=None, fail_write=False):
        self.make = make
        self.fail_write = fail_write
        self.sent = []
        self.queue = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        self.queue = []

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        if self.fail_write:
            return False
        if self.make:
            self.queue = list(self.make(pkt[3]))
        return True

    def read(self, timeout_ms=0):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(min(timeout_ms, 20) / 1000)
        return None


def info(pid=0x2E57, usage=0x05, page=0xFF00, n=64, path="p", product="", vid=0x0B0E):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=n, out_len=n, product=product)


def run(make, infos=None, budget=1.0, cancel=None, **kw):
    h = FakeHandle(make, **kw)
    out = jabra.read(infos or [info()], time.monotonic() + budget, cancel,
                     handle_factory=lambda path, in_len=64: h)
    return out, h


def test_vendor_ids():
    assert jabra.VENDOR_IDS == (0x0B0E,)


@pytest.mark.parametrize("addr", [4, 8])
def test_request_bytes(addr):
    r = jabra.build_request(addr, 7, 0x12, 0x02)
    assert len(r) == 64
    assert r[:7] == bytes([5, addr, 0, 7, 0x46, 0x12, 0x02]) and r[7:] == bytes(57)


@pytest.mark.parametrize("args", [(4, 1, 0x13, 0x7C), (4, 1, 0x12, 0x08), (4, 1, 0x02, 0x00),
                                  (1, 1, 0x12, 0x02), (0x30, 1, 0x12, 0x02)])
def test_whitelist_refuses_other_requests(args):
    with pytest.raises(ValueError):
        jabra.build_request(*args)


def test_parse_valid_and_charging():
    assert jabra.parse_reply(reply(9, flags=0, level=77), 9) == ("ok", 77, False)
    assert jabra.parse_reply(reply(9, flags=0x41, level=50), 9) == ("ok", 50, True)
    assert jabra.parse_reply(reply(9, level=250), 9) == ("ok", 100, False)


def test_parse_ignores_unrelated():
    for buf in (None, b"", b"\x05\x00\x04", bytes(64), reply(9, typ=0), reply(8)):
        assert jabra.parse_reply(buf, 9) is None
    assert jabra.parse_reply(bytes([6]) + reply(9)[1:], 9) is None


def test_parse_short_payload_rejected():
    assert jabra.parse_reply(reply(9, n=1), 9) == ("rejected",)


def test_parse_nack():
    assert jabra.parse_reply(nack(3, 0xF3), 3) == ("off",)
    assert jabra.parse_reply(nack(3, 0xF5), 3) == ("off",)
    assert jabra.parse_reply(nack(3, 0xF6), 3) == ("rejected",)


@pytest.mark.parametrize("pid,usage,addr,link,name", [
    (0x24C7, 1, 4, "2.4ghz", "Jabra Link 380"),
    (0x2E57, 5, 4, "2.4ghz", "Jabra Link 390"),
    (0x2519, 1, 8, "usb", "Jabra Evolve2 65 Flex"),
])
def test_models(pid, usage, addr, link, name):
    out, h = run(lambda seq: [reply(seq, flags=1, level=64)], [info(pid, usage)])
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.name, d.kind, d.battery, d.charging, d.connected, d.link, d.source) == (
        f"jabra:{pid:04x}", name, "headset", 64, True, True, link, "hid")
    assert len(h.sent) == 1 and h.sent[0][1] == addr and h.sent[0][4:7] == bytes([0x46, 0x12, 0x02])


def test_product_string_is_sanitized():
    out, _ = run(lambda seq: [reply(seq)], [info(product="Jabra <Link> 390")])
    assert out[0].name == "Jabra Link 390"


def test_skips_unrelated_reports_before_answer():
    out, _ = run(lambda seq: [reply(seq ^ 1), reply(seq, typ=0), reply(seq, level=42)])
    assert out[0].battery == 42


def test_headset_off_nack():
    out, _ = run(lambda seq: [nack(seq, 0xF3)])
    assert len(out) == 1 and out[0].connected is False and out[0].battery is None


def test_silent_headset_yields_no_device():
    out, _ = run(lambda seq: [], budget=0.5)
    assert out == []


def test_rejected_yields_no_device():
    out, _ = run(lambda seq: [nack(seq, 0xF6)])
    assert out == []


def test_write_failure_yields_no_device():
    out, _ = run(None, fail_write=True)
    assert out == []


def test_deadline_before_write():
    out, h = run(lambda seq: [reply(seq)], budget=-1)
    assert out == [] and h.sent == []


def test_cancel_before_write():
    ev = threading.Event()
    ev.set()
    out, h = run(lambda seq: [reply(seq)], cancel=ev)
    assert out == [] and h.sent == []


def test_cancel_during_wait_yields_nothing():
    ev = threading.Event()

    def make(seq):
        ev.set()
        return []
    out, _ = run(make, cancel=ev)
    assert out == []


@pytest.mark.parametrize("kw", [dict(page=0xFF01), dict(usage=0x01), dict(n=32), dict(vid=0x046D),
                                dict(pid=0x1234)])
def test_collection_selection(kw):
    out, h = run(lambda seq: [reply(seq)], [info(**kw)])
    assert out == [] and h.sent == []


def test_link380_usage_differs_from_390():
    out, h = run(lambda seq: [reply(seq)], [info(0x24C7, usage=0x05)])
    assert out == [] and h.sent == []


def test_only_one_device_per_pid_and_first_path_wins():
    infos = [info(path="b"), info(path="a")]
    out, h = run(lambda seq: [reply(seq)], infos)
    assert len(out) == 1 and len(h.sent) == 1


def test_never_raises():
    def boom(path, in_len=64):
        raise RuntimeError("x")
    assert jabra.read([info()], time.monotonic() + 1, None, handle_factory=boom) == []
    assert jabra.read(None, time.monotonic() + 1, None, handle_factory=boom) == []
