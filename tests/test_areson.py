"""Unit tests for the experimental Areson 25A7:FA07/FA08 mouse battery reader (fake handles only)."""
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import areson, wireless

REAL_FA07 = bytes.fromhex("09040000000208000000000000000000" "3e")
GET_POWER_HEX = "08040000000000000000000000000000" "49"
MI = "hid#vid_25a7&pid_fa07&mi_{mi:02x}#7&1&0&{col:04x}"


def reply(level10=8, charging=0, status=0, cmd=4, length=2, bad_sum=False, rid=9):
    b = bytearray(17)
    b[0], b[1], b[2], b[5] = rid, cmd, status, length
    b[6], b[7] = level10, charging
    b[16] = (0x55 - sum(b[:16])) & 0xFF
    if bad_sum:
        b[16] ^= 0x01
    return bytes(b)


def send_info(mi=1, feat_len=17, pid=0xFA07, col=1, product="2.4G Wireless Receiver"):
    return SimpleNamespace(vid=0x25A7, pid=pid, path=MI.format(mi=mi, col=col), usage_page=0xFF02,
                           usage=1, in_len=0, out_len=0, feat_len=feat_len, product=product)


def recv_info(mi=1, in_len=17, pid=0xFA07, col=2, product="2.4G Wireless Receiver"):
    return SimpleNamespace(vid=0x25A7, pid=pid, path=MI.format(mi=mi, col=col), usage_page=0xFF01,
                           usage=1, in_len=in_len, out_len=0, feat_len=0, product=product)


class Handle:
    def __init__(self, owner, path, feature_only):
        self.o, self.path, self.feature_only = owner, path, feature_only

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def set_feature(self, data):
        self.o.sent.append((self.path, bytes(data)))
        self.o.queue.extend(self.o.script)
        return True

    def write(self, data, timeout_ms=500):
        raise AssertionError("output reports must never be written")

    def read(self, timeout_ms=0):
        return self.o.queue.pop(0) if self.o.queue else None


class Factory:
    def __init__(self, script=()):
        self.script, self.queue, self.sent, self.opened = list(script), [], [], []

    def __call__(self, path, in_len=64, feature_only=False):
        self.opened.append((path, feature_only))
        return Handle(self, path, feature_only)


def run(infos, script, deadline_s=5.0, cancel=None):
    f = Factory(script)
    out = areson.read(infos, time.monotonic() + deadline_s, cancel, handle_factory=f)
    return out, f


@pytest.fixture(autouse=True)
def _clear_logged():
    areson._logged.clear()
    areson._merge_logged = False


def test_frame_and_checksum():
    assert areson.GET_POWER.hex() == GET_POWER_HEX
    assert areson.VENDOR_IDS == (0x25A7,)
    assert sum(REAL_FA07) & 0xFF == 0x55


def test_real_fa07_sample():
    out, f = run([send_info(), recv_info()], [REAL_FA07])
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.battery, d.charging, d.kind, d.link, d.source, d.connected) == \
        ("areson:fa07", 80, False, "mouse", "2.4ghz", "hid", True)
    assert d.name == "2.4 GHz mouse"            # generic receiver product -> generic name


def test_charging_and_custom_name():
    out, _ = run([send_info(product="Redragon M913"), recv_info(product="Redragon M913")],
                 [reply(5, charging=1)])
    assert out[0].battery == 50 and out[0].charging is True and out[0].name == "Redragon M913"


def test_length_one_never_charging():
    out, _ = run([send_info(), recv_info()], [reply(7, charging=1, length=1)])
    assert out[0].battery == 70 and out[0].charging is False


@pytest.mark.parametrize("bad", [reply(bad_sum=True), reply(status=1), reply(cmd=0x0A),
                                 reply(length=0), reply(11), REAL_FA07[:16]])
def test_invalid_replies_give_no_device(bad):
    out, _ = run([send_info(), recv_info()], [bad], deadline_s=0.3)
    assert out == []


