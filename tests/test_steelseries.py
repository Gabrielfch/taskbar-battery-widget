import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import steelseries as ss


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


def rep(*vals, n=64):
    """Windows-style input report: report id 0 + payload, zero padded."""
    return bytes((0,) + vals) + bytes(max(0, n - len(vals) - 1))


def nova(*vals):
    return rep(0xB0, *vals)


def info(pid, page=0xFFC0, usage=1, path="p", out_len=65, in_len=65, product="", vid=0x1038):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, product=product)


def run(replies, infos, budget=1.0, cancel=None):
    h = FakeHandle(replies)
    out = ss.read(infos, time.monotonic() + budget, cancel,
                  handle_factory=lambda path, in_len=64: h, beta=True)
    return out, h


def test_contract_constants():
    assert ss.VENDOR_IDS == (0x1038,)
    assert all(kind == "headset" for _, kind in ss.MODELS.values())
    assert 0x2202 in ss.MODELS and 0x12E0 in ss.MODELS and 0x1290 in ss.MODELS


# ---- requests ---------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,out_len", [
    (ss.REQ_LEGACY_ARCTIS1, 32), (ss.REQ_LEGACY_ARCTIS7, 32), (ss.REQ_NOVA_STATUS, 65),
    (ss.REQ_ARCTIS9, 32), (ss.REQ_NOVA_PRO, 32), (ss.REQ_PRO_CONNECTION, 32),
    (ss.REQ_PRO_BATTERY, 32),
])
def test_request_bytes_padded(cmd, out_len):
    r = ss.build_request(cmd, out_len)
    assert len(r) == out_len and r[:2] == cmd and r[2:] == bytes(out_len - 2)


def test_request_bytes_values():
    assert ss.REQ_LEGACY_ARCTIS1.hex() == "0612"
    assert ss.REQ_LEGACY_ARCTIS7.hex() == "0618"
    assert ss.REQ_NOVA_STATUS.hex() == "00b0"
    assert ss.REQ_ARCTIS9.hex() == "0020"
    assert ss.REQ_NOVA_PRO.hex() == "06b0"
    assert ss.REQ_PRO_CONNECTION.hex() == "41aa" and ss.REQ_PRO_BATTERY.hex() == "40aa"


def test_whitelist():
    h = FakeHandle([])
    for bad in (b"\x06\x35\x01\x00\x12", b"\x06\x09", b"\x00\x33", b"\x06\x53\x05", b"\x00\x39\x01"):
        with pytest.raises(ValueError):
            ss.build_request(bad, 32)
        with pytest.raises(ValueError):
            ss._send(h, bad + bytes(30 - len(bad)), 100)
    with pytest.raises(ValueError):
        ss.build_request(ss.REQ_NOVA_STATUS, 1)
    assert h.sent == []


# ---- reply parsing per family -----------------------------------------------------------------

def test_arctis1():
    out, h = run([rep(0x06, 0x12, 0x00, 55, n=32)], [info(0x12B3, 0xFF43, 0x202, out_len=32, in_len=32)])
    assert h.sent == [ss.build_request(ss.REQ_LEGACY_ARCTIS1, 32)]
    d, = out
    assert d.id == "steelseries:12b3" and d.kind == "headset" and d.connected and not d.charging
    assert d.battery == 55 and d.link == "2.4ghz" and d.source == "hid"
    assert d.name == "SteelSeries Arctis 1"
    out, _ = run([bytes([0x06, 0x12, 0x01, 0x00]) + bytes(28)],
                 [info(0x12D7, 0xFF43, 0x202, out_len=32, in_len=32)])
    assert not out[0].connected and out[0].battery is None


def test_arctis7():
    out, h = run([bytes([0x07, 0x18, 80]) + bytes(29)], [info(0x1260, 0xFF43, 0x202, out_len=32, in_len=32)])
    assert h.sent == [ss.build_request(ss.REQ_LEGACY_ARCTIS7, 32)]
    assert out[0].battery == 80 and out[0].connected and out[0].id == "steelseries:1260"


def test_arctis9_scaling_and_charging():
    p = "\\\\?\\hid#vid_1038&pid_12c2&mi_00#x"

    def r(bat, st):
        return rep(0, 0, 0, bat, st, n=12)
    inf = [info(0x12C2, 0xFF00, 1, path=p, out_len=32, in_len=12)]
    out, h = run([r(0x64, 0)], inf)
    assert out[0].battery == 0 and not out[0].charging
    assert h.sent == [ss.build_request(ss.REQ_ARCTIS9, 32)]
    assert run([r(0x9A, 0)], inf)[0][0].battery == 100
    assert run([r(0xFF, 0)], inf)[0][0].battery == 100          # clamped
    out, _ = run([r(0x7F, 1)], inf)
    assert out[0].charging and out[0].battery == round((0x7F - 0x64) * 100 / 0x36)
    assert run([r(0x20, 0)], inf)[0][0].battery == 0            # clamped low


