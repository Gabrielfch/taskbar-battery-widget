import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import corsair as cs


class FakeHandle:
    """Answers each written request through `responder(request) -> reply | None | list`."""

    def __init__(self, responder):
        self.responder = responder
        self.sent = []
        self.replies = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def write(self, pkt, timeout_ms=1000):
        self.sent.append(bytes(pkt))
        r = self.responder(bytes(pkt))
        if r is not None:
            self.replies.extend(r if isinstance(r, list) else [r])
        return True

    def read(self, timeout_ms=0):
        if self.replies:
            return self.replies.pop(0)
        time.sleep(min(timeout_ms, 20) / 1000)
        return None


def info(pid, page, usage=1, in_len=64, out_len=64, path="p", vid=0x1B1C, product=""):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, product=product)


def run(responder, infos, budget=1.0, cancel=None):
    h = FakeHandle(responder)
    out = cs.read(infos, time.monotonic() + budget, cancel,
                  handle_factory=lambda path, in_len=0: h)
    return out, h


def pad(b, n):
    return bytes(b).ljust(n, b"\0")


# ---------------------------------------------------------------------------------------------
# Void (legacy)
# ---------------------------------------------------------------------------------------------

VOID = info(0x0A14, 0xFFC5, in_len=5, out_len=64)


def void_reply(level=77, status=1, mic=False):
    return bytes([0x64, 0, level | (0x80 if mic else 0), 0xB1, status])


def test_void_request_and_parse():
    out, h = run(lambda r: void_reply(), [VOID])
    assert h.sent == [pad([0xC9, 0x64], 64)]
    d, = out
    assert d.id == "corsair:0a14" and d.battery == 77 and d.connected and not d.charging
    assert d.kind == "headset" and d.link == "2.4ghz" and d.source == "hid"
    assert d.name == "Corsair Void"


@pytest.mark.parametrize("status,charging", [(1, False), (2, False), (4, True), (5, True)])
def test_void_status_codes(status, charging):
    out, _ = run(lambda r: void_reply(40, status), [VOID])
    assert out[0].charging is charging and out[0].battery == 40


def test_void_mic_flag_masked():
    out, _ = run(lambda r: void_reply(55, 1, mic=True), [VOID])
    assert out[0].battery == 55


def test_void_explicit_offline_status():
    out, _ = run(lambda r: void_reply(0, 0), [VOID])
    assert out[0].connected is False and out[0].battery is None


def test_void_silence_and_bad_replies_yield_nothing():
    out, _ = run(lambda r: None, [VOID], budget=0.4)
    assert out == []
    for bad in (b"\x64\0\x10", bytes([0x65, 0, 50, 0xB1, 1]), bytes([0x64, 0, 50, 0xB1, 9]),
                bytes([0x64, 0, 120, 0xB1, 1])):
        out, _ = run(lambda r, b=bad: b, [VOID], budget=0.4)
        assert out == []


def test_void_write_failure_yields_nothing():
    class H(FakeHandle):
        def write(self, pkt, timeout_ms=1000):
            self.sent.append(bytes(pkt))
            return False
    h = H(lambda r: void_reply())
    assert cs.read([VOID], time.monotonic() + 1, None, handle_factory=lambda p, in_len=0: h) == []


def test_void_parse_helpers():
    assert cs.parse_void_reply(None) is None
    assert cs.parse_void_reply(void_reply(10, 4)) == ("charging", 10, False)
    assert cs.parse_void_reply(void_reply(90, 1, mic=True)) == ("ok", 90, True)


# ---------------------------------------------------------------------------------------------
# Virtuoso XT (Bragi, report ids)
# ---------------------------------------------------------------------------------------------

XT = info(0x0A64, 0xFF42)
XT_WIRED = info(0x0A62, 0xFF42)


def xt_responder(level_tenths=735, charge=0, src=1, status=0, dead_targets=()):
    def resp(r):
        target, prop = r[1], r[3]
        if target in dead_targets:
            return None
        s = 0 if target == 8 else src
        val = level_tenths if prop == 0x0F else charge
        return pad([1, s, 2, status, val & 0xFF, val >> 8], 64)
    return resp


def test_xt_requests_and_parse():
    out, h = run(xt_responder(), [XT])
    assert h.sent == [pad([2, 9, 2, 0x0F], 64), pad([2, 9, 2, 0x10], 64)]
    d, = out
    assert d.id == "corsair:0a64" and d.battery == 73 and d.connected and not d.charging
    assert d.link == "2.4ghz" and d.name == "Corsair Virtuoso XT"