def test_level_zero_without_charge_is_no_reading_but_charging_zero_is_kept():
    assert run([send_info(), recv_info()], [reply(0)], deadline_s=2)[0] == []
    out, _ = run([send_info(), recv_info()], [reply(0, charging=1)])
    assert out[0].battery == 0 and out[0].charging is True


def test_skips_unrelated_reports():
    other = bytes.fromhex("0a" + "00" * 16)
    out, _ = run([send_info(), recv_info()], [other, reply(cmd=0x0A), REAL_FA07])
    assert out[0].battery == 80


def test_only_exact_get_power_is_sent_padded_to_feature_length():
    out, f = run([send_info(feat_len=33), recv_info()], [REAL_FA07])
    assert len(f.sent) == 1
    path, data = f.sent[0]
    assert path == send_info().path and len(data) == 33
    assert data == bytes.fromhex(GET_POWER_HEX) + bytes(16)
    assert (send_info().path, True) in f.opened       # FF02 opened feature-only


def test_whitelist_refuses_everything_else():
    class H:
        def set_feature(self, d):
            raise AssertionError

    w = areson._FeatureOnly(H(), 17)
    for bad in (b"", bytes(17), areson.GET_POWER[:-1] + b"\x00", areson.GET_POWER + b"\x00",
                bytes.fromhex("0803" + "00" * 15)):
        with pytest.raises(ValueError):
            w.set_feature(bad)
    with pytest.raises(ValueError):
        w.write(areson.GET_POWER)
    with pytest.raises(ValueError):
        w.get_feature(0, 17)
    r = areson._Reader(H())
    with pytest.raises(ValueError):
        r.write(areson.GET_POWER)
    with pytest.raises(ValueError):
        r.set_feature(areson.GET_POWER)


def test_pairs_only_within_same_interface():
    out, f = run([send_info(mi=1), recv_info(mi=2)], [REAL_FA07], deadline_s=0.3)
    assert out == [] and f.opened == []
    out, f = run([send_info(mi=1), recv_info(mi=2), recv_info(mi=1, col=3)], [REAL_FA07])
    assert len(out) == 1
    assert (recv_info(mi=1, col=3).path, False) in f.opened
    assert (recv_info(mi=2).path, False) not in f.opened


def test_short_collections_and_other_devices_are_ignored():
    other = SimpleNamespace(vid=0x25A7, pid=0xFA09, path=MI.format(mi=1, col=9), usage_page=0xFF01,
                            usage=1, in_len=17, out_len=0, feat_len=0, product="")
    out, f = run([send_info(feat_len=8), recv_info(), other], [REAL_FA07], deadline_s=0.3)
    assert out == [] and f.opened == []
    out, f = run([send_info(), recv_info(in_len=8)], [REAL_FA07], deadline_s=0.3)
    assert out == [] and f.opened == []


def test_two_receivers_get_distinct_ids():
    a = [send_info(mi=1), recv_info(mi=1)]
    b = [SimpleNamespace(**{**vars(i), "path": i.path.replace("7&1&0", "9&2&0")}) for i in a]
    out, _ = run(a + b + a, [REAL_FA07])
    assert len(out) == 2
    assert out[0].id == "areson:fa07" and out[1].id.startswith("areson:fa07:")


def test_cancel_and_deadline_do_not_touch_device():
    ev = threading.Event()
    ev.set()
    out, f = run([send_info(), recv_info()], [REAL_FA07], cancel=ev)
    assert out == [] and f.opened == [] and f.sent == []
    out, f = run([send_info(), recv_info()], [REAL_FA07], deadline_s=-1)
    assert out == [] and f.sent == []


def test_never_raises():
    def boom(*a, **k):
        raise OSError("gone")
    assert areson.read([send_info(), recv_info()], time.monotonic() + 2, None, handle_factory=boom) == []
    assert areson.read(None, time.monotonic() + 2, None, handle_factory=boom) == []
    assert areson.read([object()], time.monotonic() + 2, None, handle_factory=boom) == []


def test_first_reply_logged_once_as_hex(caplog):
    infos = [send_info(), recv_info()]
    with caplog.at_level(logging.INFO, logger="taskbar_battery_widget.areson"):
        run(infos, [REAL_FA07])
        run(infos, [REAL_FA07])
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(msgs) == 1 and REAL_FA07.hex(" ").upper() in msgs[0]


