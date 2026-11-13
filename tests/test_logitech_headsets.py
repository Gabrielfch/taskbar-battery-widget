import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import hidpp
from taskbar_battery_widget import logitech_headsets as lh


class FakeHandle:
    """responder(pkt) -> list of input reports queued after each write."""

    def __init__(self, responder=None, feature=None):
        self.responder = responder or (lambda pkt: [])
        self.queue, self.sent, self.closed = [], [], False
        self.feature_sent, self.feature_reply = [], feature

    def write(self, data, timeout_ms=1000):
        self.sent.append(bytes(data))
        self.queue.extend(self.responder(bytes(data)))
        return True

    def read(self, timeout_ms=0):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(min(timeout_ms, 5) / 1000)
        return None

    def drain(self):
        self.queue.clear()

    def set_feature(self, data):
        self.feature_sent.append(bytes(data))
        return True

    def get_feature(self, report_id, length):
        return self.feature_reply

    def close(self):
        self.closed = True


def info(pid, page, usage, n=20, out=None, feat=0, path=None, product=""):
    return SimpleNamespace(vid=0x046D, pid=pid, path=path or f"p{pid:04x}{page:04x}", usage_page=page,
                           usage=usage, in_len=n, out_len=n if out is None else out, feat_len=feat,
                           product=product)


def run(infos, handle, budget=2.0, cancel=None):
    return lh.read(infos, time.monotonic() + budget, cancel, handle_factory=lambda path, n=0: handle,
                   beta=True)


# ---- legacy HID++ ------------------------------------------------------------------------------
LEGACY = [  # pid, sw id of the model, battery feature index the fake headset reports
    (0x0A66, 0x01, 0x07), (0x0AC4, 0x0D, 0x05), (0x0A5C, 0x0A, 0x08), (0x0AB5, 0x0A, 0x08),
    (0x0AA7, 0x0D, 0x06), (0x0ABA, 0x0D, 0x06),
]


def legacy_reply(feat, sw, mv, state=0):
    return bytes([0x11, 0xFF, feat, sw, mv >> 8, mv & 0xFF, state]).ljust(20, b"\0")


def root_reply(sw, idx, fid_ok=True):
    return bytes([0x11, 0xFF, 0x00, sw, idx if fid_ok else 0, 0, 0]).ljust(20, b"\0")


def legacy_sim(idx, mv=4000, state=0, features=(0x1F20, 0x1001), battery_error=False):
    """Fake headset: Root.getFeature resolves `idx` for the supported features; then a voltage getter."""
    def f(pkt):
        assert pkt[0] == 0x11 and pkt[1] == 0xFF and len(pkt) == 20
        sw = pkt[3] & 0x0F
        assert pkt[3] >> 4 == 0
        if pkt[2] == 0x00:
            fid = (pkt[4] << 8) | pkt[5]
            return [root_reply(sw, idx, fid in features)]
        assert pkt[2] == idx
        if battery_error:
            return [bytes([0x11, 0xFF, 0xFF, idx, pkt[3], 0x05, 0]).ljust(20, b"\0")]
        return [legacy_reply(idx, sw, mv, state)]
    return f


@pytest.mark.parametrize("pid,sw,idx", LEGACY)
def test_legacy_resolves_feature_index_then_reads(pid, sw, idx):
    h = FakeHandle(legacy_sim(idx))
    devs = run([info(pid, 0xFF43, 0x0202)], h)
    assert h.sent == [bytes([0x11, 0xFF, 0x00, sw, 0x1F, 0x20]).ljust(20, b"\0"),
                      bytes([0x11, 0xFF, idx, sw]).ljust(20, b"\0")]
    assert len(devs) == 1 and devs[0].id == f"logi:{pid:04x}" and devs[0].kind == "headset"
    assert devs[0].connected and devs[0].charging is False and devs[0].link == "2.4ghz"
    assert devs[0].source == "hid" and 60 <= devs[0].battery <= 95 and h.closed


def test_legacy_index_is_not_fixed_per_pid():
    for idx in (0x04, 0x0B):
        h = FakeHandle(legacy_sim(idx))
        assert run([info(0x0A66, 0xFF43, 0x0202)], h)[0].battery is not None
        assert h.sent[1][2] == idx