def test_xt_charging_and_se_name():
    out, _ = run(xt_responder(500, charge=1), [info(0x0A3E, 0xFF42)])
    assert out[0].charging and out[0].battery == 50 and out[0].name == "Corsair Virtuoso SE"


def test_xt_wired_probes_self_first():
    out, h = run(xt_responder(910, src=1), [XT_WIRED])
    assert h.sent[0] == pad([2, 8, 2, 0x0F], 64)
    d, = out
    assert d.battery == 91 and d.link == "usb"


def test_xt_falls_back_to_alternate_target():
    out, h = run(xt_responder(600, dead_targets=(9,)), [XT], budget=1.5)
    assert [s[1] for s in h.sent[:2]] == [9, 8] and out[0].battery == 60 and out[0].connected


def test_xt_explicit_error_status_is_off():
    out, _ = run(xt_responder(status=5), [XT], budget=1.5)
    assert out[0].connected is False and out[0].battery is None


def test_xt_silence_yields_nothing():
    out, _ = run(lambda r: None, [XT], budget=1.5)
    assert out == []


def test_xt_bad_replies():
    out, _ = run(xt_responder(2000), [XT], budget=1.5)                    # out of range
    assert out == []
    out, _ = run(lambda r: pad([1, 1, 2], 4), [XT], budget=0.8)           # short
    assert out == []
    out, _ = run(lambda r: pad([7, 1, 2, 0, 1, 1], 64), [XT], budget=0.8)  # unrelated report
    assert out == []
    out, _ = run(lambda r: pad([1, 1, 9, 0, 1, 1], 64), [XT], budget=0.8)  # other command
    assert out == []


def test_xt_wrong_source_ignored():
    # a reply from the receiver (src 0) must not answer a headset (9) query
    out, _ = run(lambda r: pad([1, 0, 2, 0, 0xE7, 0x02], 64), [XT], budget=1.5)
    assert out[0].battery == 74                                           # via fallback to target 8


# ---------------------------------------------------------------------------------------------
# Void Wireless V2 (Bragi, no report ids)
# ---------------------------------------------------------------------------------------------

V2 = info(0x2A08, 0xFF00, in_len=65, out_len=65)


def v2_responder(tenths=(820,), headset_alive=True, lead_zero=True, ids=False, hb_status=0,
                 batt_status=0):
    seq = list(tenths)

    def resp(r):
        if ids:                                     # 64-byte collection: the report id is the frame's 1st byte
            assert len(r) == 64 and r[0] == 2
            target, prop = r[1], r[3]
        else:                                       # 65-byte collection: leading 0 = no report id
            assert len(r) == 65 and r[0] == 0 and r[1] == 2
            target, prop = r[2], r[4]
        if target == 8 or not headset_alive:
            return None
        if prop == 0x12:
            body = pad([1, 1, 2, hb_status, 0], 64)
        else:
            v = seq.pop(0) if len(seq) > 1 else seq[0]
            body = pad([1, 1, 2, batt_status, v & 0xFF, v >> 8], 64)
        return (b"\0" + body) if lead_zero else body
    return resp


def test_v2_sequence_and_parse():
    out, h = run(v2_responder(), [V2])
    assert h.sent == [pad([0, 2, 8, 2, 0x13], 65), pad([0, 2, 8, 2, 0x12], 65),
                      pad([0, 2, 9, 2, 0x12], 65), pad([0, 2, 9, 2, 0x0F], 65)]
    d, = out
    assert d.id == "corsair:2a08" and d.battery == 82 and d.connected and not d.charging
    assert d.name == "Corsair Void Wireless V2"


def test_v2_64_byte_collection_writes_exactly_out_len():
    out, h = run(v2_responder(lead_zero=False, ids=True), [info(0x2A08, 0xFF00, in_len=64, out_len=64)])
    assert out[0].battery == 82
    assert all(len(s) == 64 and s[0] == 2 for s in h.sent) and len(h.sent) == 4


def test_v2_65_byte_collection_writes_exactly_out_len():
    out, h = run(v2_responder(), [V2])
    assert out[0].battery == 82 and all(len(s) == 65 and s[0] == 0 for s in h.sent)


def test_send_refuses_length_mismatch():
    class Sized(FakeHandle):
        out_len = 64
    h = Sized(lambda r: None)
    assert cs._send(h, pad([0, 2, 9, 2, 0x0F], 65), False, 100) is False and h.sent == []