def test_arctis7plus():
    inf = [info(0x220E)]
    out, h = run([nova(0, 3, 0)], inf)
    assert h.sent == [ss.build_request(ss.REQ_NOVA_STATUS, 65)]
    assert out[0].battery == 75 and not out[0].charging and out[0].connected
    out, _ = run([nova(0, 4, 1)], inf)
    assert out[0].battery == 100 and out[0].charging
    out, _ = run([nova(1, 4, 0)], inf)
    assert not out[0].connected and out[0].battery is None


def test_gamebuds():
    inf = [info(0x230A)]
    out, _ = run([nova(0, 0, 3, 3, 80, 60)], inf)
    assert out[0].battery == 60 and out[0].connected
    out, _ = run([nova(0, 0, 3, 2, 80, 0)], inf)
    assert out[0].battery == 80
    out, _ = run([nova(0, 0, 2, 2, 0, 0)], inf)
    assert not out[0].connected and out[0].battery is None
    out, _ = run([nova(0, 0, 3, 3, 200, 150)], inf)
    assert out[0].battery == 100


@pytest.mark.parametrize("pid", [0x2269, 0x226D])
def test_nova3(pid):
    inf = [info(pid)]
    out, _ = run([nova(0, 0, 42, 0)], inf)
    assert out[0].battery == 42 and not out[0].charging
    out, _ = run([nova(0, 0, 90, 1)], inf)
    assert out[0].battery == 90 and out[0].charging
    out, _ = run([nova(2, 0, 90, 0)], inf)
    assert not out[0].connected and out[0].battery is None
    out, _ = run([nova(0, 0, 250, 0)], inf)
    assert out[0].battery == 100


@pytest.mark.parametrize("pid", [0x2232, 0x2253])
def test_nova5(pid):
    inf = [info(pid)]
    out, _ = run([nova(0, 0, 77, 1)], inf)
    assert out[0].battery == 77 and out[0].charging
    out, _ = run([nova(2, 0, 0, 0)], inf)
    assert not out[0].connected
    # shorter than the 16 bytes the layout needs -> ignored, treated as no answer (no device)
    out, _ = run([bytes([0, 0xB0, 0, 0, 77, 0])], inf, budget=0.4)
    assert out == []


@pytest.mark.parametrize("pid,raw,pct", [
    (0x2202, 2, 50), (0x2206, 4, 100), (0x220A, 1, 25), (0x223A, 3, 75), (0x227A, 0, 0), (0x22A4, 4, 100),
    (0x22A1, 63, 63), (0x227E, 100, 100), (0x2258, 12, 12), (0x22A7, 99, 99), (0x2298, 5, 5),
])
def test_nova7_scaling(pid, raw, pct):
    out, _ = run([nova(0, raw, 0x05)], [info(pid)])
    assert out[0].battery == pct and out[0].connected and not out[0].charging


def test_nova7_charging_and_off():
    out, _ = run([nova(0, 3, 0x01)], [info(0x2202)])
    assert out[0].charging and out[0].battery == 75
    out, _ = run([nova(0, 80, 0x02)], [info(0x22A5)])
    assert out[0].charging and out[0].battery == 80
    out, _ = run([nova(0, 3, 0x00)], [info(0x2202)])
    assert not out[0].connected and out[0].battery is None


def test_nova_pro():
    p = "\\\\?\\hid#vid_1038&pid_12e0&mi_04#x"
    inf = [info(0x12E0, 0xFFC0, 1, path=p, out_len=32, in_len=32)]

    def r(bat, st):
        b = bytearray(31)
        b[0] = 0x06
        b[6] = bat
        b[15] = st
        return bytes([0]) + bytes(b)
    out, h = run([r(4, 0x08)], inf)
    assert h.sent == [ss.build_request(ss.REQ_NOVA_PRO, 32)]
    assert out[0].battery == 50 and out[0].connected and not out[0].charging
    out, _ = run([r(8, 0x02)], inf)
    assert out[0].battery == 100 and out[0].charging
    out, _ = run([r(8, 0x01)], inf)
    assert not out[0].connected and out[0].battery is None
    out, _ = run([r(20, 0x08)], inf)
    assert out[0].battery == 100


def test_pro_wireless_two_step():
    p = "\\\\?\\hid#vid_1038&pid_1290&mi_00#x"
    inf = [info(0x1290, 0xFF00, 1, path=p, out_len=32, in_len=32)]
    out, h = run([bytes([0x04, 0]), bytes([0x03, 0])], inf)
    assert h.sent == [ss.build_request(ss.REQ_PRO_CONNECTION, 32),
                      ss.build_request(ss.REQ_PRO_BATTERY, 32)]
    assert out[0].battery == 75 and out[0].connected
    out, h = run([bytes([0x02, 0])], inf)
    assert len(h.sent) == 1 and not out[0].connected and out[0].battery is None
    out, h = run([bytes([0x04, 0])], inf, budget=0.4)                # battery query unanswered
    assert len(h.sent) == 2 and out == []


# ---- robustness -------------------------------------------------------------------------------

