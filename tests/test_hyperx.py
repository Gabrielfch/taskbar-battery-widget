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
                         handle_factory=lambda path, in_len=20: h, beta=True)
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


# ---------------------------------------------------------------------------------------------
# Other models (Cloud II, Cloud Alpha, Cloud Alpha 2)
# ---------------------------------------------------------------------------------------------

class ScriptHandle(FakeHandle):
    """Answers each written request through `responder(request) -> reply bytes | None`."""

    def __init__(self, responder):
        super().__init__([])
        self.responder = responder
        self.feature_calls = []

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        r = self.responder(bytes(pkt))
        if r is not None:
            self.replies.append(r)
        return True

    def get_input_report(self, rid, n):
        self.feature_calls.append((rid, n))
        return None


def minfo(vid, pid, page, usage, in_len, out_len, path="p", product=""):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, product=product)


def mrun(responder, infos, budget=1.0, cancel=None):
    h = ScriptHandle(responder)
    out = hx.read_hyperx(infos, time.monotonic() + budget, cancel,
                         handle_factory=lambda path, in_len=0: h, beta=True)
    return out, h


def pad(b, n):
    return bytes(b).ljust(n, b"\0")


HP2 = minfo(0x03F0, 0x0696, 0xFF90, 0x0303, 20, 52)
KS2 = minfo(0x0951, 0x1718, 0xFF00, 1, 64, 62)
ALPHA = minfo(0x03F0, 0x098D, 0xFF00, 1, 31, 31)
ALPHA2 = minfo(0x03F0, 0x08BE, 0xFF13, 0xFF00, 64, 64)


def cloud2_reply(req, level=73, charging=0):
    cmd = req[3]
    if cmd == 2:
        return pad([6, 0xFF, 0xBB, 2, 0, 0x0F, 0xA0, level], 20)
    return pad([6, 0xFF, 0xBB, 3, charging], 20)


def test_vendor_ids_include_hp():
    assert hx.VENDOR_IDS == (0x0951, 0x03F0)


def test_cloud2_hp_requests_and_parse():
    out, h = mrun(cloud2_reply, [HP2])
    assert h.sent == [pad([6, 0xFF, 0xBB, 2], 52), pad([6, 0xFF, 0xBB, 3], 52)]
    d, = out
    assert d.id == "hyperx:0696" and d.battery == 73 and d.connected and not d.charging
    assert d.kind == "headset" and d.link == "2.4ghz" and d.source == "hid"
    assert d.name == "HyperX Cloud II Wireless"


def test_cloud2_hp_charging_and_second_pid():
    out, _ = mrun(lambda r: cloud2_reply(r, 40, 1),
                  [minfo(0x03F0, 0x018B, 0xFF90, 0x0303, 20, 52)])
    assert out[0].id == "hyperx:018b" and out[0].charging and out[0].battery == 40


def test_cloud2_hp_bad_replies():
    out, _ = mrun(lambda r: bytes([6, 0xFF, 0xBB, 2]), [HP2], budget=0.4)       # too short
    assert out == []                                                            # silence is not "off"
    out, _ = mrun(lambda r: pad([9, 9, 9, 9], 20), [HP2], budget=0.4)           # unrelated
    assert out == []
    out, _ = mrun(lambda r: cloud2_reply(r, 250), [HP2])                        # out of range
    assert out == []


def test_cloud2_hp_unrelated_report_skipped():
    h = ScriptHandle(cloud2_reply)
    h.replies.append(pad([1, 2, 3], 20))              # stale/unrelated first
    out = hx.read_hyperx([HP2], time.monotonic() + 1, None, handle_factory=lambda p, in_len=0: h)
    assert out[0].battery == 73


KS_HEAD = bytes.fromhex("06000200" "9a000068" "4a8e0a" "000000" "bb")


def ks_reply(req, level=55, charging=0):
    cmd = req[15]
    if cmd == 2:
        return pad([0x0B, 0, 0xBB, 2, 0, 0, 0, level], 64)
    return pad([0x0B, 0, 0xBB, 3, charging], 64)


