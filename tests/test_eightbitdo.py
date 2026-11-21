import sys
import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import eightbitdo as eb
from tests._fake_clock import install as _install_clock


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    eb._triggered.clear()
    return _install_clock(monkeypatch, eb, sys.modules[__name__])


class FakeHandle:
    def __init__(self, responder=None, initial=(), feature_responder=None, fail_write=False):
        self.responder = responder
        self.feature_responder = feature_responder
        self.fail_write = fail_write
        self.opened = 0
        self.sent = []
        self.features = []
        self.queue = list(initial)

    def __enter__(self):
        self.opened += 1
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def write(self, pkt, timeout_ms=500):
        self.sent.append(bytes(pkt))
        if self.fail_write:
            return False
        if self.responder:
            self.queue.extend(self.responder(bytes(pkt)))
        return True

    def get_feature(self, report_id, length):
        self.features.append((report_id, length))
        if self.feature_responder:
            self.queue.extend(self.feature_responder(report_id, length))
        return bytes(length)

    def read(self, timeout_ms=0):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(min(timeout_ms, 20) / 1000)
        return None


def vinfo(pid, page=0xFFA0, usage=1, in_len=64, out_len=64, path=r"\\?\hid#vid_2dc8&pid_6013#1",
          product="", vid=0x2DC8, feat_len=0):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, feat_len=feat_len, product=product)


def ident_reply(hw_pid=0x6013, fw=103):
    b = bytearray(64)
    b[0:2] = b"\x02\x22"
    b[2], b[3] = fw & 0xFF, fw >> 8
    b[6], b[7] = hw_pid & 0xFF, hw_pid >> 8
    return bytes(b)


def pad_reply(pct=75, fw=110, edition=3):
    b = bytearray(64)
    b[0:6] = b"\x02\x04\x04\x00\x21\x01"
    b[18] = pct
    b[19], b[20] = fw & 0xFF, fw >> 8
    b[23] = edition
    return bytes(b)


def state_report(pct=64, charging=False, rid=0x04, size=36):
    b = bytearray(size)
    if size > 0:
        b[0] = rid
    if size > 14:
        b[14] = (pct & 0x7F) | (0x80 if charging else 0)
    return bytes(b)


def zero_padded_legacy_report(rid=0x01, raw_len=9, padded_len=20):
    """Simulate Win32 ReadFile zero-padding a short legacy report up to InputReportByteLength."""
    raw = bytearray(raw_len)
    raw[0] = rid
    if raw_len >= 7:
        raw[2] = 0x08                       # neutral hat
        raw[3:7] = b"\x7f\x7f\x7f\x7f"      # neutral sticks
    return bytes(raw).ljust(padded_len, b"\x00")


def run(h, infos, budget=1.5, cancel=None, beta=False):
    return eb.read(infos, time.monotonic() + budget, cancel,
                   handle_factory=lambda p, in_len=64: h, beta=beta)


def test_vendor_ids():
    assert eb.VENDOR_IDS == (0x2DC8,)


def test_request_bytes():
    ident = eb.build_request(eb.CMD_IDENTIFY)
    assert len(ident) == 64 and ident[:5] == b"\x81\x05\x00\x21\x01" and ident[5:] == bytes(59)
    pad = eb.build_request(eb.CMD_PAD_STATUS)
    assert len(pad) == 64 and pad[:7] == b"\x81\x04\x21\x01\x00\x00\x06" and pad[7:] == bytes(57)


@pytest.mark.parametrize("cmd", ["", "write", "commit", "dfu", "81 05 00 51 00", 1, None])
def test_whitelist_refuses_unknown_commands(cmd):
    with pytest.raises(ValueError):
        eb.build_request(cmd)


def test_readonly_wrapper_blocks_arbitrary_writes_and_features():
    ro = eb._ReadOnly(FakeHandle())
    for forbidden in (
        b"\x81\x05\x00\x51\x00".ljust(64, b"\x00"),     # bootloader / DFU
        b"\x81\x05\xc1".ljust(64, b"\x00"),             # lift write protection
        b"\x81\x04\x01\x00".ljust(64, b"\x00"),         # config write
        b"\x81\x04\x06\x00\x23\x01".ljust(64, b"\x00"), # flash commit
    ):
        with pytest.raises(ValueError):
            ro.write(forbidden)
    with pytest.raises(ValueError):
        ro.get_feature(0x30, 64)
    assert not hasattr(ro, "set_feature")


def test_parse_identify_and_pad_status():
    assert eb.parse_identify(ident_reply(0x6013, fw=103)) == 0x6013
    for bad in (None, b"", b"\x02\x22" + bytes(10), b"\x02\x22" + bytes(62), bytes(64)):
        assert eb.parse_identify(bad) is None

    assert eb.parse_pad_status(pad_reply(1, fw=110)) == 1
    assert eb.parse_pad_status(pad_reply(100, fw=110)) == 100
    for bad in (
        None,
        b"",
        pad_reply(0, fw=110),           # 0 % rejected (never false 0 %)
        pad_reply(75, fw=0),            # zeroed controller firmware rejected
        pad_reply(0, fw=0),             # zero-padded header echo when pad is off
        pad_reply(101, fw=110),
        bytes(64),
        pad_reply(50, fw=110)[:30],
    ):
        assert eb.parse_pad_status(bad) is None