def test_no_reply_logged_once(caplog):
    infos = [send_info(), recv_info()]
    with caplog.at_level(logging.INFO, logger="taskbar_battery_widget.areson"):
        run(infos, [], deadline_s=0.3)
        run(infos, [], deadline_s=0.3)
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(msgs) == 1 and "no GetPower reply" in msgs[0]


def test_hid_win_filters_pids():
    from taskbar_battery_widget import hid_win
    assert hid_win._USB_ONLY_PIDS[0x25A7] == (0xFA07, 0xFA08)
    assert 0x25A7 in wireless.EXTRA_VENDOR_IDS


CABLE = "hid#vid_25a7&pid_fa08&mi_{mi:02x}#a&1b2c3d4&0&{col:04x}"
# Real replies from issue #9: dongle FA07 "05 00" (50 %), cable FA08 "06 01" (60 %, charging).
REAL_DONGLE = bytes.fromhex("09 04 00 00 00 02 05 00 00 00 00 00 00 00 00 00 41")
REAL_CABLE = bytes.fromhex("09 04 00 00 00 02 06 01 00 00 00 00 00 00 00 00 3F")


def cable_infos(path=CABLE, product="2.4G Dual Mode Mouse"):
    return [SimpleNamespace(**{**vars(i), "path": path.format(mi=1, col=c), "pid": 0xFA08,
                               "product": product})
            for i, c in ((send_info(), 1), (recv_info(), 2))]


class PerPath(Factory):
    """Answers each receiver with its own reply (keyed by a substring of the path)."""

    def __init__(self, replies):
        super().__init__()
        self.replies = replies

    def __call__(self, path, in_len=64, feature_only=False):
        h = super().__call__(path, in_len, feature_only)
        if not feature_only:
            self.script = [r for k, r in self.replies.items() if k in path]
        return h


def run_per_path(infos, replies, deadline_s=5.0):
    f = PerPath(replies)
    return areson.read(infos, time.monotonic() + deadline_s, None, handle_factory=f), f


def test_real_replies_from_issue_9():
    assert sum(REAL_DONGLE) & 0xFF == 0x55 and sum(REAL_CABLE) & 0xFF == 0x55
    assert areson.parse_reply(REAL_DONGLE) == ("ok", 50, False)
    assert areson.parse_reply(REAL_CABLE) == ("ok", 60, True)


def test_dongle_and_cable_of_same_mouse_are_one_entry(caplog):
    infos = [send_info(), recv_info()] + cable_infos()
    with caplog.at_level(logging.INFO, logger="taskbar_battery_widget.areson"):
        out, _ = run_per_path(infos, {"pid_fa07": REAL_DONGLE, "pid_fa08": REAL_CABLE})
        run_per_path(infos, {"pid_fa07": REAL_DONGLE, "pid_fa08": REAL_CABLE})
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.name, d.battery, d.charging) == ("areson:fa07", "2.4 GHz mouse", 60, True)
    merged = [r for r in caplog.records if "same mouse" in r.getMessage()]
    assert len(merged) == 1


def test_cable_only_still_shows_the_mouse():
    out, _ = run_per_path(cable_infos(), {"pid_fa08": REAL_CABLE})
    assert [(d.id, d.name, d.battery, d.charging) for d in out] == \
        [("areson:fa08", "2.4G Dual Mode Mouse", 60, True)]


def test_two_dongles_and_a_cable_are_not_merged():
    other = [SimpleNamespace(**{**vars(i), "path": i.path.replace("7&1&0", "9&2&0")})
             for i in (send_info(), recv_info())]
    infos = [send_info(), recv_info()] + other + cable_infos()
    out, _ = run_per_path(infos, {"pid_fa07": REAL_DONGLE, "pid_fa08": REAL_CABLE})
    assert len(out) == 3
    assert sorted(d.id.split(":")[1] for d in out) == ["fa07", "fa07", "fa08"]