def test_cloud2_kingston():
    out, h = mrun(ks_reply, [KS2])
    assert all(len(s) == 62 and s[:15] == KS_HEAD and s[16:] == bytes(46) for s in h.sent)
    assert [s[15] for s in h.sent] == [2, 3]
    assert h.feature_calls == [(6, 64)]
    d, = out
    assert d.id == "hyperx:1718" and d.battery == 55 and d.connected and not d.charging
    out, _ = mrun(lambda r: ks_reply(r, 90, 1), [KS2])
    assert out[0].charging and out[0].battery == 90
    out, _ = mrun(lambda r: pad([6, 0, 0xBB, 2, 0, 0, 0, 50], 64), [KS2], budget=0.4)
    assert out == []


def alpha_reply(req, connected=0, charging=0, level=64):
    cmd = req[2]
    return pad([0x21, 0xBB, cmd, {3: connected, 0x0C: charging, 0x0B: level}[cmd]], 31)


def test_alpha_wireless():
    out, h = mrun(alpha_reply, [ALPHA])
    assert [s[:3] for s in h.sent] == [b"\x21\xbb\x03", b"\x21\xbb\x0c", b"\x21\xbb\x0b"]
    assert all(len(s) == 31 and s[3:] == bytes(28) for s in h.sent)
    d, = out
    assert d.id == "hyperx:098d" and d.battery == 64 and d.connected and not d.charging


def test_alpha_wireless_off_and_charging():
    out, h = mrun(lambda r: alpha_reply(r, connected=1), [ALPHA])
    assert out[0].connected is False and out[0].battery is None and len(h.sent) == 1
    hx._last_battery.clear()
    out, h = mrun(lambda r: alpha_reply(r, charging=1), [ALPHA])
    assert out[0].charging and out[0].connected and out[0].battery is None and len(h.sent) == 2
    mrun(alpha_reply, [ALPHA])
    out, _ = mrun(lambda r: alpha_reply(r, charging=1), [ALPHA])
    assert out[0].battery == 64
    out, _ = mrun(lambda r: alpha_reply(r, level=200), [ALPHA])
    assert out == []
    out, _ = mrun(lambda r: None, [ALPHA], budget=0.4)
    assert out == []


def alpha2_reply(req, connected=1, level=88, charge=0):
    if req[0] == 0x52:
        return pad([0x53, 0x01, connected], 64)
    return pad([0x51, 0x02, level, charge, 0x10, 0x00, 0x68, 0x10], 64)


def test_alpha2_wireless():
    out, h = mrun(alpha2_reply, [ALPHA2])
    assert h.sent == [pad([0x52, 0x01], 64), pad([0x50, 0x02], 64)]
    d, = out
    assert d.id == "hyperx:08be" and d.battery == 88 and d.connected and not d.charging
    assert d.name == "HyperX Cloud Alpha 2 Wireless"
    out, _ = mrun(lambda r: alpha2_reply(r, charge=1), [ALPHA2])
    assert out[0].charging


def test_alpha2_off_and_errors():
    out, h = mrun(lambda r: alpha2_reply(r, connected=0), [ALPHA2])
    assert out[0].connected is False and out[0].battery is None and len(h.sent) == 1
    out, _ = mrun(lambda r: alpha2_reply(r, level=101), [ALPHA2])
    assert out == []
    out, _ = mrun(lambda r: pad([0xFF, 0x02], 64), [ALPHA2], budget=0.4)    # NACK / unrelated
    assert out == []


def test_model_whitelist():
    for key, cmd in [("cloud2", 0x22), ("cloud2", 0x07), ("cloud2k", 0x18), ("cloud2k", 0x19),
                     ("alpha", 0x10), ("alpha", 0x12), ("alpha", 0x13),
                     ("alpha2", (0x60, 0x01)), ("alpha2", (0x60, 0x02)), ("nope", 2)]:
        with pytest.raises(ValueError):
            hx.build_model_request(key, cmd)
    h = FakeHandle([])
    with pytest.raises(ValueError):
        hx._send_model(h, pad([0x06, 0xFF, 0xBB, 0x22], 52), 52, 100)
    assert h.sent == []
    assert hx.build_model_request("alpha2", (0x50, 0x02)) == pad([0x50, 0x02], 64)