def test_parse_state_battery_rejects_zero_padded_legacy_and_non_enhanced_reports():
    assert eb.parse_state_battery(state_report(55, False, rid=0x04)) == (55, False)
    assert eb.parse_state_battery(state_report(55, True, rid=0x04)) == (55, True)
    assert eb.parse_state_battery(state_report(55, False, rid=0x01)) == (55, False)
    assert eb.parse_state_battery(state_report(100, True)) == (100, False)  # 100 % = charged
    for bad in (
        None,
        b"",
        zero_padded_legacy_report(rid=0x01, raw_len=9, padded_len=20),      # 9-byte legacy 0x01 padded to 20
        zero_padded_legacy_report(rid=0x01, raw_len=12, padded_len=36),     # 12-byte U2 v1.02 padded to 36
        state_report(0, False, rid=0x04),                                   # 0 % on battery -> rejected
        state_report(0, True, rid=0x04),                                    # 0 % with high bit -> rejected
        state_report(50, rid=0x03),                                         # 0x03 = firmware without enhanced mode
        state_report(50, rid=0x02),
        state_report(105, False),
        state_report(50, size=14),
    ):
        assert eb.parse_state_battery(bad) is None


# ------------------------------------------------------------------ Ultimate 2 Wireless (2.4 GHz)
def test_u2_dongle_pad_online():
    h = FakeHandle(lambda p: [pad_reply(84, fw=110)])
    [d] = run(h, [vinfo(0x6013, 0xFFA0)])
    assert h.sent == [eb.build_request(eb.CMD_PAD_STATUS)]
    assert (d.id, d.name, d.battery, d.connected, d.charging, d.link) == (
        "eightbitdo:6013", "8BitDo Ultimate 2 Wireless", 84, True, False, "2.4ghz")


def test_u2_dongle_pad_powered_off_silent_or_zero_padded_reply():
    for pad_ans in ([], [pad_reply(0, fw=0)]):
        def responder(pkt, ans=pad_ans):
            return [ident_reply(0x6013)] if pkt == eb.build_request(eb.CMD_IDENTIFY) else list(ans)

        h = FakeHandle(responder)
        [d] = run(h, [vinfo(0x6013, 0xFFA0, product="8BitDo IDLE")])
        assert (d.id, d.name, d.battery, d.connected, d.link) == (
            "eightbitdo:6013", "8BitDo Ultimate 2 Wireless", None, False, "2.4ghz")


# ------------------------------------------------------------------ USB D-Input / HIDAPI
def test_dinput_ultimate2_passive_and_zero_padded_v102_ignored():
    short = FakeHandle(initial=[state_report(90, size=15)])
    assert run(short, [vinfo(0x6012, 1, 5, in_len=36, out_len=0)]) == []

    # Win32 zero-padding a 12-byte v1.02 legacy report up to InputReportByteLength=36 must NOT read 0 %
    padded_v102 = FakeHandle(initial=[zero_padded_legacy_report(rid=0x01, raw_len=12, padded_len=36)])
    assert run(padded_v102, [vinfo(0x6012, 1, 5, in_len=36, out_len=0)]) == []

    ok = FakeHandle(initial=[state_report(90, True, size=34)])
    [d] = run(ok, [vinfo(0x6012, 1, 5, in_len=36, out_len=0)], beta=False)
    assert (d.id, d.battery, d.charging, d.link) == ("eightbitdo:6012", 90, True, "usb")


def test_dinput_ultimate3_passive_works_with_beta_off():
    ok = FakeHandle(initial=[state_report(73, False, size=20)])
    [d] = run(ok, [vinfo(0x202F, 1, 5, in_len=20, out_len=0,
                         product="Controller (8BitDo Ultimate 3)")], beta=False)
    assert (d.id, d.name, d.battery, d.charging, d.link) == (
        "eightbitdo:202f", "8BitDo Ultimate 3", 73, False, "usb")


@pytest.mark.parametrize("pid", sorted(eb.BETA_PIDS))
def test_dinput_feature_pids_skipped_when_beta_off(pid):
    h = FakeHandle(initial=[state_report(50, size=20)])
    assert run(h, [vinfo(pid, 1, 5, in_len=20, out_len=0, feat_len=64)], beta=False) == []
    assert h.opened == 0 and h.features == []