def test_legacy_falls_back_to_second_voltage_feature():
    h = FakeHandle(legacy_sim(0x06, features=(0x1001,)))
    assert run([info(0x0A66, 0xFF43, 0x0202)], h)[0].battery is not None
    assert [bytes(p[4:6]).hex() for p in h.sent[:2]] == ["1f20", "1001"]


def test_legacy_aborts_when_no_expected_feature():
    h = FakeHandle(legacy_sim(0x06, features=()))
    assert run([info(0x0A66, 0xFF43, 0x0202)], h) == []
    assert len(h.sent) == 2                                     # both lookups, no battery request


def test_legacy_charging_state_and_voltage_monotone():
    h = FakeHandle(legacy_sim(0x07, 4100, 3))
    assert run([info(0x0A66, 0xFF43, 0x0202)], h)[0].charging is True
    for curve in (lh.CURVE_G533, lh.CURVE_G535, lh.CURVE_G633, lh.CURVE_GPRO):
        vals = [lh._percent(mv, curve) for mv in range(3000, 4400, 10)]
        assert vals == sorted(vals) and vals[0] == 0 and vals[-1] == 100


@pytest.mark.parametrize("reply", [
    b"", b"\x11\xff\x07", legacy_reply(0x09, 0x01, 4000), legacy_reply(0x07, 0x01, 100),
    legacy_reply(0x07, 0x01, 9000), b"\x20" + bytes(19),
])
def test_legacy_short_unrelated_or_invalid_reply_yields_nothing(reply):
    sim = legacy_sim(0x07)

    def f(pkt):
        return sim(pkt) if pkt[2] == 0x00 else [reply]
    devs = run([info(0x0A66, 0xFF43, 0x0202)], FakeHandle(f), budget=0.3)
    assert devs == []                                           # silence is not "off"


def test_legacy_silent_lookup_yields_nothing():
    assert run([info(0x0A66, 0xFF43, 0x0202)], FakeHandle(), budget=0.3) == []


def test_legacy_error_reply_means_device_off():
    devs = run([info(0x0A66, 0xFF43, 0x0202)], FakeHandle(legacy_sim(0x07, battery_error=True)))
    assert devs[0].connected is False and devs[0].battery is None
    err = bytes([0x11, 0xFF, 0xFF, 0x07, 0x01, 0x05, 0]).ljust(20, b"\0")
    assert lh.parse_legacy_reply(err, 0x07, 1) == ("off", None)
    assert lh.parse_legacy_reply(err, 0x08, 1) is None          # error for another request


def test_legacy_requests_go_through_hidpp_whitelist():
    assert lh.build_legacy_lookup(0x1F20, 1)[:6] == bytes([0x11, 0xFF, 0, 0x01, 0x1F, 0x20])
    assert lh.build_legacy_request(7, 0x1F20, 1) == bytes([0x11, 0xFF, 7, 0x01]).ljust(20, b"\0")
    for bad in [lambda: lh.build_legacy_lookup(0x1004, 1), lambda: lh.build_legacy_request(7, 0x1004, 1),
                lambda: lh.build_legacy_request(0, 0x1F20, 1), lambda: lh.build_legacy_request(0x80, 0x1F20, 1),
                lambda: lh.build_legacy_lookup(0x1F20, 0)]:
        with pytest.raises(ValueError):
            bad()


def test_legacy_collection_selection():
    h = FakeHandle(legacy_sim(0x07))
    wrong = [info(0x0A66, 0xFF00, 0x0001), info(0x0A66, 0xFF43, 0x0202, out=0),
             info(0x0A66, 0xFF43, 0x0202, n=64, out=64), info(0x1234, 0xFF43, 0x0202)]
    assert run(wrong, h) == [] and h.sent == []
    good = info(0x0A66, 0xFF43, 0x0202)
    assert len(run([wrong[0], good, info(0x0A66, 0xFF43, 0x0202, path="other")], h)) == 1   # one per pid


def test_g535_accepts_consumer_collection_alias():
    h = FakeHandle(legacy_sim(0x05, 3900))
    assert len(run([info(0x0AC4, 0x000C, 0x0001)], h)) == 1


def test_g930_reader_removed():
    assert 0x0A1F not in lh._BY_PID
    assert not hasattr(lh, "build_g930_request")


