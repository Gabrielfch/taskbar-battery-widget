import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import misc_headsets as mh


class FakeHandle:
    """responder(pkt) -> list of input reports queued after each write / set_feature."""

    def __init__(self, responder=None, fail_write=False):
        self.responder = responder
        self.fail_write = fail_write
        self.sent = []
        self.features = []
        self.queue = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        self.queue = []

    def _on(self, pkt):
        if self.responder:
            self.queue.extend(self.responder(bytes(pkt)))

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        if self.fail_write:
            return False
        self._on(pkt)
        return True

    def set_feature(self, data):
        self.features.append(bytes(data))
        if self.fail_write:
            return False
        self._on(data)
        return True

    def read(self, timeout_ms=0):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(min(timeout_ms, 20) / 1000)
        return None


def info(vid, pid, page, usage, n, path="p", product="", feat=0):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage, in_len=n,
                           out_len=n, feat_len=feat, product=product)


def run(h, infos, budget=1.0, cancel=None):
    return mh.read(infos, time.monotonic() + budget, cancel, handle_factory=lambda p, in_len=64: h)


def test_vendor_ids():
    assert set(mh.VENDOR_IDS) == {0x17EF, 0x3837, 0x291D}


# ------------------------------------------------------------------ Lenovo
LEN = (0x17EF, 0xA07D, 0xFF07, 0x222, 61)


def len_reply(cmd=1, err=0, level=55, rid=0x27, n=61):
    b = bytearray(n)
    b[0], b[1], b[2], b[7] = rid, cmd, err, level
    return bytes(b)


def test_lenovo_request_bytes():
    r = mh.build_lenovo_request(1)
    assert len(r) == 61 and r[:2] == b"\x24\x01" and r[2:] == bytes(59)


@pytest.mark.parametrize("cmd", [0x00, 0x02, 0x0E, 0x07, 0x15])
def test_lenovo_whitelist(cmd):
    with pytest.raises(ValueError):
        mh.build_lenovo_request(cmd)


def test_lenovo_parse():
    assert mh.parse_lenovo_status(len_reply(level=55)) == ("ok", 55)
    assert mh.parse_lenovo_status(len_reply(err=1)) == ("off", None)


@pytest.mark.parametrize("buf", [None, b"", len_reply(cmd=2), len_reply(rid=0x24), len_reply(n=60),
                                 len_reply(level=200)])
def test_lenovo_parse_ignores(buf):
    assert mh.parse_lenovo_status(buf) is None


def test_lenovo_read_uses_feature_report():
    h = FakeHandle(lambda p: [len_reply(cmd=2), len_reply(level=88)])
    out = run(h, [info(*LEN, feat=61)])
    assert h.sent == [] and h.features == [mh.build_lenovo_request(1)]
    d = out[0]
    assert (d.id, d.name, d.battery, d.charging, d.connected, d.link) == (
        "lenovo:a07d", "Lenovo Wireless VoIP Headset", 88, False, True, "2.4ghz")


def test_lenovo_headset_off():
    out = run(FakeHandle(lambda p: [len_reply(err=1)]), [info(*LEN, feat=61)])
    assert out[0].connected is False and out[0].battery is None


def test_lenovo_silent_yields_nothing():
    out = run(FakeHandle(lambda p: []), [info(*LEN, feat=61)], budget=0.4)
    assert out == []


def test_lenovo_set_feature_failure_yields_nothing():
    out = run(FakeHandle(None, fail_write=True), [info(*LEN, feat=61)])
    assert out == []


def test_lenovo_requires_feature_length():
    h = FakeHandle(lambda p: [len_reply()])
    assert run(h, [info(*LEN, feat=0)]) == [] and h.features == []


# ------------------------------------------------------------------ MCHOSE
X9 = (0x3837, 0x6045, 0xFF90, 0x01, 64)
V9 = (0x291D, 0x385D, 0xFF90, 0x01, 64)


def mc_reply(pct=50, field=2, rid=0x55, typ=0x65):
    return bytes([rid, typ, pct, field]) + bytes(60)


def test_mchose_request_bytes():
    r = mh.build_mchose_request(1)
    assert len(r) == 64 and r[:4] == b"\x55\x65\x01\x00" and r[4:] == bytes(60)