def test_dinput_pro2_zero_padded_legacy_0x01_triggers_feature_0x06_when_beta_on():
    # A real 9-byte legacy 0x01 report zero-padded to 20 bytes by Windows must NOT return 0 %;
    # with beta=True it triggers feature 0x06 and reads the enhanced 0x04 report.
    legacy_01 = zero_padded_legacy_report(rid=0x01, raw_len=9, padded_len=20)
    h = FakeHandle(initial=[legacy_01],
                   feature_responder=lambda rid, n: [state_report(42, False, rid=0x04, size=20)])
    [d] = run(h, [vinfo(0x6003, 1, 5, in_len=20, out_len=0, feat_len=64)], beta=True)
    assert h.features == [(0x06, 64)]
    assert (d.id, d.name, d.battery, d.charging, d.link) == (
        "eightbitdo:6003", "8BitDo Pro 2", 42, False, "usb")


def test_dinput_pro2_still_legacy_after_feature_0x06_yields_nothing_not_zero():
    legacy_01 = zero_padded_legacy_report(rid=0x01, raw_len=9, padded_len=20)
    h = FakeHandle(initial=[legacy_01], feature_responder=lambda rid, n: [legacy_01])
    assert run(h, [vinfo(0x6003, 1, 5, in_len=20, out_len=0, feat_len=64)], beta=True) == []
    assert h.features == [(0x06, 64)]


def test_two_identical_controllers_get_distinct_ids():
    handles = {
        r"\\?\hid#vid_2dc8&pid_6012#a": FakeHandle(initial=[state_report(70, size=36)]),
        r"\\?\hid#vid_2dc8&pid_6012#b": FakeHandle(initial=[state_report(80, size=36)]),
    }
    infos = [vinfo(0x6012, 1, 5, in_len=36, out_len=0, path=p) for p in sorted(handles)]
    out = eb.read(infos, time.monotonic() + 2, None, handle_factory=lambda p, in_len=64: handles[p])
    assert len(out) == 2
    assert out[0].id == "eightbitdo:6012" and out[1].id.startswith("eightbitdo:6012:")


def test_dinput_skips_bluetooth_paths():
    bt_path = r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&00022dc8_pid&6006#1"
    h = FakeHandle(initial=[state_report(80, size=20)])
    assert run(h, [vinfo(0x6006, 1, 5, in_len=20, out_len=0, path=bt_path)], beta=True) == []
    assert h.opened == 0


# ------------------------------------------------------------------ contract: selection, budgets, safety
@pytest.mark.parametrize("kw", [
    dict(vid=0x045E, pid=0x6013, page=0xFFA0, usage=1, in_len=64, out_len=64),   # wrong VID
    dict(vid=0x2DC8, pid=0x9999, page=0xFFA0, usage=1, in_len=64, out_len=64),   # unknown PID
    dict(vid=0x2DC8, pid=0x310A, page=0xFF7A, usage=1, in_len=64, out_len=64),   # Ultimate 2C active (no %)
    dict(vid=0x2DC8, pid=0x301B, page=0x0001, usage=5, in_len=20, out_len=0),    # Ultimate 2C D-Input (no %)
    dict(vid=0x2DC8, pid=0x301C, page=0xFFA0, usage=1, in_len=64, out_len=64),   # Ultimate 2C idle dongle
    dict(vid=0x2DC8, pid=0x310B, page=0xFF7A, usage=1, in_len=64, out_len=64),   # Ultimate 2 wired XInput (no %)
    dict(vid=0x2DC8, pid=0x6013, page=0xFFA0, usage=2, in_len=64, out_len=64),   # wrong usage
    dict(vid=0x2DC8, pid=0x6013, page=0xFFA0, usage=1, in_len=32, out_len=64),   # wrong in_len
    dict(vid=0x2DC8, pid=0x6013, page=0xFF7A, usage=1, in_len=64, out_len=64),   # U2 dongle on wrong page
])
def test_collection_selection_skips_non_matching(kw):
    h = FakeHandle(lambda p: [pad_reply(80)])
    assert run(h, [vinfo(**kw)], beta=True) == [] and h.opened == 0 and h.sent == []


def test_deadline_and_cancel_respected_before_write():
    h = FakeHandle(lambda p: [pad_reply(80)])
    assert run(h, [vinfo(0x6013, 0xFFA0)], budget=-0.1) == [] and h.sent == []
    ev = threading.Event()
    ev.set()
    assert run(h, [vinfo(0x6013, 0xFFA0)], cancel=ev) == [] and h.sent == []


def test_silent_handle_and_write_failure_yield_nothing():
    assert run(FakeHandle(lambda p: []), [vinfo(0x6013, 0xFFA0)], budget=0.5) == []
    assert run(FakeHandle(None, fail_write=True), [vinfo(0x6013, 0xFFA0)]) == []


def test_never_raises():
    def boom(path, in_len=64):
        raise OSError("device gone")
    assert eb.read([vinfo(0x6013, 0xFFA0)], time.monotonic() + 1, None, handle_factory=boom) == []
    assert eb.read(None, time.monotonic() + 1, None, handle_factory=boom) == []