def test_deadline_and_cancel_before_write():
    h = FakeHandle(legacy_sim(0x07))
    assert lh.read([info(0x0A66, 0xFF43, 0x0202)], time.monotonic() - 1, None,
                   handle_factory=lambda *a: h) == []
    ev = threading.Event()
    ev.set()
    assert run([info(0x0A66, 0xFF43, 0x0202)], h, cancel=ev) == []
    assert h.sent == []
    # expired inside _read_one: no write either
    assert lh._exchange(h, b"x", time.monotonic() - 1, None, lambda b: b) is None and h.sent == []


def test_open_failure_never_raises():
    def boom(path, n=0):
        raise OSError("nope")
    assert lh.read([info(0x0A66, 0xFF43, 0x0202)], time.monotonic() + 1, None, handle_factory=boom) == []


def test_exchange_last_read_is_capped_to_remaining_time():
    seen = []

    class H(FakeHandle):
        def read(self, timeout_ms=0):
            seen.append(timeout_ms)
            time.sleep(timeout_ms / 1000)
            return None
    t = time.monotonic()
    assert lh._exchange(H(), b"x", t + 0.3, None, lambda b: b) is None
    assert max(seen) <= 250 and time.monotonic() - t < 0.45


# ---- Astro A50 Gen 5 ---------------------------------------------------------------------------
def astro_reply(pct, dock=0, cmd=0x06):
    return bytes([0x02, 0x0C, 0x06, 0x00, cmd, 0x00, pct, pct, dock]).ljust(64, b"\0")


def test_astro_request_reply_and_unrelated_push():
    push = astro_reply(33, cmd=0x08)
    h = FakeHandle(lambda p: [push, astro_reply(72, dock=1)])
    devs = run([info(0x0B1C, 0xFF32, 0x0074, n=64)], h)
    assert h.sent == [bytes([0x02, 0x0C, 0x03, 0x00, 0x06, 0x0C]).ljust(64, b"\0")]
    assert devs[0].battery == 72 and devs[0].charging and devs[0].kind == "headset"


@pytest.mark.parametrize("buf", [None, b"\x02\x0c", astro_reply(150), astro_reply(50, cmd=0x09),
                                 bytes([0x02, 0x0C, 0x03, 0, 0x06, 0x0C, 50, 0, 0])])
def test_astro_bad_replies(buf):
    assert lh.parse_astro_reply(buf) is None


def test_astro_no_answer_yields_nothing_and_whitelist():
    assert run([info(0x0B1C, 0xFF32, 0x0074, n=64)], FakeHandle(), budget=0.3) == []
    assert lh.ALLOWED_ASTRO_REQUESTS == {lh.build_astro_request()}
    assert run([info(0x0B1C, 0xFF32, 0x0074, n=32)], FakeHandle()) == []


# ---- Centurion ---------------------------------------------------------------------------------
class CenturionSim:
    BRIDGE, BATT = 0x05, 0x0A

    def __init__(self, prefix, online=True, battery=(77, 0, 1), reject=False, silent_after_ack=False):
        self.prefix, self.online, self.battery = prefix, online, battery
        self.reject, self.silent = reject, silent_after_ack

    def frame(self, payload):
        n = len(self.prefix)
        return (self.prefix + bytes([len(payload) + 1, 0]) + bytes(payload)).ljust(64, b"\0")

    def __call__(self, pkt):
        n = len(self.prefix)
        assert pkt[:n] == self.prefix and len(pkt) == 64
        pl = pkt[n + 2:n + 1 + pkt[n]]
        if pl == bytes([0, 1, 0, 3]):
            return [self.frame(bytes([0, 1, self.BRIDGE, 0, 1]))]
        if pl == bytes([self.BRIDGE, 1]):                       # status probe
            return [self.frame(bytes([self.BRIDGE, 1, 3, 0xE8, 0, 1 if self.online else 0, 1]))]
        assert pl[0] == self.BRIDGE and pl[1] == 0x11
        sub, fnsw = pl[5], pl[6]
        if self.reject:
            return [self.frame(bytes([0xFF, self.BRIDGE, 0x11, 5]))]
        if not self.online:
            return [self.frame(bytes([self.BRIDGE, 0x00, 0, 0]))]
        ack = self.frame(bytes([self.BRIDGE, 0x11, 0, 0]))
        if self.silent:
            return [ack]
        if sub == 0:
            body = bytes([self.BATT, 0, 1])
        else:
            assert sub == self.BATT and pl[2:5] == bytes([0, 3, 0]) and fnsw == 0x01
            body = bytes(self.battery)
        resp = self.frame(bytes([self.BRIDGE, 0x10, 0, 3 + len(body), 0, sub, fnsw]) + body)
        return [ack, resp]