def test_models_deadline_and_cancel():
    for i in (HP2, KS2, ALPHA, ALPHA2):
        out, h = mrun(cloud2_reply, [i], budget=-1)
        assert out == [] and h.sent == []
        c = threading.Event()
        c.set()
        out, h = mrun(cloud2_reply, [i], cancel=c)
        assert out == [] and h.sent == []
    h = ScriptHandle(cloud2_reply)
    assert hx._ask(h, "cloud2", 2, 52, time.monotonic() - 1, None) is None and h.sent == []


def test_models_collection_selection():
    wrong = [minfo(0x03F0, 0x0696, 0xFF00, 0x0303, 20, 52, "a"),     # wrong page
             minfo(0x03F0, 0x0696, 0xFF90, 0x0001, 20, 52, "b"),     # wrong usage
             minfo(0x03F0, 0x0696, 0xFF90, 0x0303, 64, 64, "c"),     # wrong lengths
             minfo(0x0951, 0x0696, 0xFF90, 0x0303, 20, 52, "d"),     # wrong vid
             minfo(0x03F0, 0x08BE, 0x000C, 1, 64, 64, "e"),
             minfo(0x0951, 0x1718, 0x0001, 1, 64, 62, "f"),          # not a vendor page
             minfo(0x03F0, 0x089D, 0xFF13, 1, 64, 64, "g")]          # Cloud 3: no battery query
    out, h = mrun(cloud2_reply, wrong)
    assert out == [] and h.sent == []
    out, h = mrun(cloud2_reply, wrong + [minfo(0x03F0, 0x0696, 0xFF90, 0x0303, 20, 52, "z1"),
                                        minfo(0x03F0, 0x0696, 0xFF90, 0x0303, 20, 52, "z2")])
    assert len(out) == 1 and len(h.sent) == 2


def test_models_never_raise():
    def boom(path, in_len=0):
        raise OSError("x")
    assert hx.read_hyperx([HP2, ALPHA2, KS2, ALPHA], time.monotonic() + 1, None,
                          handle_factory=boom) == []
    assert hx.read is hx.read_hyperx


def test_flight_and_models_together():
    def resp(r):
        return cloud2_reply(r) if r[0] == 6 else LIVE
    out, h = mrun(resp, [info(), HP2])
    assert {d.id for d in out} == {"hyperx:16c4", "hyperx:0696"}


def test_kingston_and_alpha_pin_exact_usage():
    for inf in (minfo(0x0951, 0x1718, 0xFF01, 1, 64, 62), minfo(0x0951, 0x1718, 0xFF00, 2, 64, 62),
                minfo(0x03F0, 0x098D, 0xFFA0, 1, 31, 31), minfo(0x03F0, 0x098D, 0xFF00, 5, 31, 31)):
        out, h = mrun(cloud2_reply, [inf])
        assert out == [] and h.sent == []


def test_beta_models_flagged_and_skipped_without_beta():
    flags = {(m.vid, p): m.beta for m in hx.MODELS for p in m.pids}
    assert flags == {(0x03F0, 0x0696): False, (0x03F0, 0x018B): False, (0x0951, 0x1718): True,
                     (0x03F0, 0x098D): True, (0x03F0, 0x08BE): False}
    opened = []

    def rec(path, in_len=0):
        opened.append(path)
        raise OSError("x")
    for beta_info in (KS2, ALPHA):
        assert hx.read_hyperx([beta_info], time.monotonic() + 1, None, handle_factory=rec) == []
    assert opened == []
    hx.read_hyperx([KS2, ALPHA], time.monotonic() + 1, None, handle_factory=rec, beta=True)
    assert len(opened) == 2
    opened.clear()
    for stable in (HP2, ALPHA2):                # stable models are always read
        hx.read_hyperx([stable], time.monotonic() + 1, None, handle_factory=rec)
    assert len(opened) == 2