def test_payload_strips_unnumbered_id_only():
    assert ss.payload(b"\x00\xb0\x01") == b"\xb0\x01"
    assert ss.payload(b"\x06\x12") == b"\x06\x12"
    assert ss.payload(None) is None and ss.payload(b"") is None


def test_unrelated_reports_skipped_for_status_family():
    junk = rep(0x07, 0x25, 0x01)
    out, h = run([junk, rep(0x20, 1), nova(0, 4, 0)], [info(0x220E)])
    assert out[0].battery == 100 and len(h.sent) == 1


def test_short_and_empty_replies():
    for bad in ([b""], [None], [bytes([0, 0xB0])], [rep(0x20, 0, 4, 0)]):
        out, _ = run(bad, [info(0x220E)], budget=0.3)
        assert out == []                                            # silence is not "off"


def test_no_reply():
    out, h = run([], [info(0x2269)], budget=0.4)
    assert out == [] and len(h.sent) == 1


def test_deadline_and_cancel_before_write():
    out, h = run([nova(0, 4, 0)], [info(0x220E)], budget=-1)
    assert out == [] and h.sent == []
    c = threading.Event()
    c.set()
    out, h = run([nova(0, 4, 0)], [info(0x220E)], cancel=c)
    assert out == [] and h.sent == []
    fam = ss._BY_PID[0x220E]
    h = FakeHandle([nova(0, 4, 0)])
    assert ss._query(h, fam, ss.REQ_NOVA_STATUS, 4, 65, time.monotonic() - 1, None) is None
    assert ss._query(h, fam, ss.REQ_NOVA_STATUS, 4, 65, time.monotonic() + 1, c) is None
    assert h.sent == []


def test_cancel_inside_read_loop():
    c = threading.Event()

    class H(FakeHandle):
        def read(self, timeout_ms=0):
            c.set()
            return None
    h = H([])
    out = ss.read([info(0x220E)], time.monotonic() + 2, c, handle_factory=lambda p, in_len=64: h)
    assert out == [] or not out[0].connected
    assert len(h.sent) <= 1


def test_collection_selection():
    ok = info(0x220E, path="ok")
    infos = [info(0x220E, 0xFF00, 1, path="a"), info(0x220E, 0xFFC0, 2, path="b"),
             info(0x220E, 0x000C, 1, path="c"), info(0x220E, path="d", in_len=0),
             info(0x220E, path="e", out_len=0), info(0x220E, path="f", vid=0x046D),
             info(0x1234, path="g")]
    out, h = run([nova(0, 4, 0)], infos)
    assert out == [] and h.sent == []
    out, h = run([nova(0, 4, 0)], infos + [ok, info(0x220E, path="z")])
    assert len(out) == 1 and len(h.sent) == 1


def test_interface_selection_without_usage_page():
    good = "\\\\?\\hid#vid_1038&pid_12c2&mi_00#x"
    bad = "\\\\?\\hid#vid_1038&pid_12c2&mi_03#x"
    r = rep(0, 0, 0, 0x9A, 0, n=12)
    out, h = run([r], [info(0x12C2, 0xFF00, 1, path=bad, out_len=32, in_len=12),
                       info(0x12C2, 0x0001, 6, path=good, out_len=32, in_len=12)])
    assert out == [] and h.sent == []
    out, h = run([r], [info(0x12C2, 0xFF00, 1, path=bad), info(0x12C2, 0xFF00, 1, path=good)])
    assert len(out) == 1 and out[0].battery == 100


def test_product_name_sanitized():
    out, _ = run([nova(0, 4, 0)], [info(0x220E, product="Arctis <7+>  Wireless")])
    assert out[0].name == "Arctis 7+ Wireless"


def test_multiple_dongles():
    h = FakeHandle([nova(0, 4, 0), nova(0, 0, 33, 0)])
    out = ss.read([info(0x220E, path="a"), info(0x2269, path="b")], time.monotonic() + 1, None,
                  handle_factory=lambda p, in_len=64: h)
    assert [d.id for d in out] == ["steelseries:220e", "steelseries:2269"]
    assert [d.battery for d in out] == [100, 33]


def test_never_raises():
    def boom(path, in_len=64):
        raise OSError("x")
    assert ss.read([info(0x220E)], time.monotonic() + 1, None, handle_factory=boom) == []
    assert ss.read(None, time.monotonic() + 1) == []


def test_beta_families_skipped_without_beta_and_stable_always_read():
    assert {f.key for f in ss.FAMILIES if f.beta} == {"arctis1", "arctis7", "arctis9", "nova_pro",
                                                       "pro_wireless"}
    opened = []

    def rec(path, in_len=64):
        opened.append(path)
        raise OSError("x")
    old = info(0x1260, 0xFF43, 0x202)
    assert ss.read([old], time.monotonic() + 1, None, handle_factory=rec) == []
    assert opened == []
    ss.read([old], time.monotonic() + 1, None, handle_factory=rec, beta=True)
    assert len(opened) == 1
    ss.read([info(0x220E)], time.monotonic() + 1, None, handle_factory=rec)   # arctis7plus: stable
    assert len(opened) == 2