@pytest.mark.parametrize("q", [0, 2, 0x10, 0xFF])
def test_mchose_whitelist(q):
    with pytest.raises(ValueError):
        mh.build_mchose_request(q)


@pytest.mark.parametrize("buf", [None, b"", b"\x55\x65\x32", mc_reply(field=3), mc_reply(rid=0x56),
                                 mc_reply(typ=0x66), mc_reply(pct=101)])
def test_mchose_parse_ignores(buf):
    assert mh.parse_mchose_battery(buf) is None


@pytest.mark.parametrize("model,dev_id,name", [
    (X9, "mchose:6045", "MCHOSE X9 Wireless"),
    (V9, "mchose:385d", "MCHOSE V9 Pro Wireless"),
])
def test_mchose_read(model, dev_id, name):
    h = FakeHandle(lambda p: [mc_reply(field=7, pct=1), mc_reply(pct=50)])
    out = run(h, [info(*model)])
    assert h.sent == [mh.build_mchose_request(1)]
    d = out[0]
    assert (d.id, d.name, d.battery, d.connected, d.charging, d.link) == (
        dev_id, name, 50, True, False, "2.4ghz")


def test_mchose_silent_yields_nothing():
    out = run(FakeHandle(lambda p: []), [info(*X9)], budget=0.4)
    assert out == []


def test_mchose_write_failure():
    assert run(FakeHandle(None, fail_write=True), [info(*X9)]) == []


# ------------------------------------------------------------------ shared behaviour
@pytest.mark.parametrize("kw", [
    dict(vid=0x17EF, pid=0x0001, page=0xFF07, usage=0x222, n=61),   # unknown pid
    dict(vid=0x0951, pid=0xA07D, page=0xFF07, usage=0x222, n=61),   # wrong vendor
    dict(vid=0x17EF, pid=0xA07D, page=0xFF00, usage=0x222, n=61),   # wrong usage page
    dict(vid=0x17EF, pid=0xA07D, page=0xFF07, usage=1, n=61),       # wrong usage
    dict(vid=0x17EF, pid=0xA07D, page=0xFF07, usage=0x222, n=32),   # wrong report length
    dict(vid=0x3837, pid=0x385D, page=0xFF90, usage=1, n=64),       # V9 pid under X9 vendor
    dict(vid=0x047F, pid=0x02EE, page=0xFFA2, usage=3, n=64),       # Plantronics BT600: not handled
])
def test_collection_selection(kw):
    h = FakeHandle(lambda p: [mc_reply()])
    infos = [info(kw["vid"], kw["pid"], kw["page"], kw["usage"], kw["n"])]
    assert run(h, infos) == [] and h.sent == [] and h.features == []


def test_deadline_before_write():
    h = FakeHandle(lambda p: [mc_reply()])
    assert run(h, [info(*X9)], budget=-1) == [] and h.sent == []


def test_cancel_before_write():
    ev = threading.Event()
    ev.set()
    h = FakeHandle(lambda p: [mc_reply()])
    assert run(h, [info(*X9)], cancel=ev) == [] and h.sent == []


def test_cancel_while_waiting_yields_nothing():
    ev = threading.Event()

    def responder(pkt):
        ev.set()
        return []
    assert run(FakeHandle(responder), [info(*X9)], cancel=ev) == []


def test_multiple_models_in_one_scan():
    handles = {"a": FakeHandle(lambda p: [mc_reply(pct=30)]),
               "b": FakeHandle(lambda p: [len_reply(level=70)])}
    infos = [info(*X9, path="a"), info(*LEN, path="b", feat=61)]
    out = mh.read(infos, time.monotonic() + 2, None,
                  handle_factory=lambda path, in_len=64: handles[path])
    assert sorted((d.id, d.battery) for d in out) == [("lenovo:a07d", 70), ("mchose:6045", 30)]


def test_never_raises():
    def boom(path, in_len=64):
        raise RuntimeError("x")
    assert mh.read([info(*X9)], time.monotonic() + 1, None, handle_factory=boom) == []
    assert mh.read(None, time.monotonic() + 1, None, handle_factory=boom) == []