@pytest.mark.parametrize("pid,prefix", [(0x0AF7, b"\x51"), (0x0B45, b"\x50\x23"), (0x0B18, b"\x50\x23")])
def test_centurion_reads_battery(pid, prefix):
    sim = CenturionSim(prefix)
    h = FakeHandle(sim)
    devs = run([info(pid, 0xFFA0, 0x0001, n=64)], h)
    assert len(h.sent) == 4                     # bridge, bridge status, battery lookup, getter
    assert devs[0].battery == 77 and devs[0].charging is True and devs[0].connected
    assert devs[0].id == f"logi:{pid:04x}" and h.closed


def test_centurion_headset_off_by_connection_event_and_status_probe():
    for kw in ({"online": False}, {"reject": True, "online": False}, {"silent_after_ack": True,
                                                                       "online": False}):
        sim = CenturionSim(b"\x51", **kw)
        if kw.get("silent_after_ack"):
            sim.online = True                       # acked, never answers; status says "connected"
            devs = run([info(0x0AF7, 0xFFA0, 0x0001, n=64)], FakeHandle(sim), budget=1.0)
            assert devs == []                       # unclear failure: do not guess
            sim.online = False
        devs = run([info(0x0AF7, 0xFFA0, 0x0001, n=64)], FakeHandle(sim), budget=1.0)
        assert len(devs) == 1 and devs[0].connected is False and devs[0].battery is None


def test_centurion_garbage_and_silence_are_skipped():
    assert run([info(0x0AF7, 0xFFA0, 0x0001, n=64)], FakeHandle(lambda p: [b"\x00" * 64]), budget=0.4) == []
    assert run([info(0x0AF7, 0xFFA0, 0x0001, n=64)], FakeHandle(), budget=0.4) == []


def test_centurion_whitelist_and_framing():
    pre = b"\x51"
    f = lh.centurion_bridge_lookup(pre)
    assert f == bytes([0x51, 5, 0, 0, 1, 0, 3]).ljust(64, b"\0")
    f = lh.centurion_battery_get(pre, 5, 10)
    assert f[:2] == b"\x51\x08" and lh.extract_centurion_payload(f, pre) == bytes(
        [5, 0x11, 0, 3, 0, 10, 1])
    for bad in [bytes([0, 1, 0, 4]), bytes([5, 0x11, 0, 3, 0, 10, 0x11]), bytes([5, 0x10, 0, 3, 0, 10, 1]),
                bytes([0x80, 1]), bytes([5, 0x21]), b"", bytes([5, 0x11, 0, 5, 0, 0, 1, 1, 5])]:
        with pytest.raises(ValueError):
            lh.build_centurion_frame(pre, bad)
    assert lh.extract_centurion_payload(b"\x50\x23\x03\x00\x01\x02", b"\x51") is None
    assert lh.extract_centurion_payload(b"\x51\x01\x00", b"\x51") is None
    assert lh.parse_centurion_battery(b"") is None and lh.parse_centurion_battery(b"\x65") is None


def test_centurion_requires_64_byte_vendor_collection():
    h = FakeHandle(CenturionSim(b"\x51"))
    assert run([info(0x0AF7, 0xFFA0, 0x0001, n=20), info(0x0AF7, 0xFF00, 0x0001, n=64)], h) == []
    assert h.sent == []


def test_model_table_has_no_pid_overlap():
    seen = {}
    for m in lh.MODELS:
        for pid in m.pids:
            assert pid not in seen
            seen[pid] = m
    assert lh.VENDOR_IDS == (0x046D,)


def test_beta_centurion_skipped_without_beta_and_stable_always_read():
    assert {m.pids[0] for m in lh.MODELS if m.beta} == {0x0AF7, 0x0B45, 0x0B18}
    opened = []

    def rec(path, n=0):
        opened.append(path)
        raise OSError("x")
    cent = info(0x0AF7, 0xFFA0, 1, n=64)
    assert lh.read([cent], time.monotonic() + 1, None, handle_factory=rec) == []
    assert opened == []
    lh.read([cent], time.monotonic() + 1, None, handle_factory=rec, beta=True)
    assert len(opened) == 1
    lh.read([info(0x0A66, 0xFF43, 0x0202)], time.monotonic() + 1, None, handle_factory=rec)
    assert len(opened) == 2                         # legacy headset: stable