def test_v2_reply_status_is_checked():
    out, _ = run(v2_responder(hb_status=5), [V2])                    # heartbeat refused: explicit off
    assert out[0].connected is False and out[0].battery is None
    out, _ = run(v2_responder(batt_status=5), [V2])                  # battery GET refused every time
    assert out[0].battery is None and out[0].connected is True


def test_v2_retries_invalid_values():
    out, h = run(v2_responder(tenths=(0, 5000, 640)), [V2])
    assert out[0].battery == 64 and h.sent[-1][4] == 0x0F and len(h.sent) == 6
    out, _ = run(v2_responder(tenths=(0,)), [V2])
    assert out[0].connected is True and out[0].battery is None


def test_v2_silent_headset_yields_nothing():
    out, h = run(v2_responder(headset_alive=False), [V2], budget=0.8)
    assert out == [] and len(h.sent) == 3


# ---------------------------------------------------------------------------------------------
# Whitelist, deadline, selection
# ---------------------------------------------------------------------------------------------

def test_bragi_whitelist():
    assert cs.build_bragi_request(9, 0x0F) == pad([2, 9, 2, 0x0F], 64)
    for args in [(9, 0x46), (9, 0x47), (9, 0x03), (9, 0x0D), (9, 0x0E), (7, 0x0F), (9, 0x0F, 1)]:
        with pytest.raises(ValueError):
            cs.build_bragi_request(*args)
    h = FakeHandle(lambda r: None)
    for frame, ids in [(pad([2, 9, 1, 0x46, 0, 0x64], 64), True),         # SET
                       (pad([2, 9, 2, 0x47], 64), True),
                       (pad([2, 9, 2, 0x0F, 1], 64), True),               # extra payload
                       (pad([2, 9, 2, 0x0F], 10), True),                  # too short
                       (pad([2, 9, 2, 0x0F], 65), False),                 # missing leading 0
                       (pad([0, 2, 9, 1, 0x03], 65), False)]:
        with pytest.raises(ValueError):
            cs._send(h, frame, ids, 100)
    with pytest.raises(ValueError):
        cs._send_void(h, pad([0xC8, 0x01], 64), 100)                      # lights command
    with pytest.raises(ValueError):
        cs._send_void(h, pad([0xCA, 0x02, 1], 64), 100)                   # sound command
    assert h.sent == []


@pytest.mark.parametrize("i", [VOID, XT, V2])
def test_deadline_and_cancel_before_write(i):
    out, h = run(lambda r: void_reply(), [i], budget=-1)
    assert out == [] and h.sent == []
    c = threading.Event()
    c.set()
    out, h = run(lambda r: void_reply(), [i], cancel=c)
    assert out == [] and h.sent == []
    h = FakeHandle(lambda r: None)
    assert cs._bragi_get(h, 9, 0x0F, time.monotonic() - 1, None, True) is None and h.sent == []


def test_cancel_mid_read_stops():
    c = threading.Event()

    def resp(r):
        c.set()
        return None
    out, h = run(resp, [XT], cancel=c, budget=5)
    assert len(h.sent) <= 1


def test_collection_selection():
    wrong = [info(0x0A14, 0xFF00, path="a"),                       # void wrong page
             info(0x0A14, 0xFFC5, usage=2, in_len=5, path="b"),
             info(0x0A64, 0xFF43, path="c"),                       # xt wrong page
             info(0x0A64, 0xFF42, in_len=8, path="d"),             # xt short report
             info(0x2A08, 0x000C, in_len=65, out_len=65, path="e"),
             info(0x2A08, 0xFF00, in_len=8, out_len=8, path="f"),
             info(0x0A64, 0xFF42, vid=0x046D, path="g"),           # other vendor
             info(0x9999, 0xFF42, path="h")]                       # unknown pid
    out, h = run(xt_responder(), wrong)
    assert out == [] and h.sent == []
    out, h = run(xt_responder(), wrong + [info(0x0A64, 0xFF42, path="z1"),
                                          info(0x0A64, 0xFF42, path="z2")])
    assert len(out) == 1 and len(h.sent) == 2


def test_product_name_sanitized_and_never_raises():
    out, _ = run(xt_responder(), [info(0x0A64, 0xFF42, product="CORSAIR VIRTUOSO XT")])
    assert out[0].name

    def boom(path, in_len=0):
        raise OSError("x")
    assert cs.read([XT, VOID, V2], time.monotonic() + 1, None, handle_factory=boom) == []
    assert cs.read(None, time.monotonic() + 1) == []


def test_vendor_ids():
    assert cs.VENDOR_IDS == (0x1B1C,)
