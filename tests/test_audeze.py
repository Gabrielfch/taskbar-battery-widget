import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import audeze as az


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(az, "POLL_S", 0.005)
    monkeypatch.setattr(az, "ATTEMPT_S", 0.15)


class FakeHandle:
    """write() records; get_input_report() returns what `responder(request)` queued."""

    def __init__(self, responder):
        self.responder = responder
        self.sent = []
        self.queue = []
        self.current = None
        self.in_len = 62

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        r = self.responder(bytes(pkt))
        if r is not None:
            self.queue.extend(r if isinstance(r, list) else [r])
        return True

    def get_input_report(self, rid, length):
        assert rid == 0x07 and length == self.in_len
        if self.queue:
            self.current = self.queue.pop(0)
        return self.current


def info(pid=0x4B19, page=0xFF13, usage=1, in_len=62, out_len=62, path="p", vid=0x3329,
         product=""):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, product=product)


def run(responder, infos=None, budget=1.0, cancel=None):
    h = FakeHandle(responder)
    out = az.read(infos or [info()], time.monotonic() + budget, cancel,
                  handle_factory=lambda path, in_len=0: h)
    return out, h


def pad(b, n=62):
    return bytes(b).ljust(n, b"\0")


def reply_report(msg, n=62):
    return pad(bytes([0x07, len(msg), 0x00]) + msg, n)


def result_msg(pct):
    return bytes([5, 0x5D, 5, 0, 0xD6, 0x0C, 0, 0, pct])


def refused_msg():
    return bytes([5, 0x5B, 3, 0, 0xD6, 0x0C, 0x01])


def ok(pct=64):
    return lambda r: reply_report(result_msg(pct))


def test_request_bytes():
    out, h = run(ok())
    assert h.sent == [pad([0x06, 7, 0x80, 0x05, 0x5A, 0x03, 0x00, 0xD6, 0x0C, 0x00])]
    d, = out
    assert d.id == "audeze:4b19" and d.name == "Audeze Maxwell" and d.battery == 64
    assert d.connected and not d.charging and d.kind == "headset"
    assert d.link == "2.4ghz" and d.source == "hid"


def test_maxwell2_models():
    for pid in (0x4B29, 0x4B28):
        out, _ = run(ok(81), [info(pid)])
        assert out[0].id == f"audeze:{pid:04x}" and out[0].battery == 81
        assert out[0].name == "Audeze Maxwell 2"
    out, _ = run(ok(), [info(0x4B18)])
    assert out[0].name == "Audeze Maxwell"


def test_percent_clamped():
    out, _ = run(ok(250))
    assert out[0].battery == 100


def test_fragmented_reply_and_unrelated_first():
    msg = result_msg(42)
    other = bytes([5, 0x5D, 3, 0, 0x99, 0x01, 0x07])                 # other command result
    reports = [reply_report(b"\x00\x00"),                            # junk without marker
               reply_report(other),
               reply_report(msg[:5]), reply_report(msg[5:])]
    out, _ = run(lambda r: reports)
    assert out[0].battery == 42


def test_refused_means_unavailable():
    out, _ = run(lambda r: reply_report(refused_msg()))
    d, = out
    assert d.connected is False and d.battery is None


def test_no_reply_yields_nothing_and_bad_reports_ignored():
    out, h = run(lambda r: None, budget=1.0)
    assert out == [] and len(h.sent) == 2                                     # one retry
    for bad in (pad([0x08, 9, 0] + list(result_msg(50))),                     # wrong report id
                pad([0x07, 200, 0]),                                          # bad length
                pad([0x07, 3, 0, 5, 0x5D]),                                   # truncated
                reply_report(result_msg(50)[:8])):                            # no percentage
        out, _ = run(lambda r, b=bad: b, budget=0.6)
        assert out == []


def test_whitelist():
    for body in (b"\x00\x09\x20\x00", b"\x82\x2c\x07\x00\x01", b"\xd6\x0c", b""):
        with pytest.raises(ValueError):
            az.build_message(body)
    h = FakeHandle(lambda r: None)
    good = az.build_request()
    for bad in (pad([0x06, 8, 0x80, 5, 0x5A, 4, 0, 0x82, 0x2C, 0x07, 0]),   # write parameter
                pad([0x06, 7, 0x80, 5, 0x5A, 3, 0, 0x00, 0x09, 0x20]),      # write key
                good[:40], bytes([0x05]) + good[1:],
                good[:-1] + b"\x01"):
        with pytest.raises(ValueError):
            az._send(h, bad, 100)
    assert h.sent == []
    assert az._send(h, good, 100) and len(h.sent) == 1


def test_deadline_and_cancel_before_write():
    out, h = run(ok(), budget=-1)
    assert out == [] and h.sent == []
    c = threading.Event()
    c.set()
    out, h = run(ok(), cancel=c)
    assert out == [] and h.sent == []
    h = FakeHandle(ok())
    assert az._query(h, 62, 62, time.monotonic() - 1, None) is None and h.sent == []
    assert az._query(h, 62, 62, time.monotonic() + 1, c) is None and h.sent == []


def test_collection_selection():
    wrong = [info(page=0xFF00, path="a"), info(usage=2, path="b"), info(in_len=8, path="c"),
             info(in_len=64, path="c2"),                    # exact input length required
             info(out_len=8, path="d"), info(vid=0x046D, path="e"), info(pid=0x9999, path="f")]
    out, h = run(ok(), wrong)
    assert out == [] and h.sent == []
    out, h = run(ok(), wrong + [info(path="z1"), info(path="z2")])
    assert len(out) == 1 and len(h.sent) == 1


def test_silent_collection_falls_through_to_next():
    h_sent = []                    # first collection (2 attempts) stays silent, second answers
    h = FakeHandle(lambda r: (h_sent.append(r), None)[1] if len(h_sent) < 2 else ok(33)(r))
    out = az.read([info(path="a"), info(path="b")], time.monotonic() + 3, None,
                  handle_factory=lambda path, in_len=0: h)
    assert len(out) == 1 and out[0].battery == 33 and out[0].connected


def test_never_raises():
    def boom(path, in_len=0):
        raise OSError("x")
    assert az.read([info()], time.monotonic() + 1, None, handle_factory=boom) == []
    assert az.read(None, time.monotonic() + 1) == []
    assert az.VENDOR_IDS == (0x3329,)
