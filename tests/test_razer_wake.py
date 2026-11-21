"""Opt-in Razer dongle activation (`razer_dongle_wake`)."""
import time

import pytest

from taskbar_battery_widget import razer_odessa as ro
from taskbar_battery_widget import wireless
from taskbar_battery_widget.config import Config, DEFAULTS
from taskbar_battery_widget.settings_model import build_settings_model
from tests.test_razer_odessa import (FORBIDDEN_REMOTE_SET, PA_REQ_BAT, PA_REQ_CHG, PA_REQ_CONN,
                                     FakePaHandle, pa_info, pa_reply)

WAKE = FORBIDDEN_REMOTE_SET


class WakeHandle(FakePaHandle):
    """Silent until it receives the wake frame (when `needs_wake`)."""

    def __init__(self, needs_wake=True):
        super().__init__({0x20: [pa_reply(0x20, 1)] * 9, 0x21: [pa_reply(0x21, 70)] * 9,
                          0x2A: [pa_reply(0x2A, 0)] * 9})
        self.armed = not needs_wake

    def write(self, pkt, timeout_ms=1000):
        if bytes(pkt) == WAKE:
            self.sent.append(bytes(pkt))
            self.armed = True
            return True
        return super().write(pkt, timeout_ms)

    def wakes(self):
        return self.sent.count(WAKE)


@pytest.fixture(autouse=True)
def _clean():
    ro._woken.clear()
    yield
    ro._woken.clear()


def scan(h, infos=None, wake=True):
    return ro.read_odessa([pa_info()] if infos is None else infos, time.monotonic() + 3, None,
                          handle_factory=lambda path, in_len=64: h, wake=wake)


def test_off_never_wakes_even_if_silent():
    h = WakeHandle()
    assert scan(h, wake=False) == []
    assert h.sent == [PA_REQ_CONN]


def test_default_is_off():
    h = WakeHandle()
    assert ro.read_odessa([pa_info()], time.monotonic() + 3, None,
                          handle_factory=lambda path, in_len=64: h) == []
    assert h.wakes() == 0


def test_on_answering_dongle_gets_no_wake():
    h = WakeHandle(needs_wake=False)
    out = scan(h)
    assert len(out) == 1 and out[0].battery == 70
    assert h.wakes() == 0


def test_on_silent_then_answering_one_wake_then_gets():
    h = WakeHandle()
    out = scan(h)
    assert len(out) == 1 and out[0].battery == 70
    assert h.sent == [PA_REQ_CONN] + [WAKE] * ro.WAKE_FRAMES + [PA_REQ_CONN, PA_REQ_BAT, PA_REQ_CHG]


def test_second_scan_same_path_does_not_rewake():
    h = WakeHandle()
    scan(h)
    h.armed = False                       # dongle went silent again
    assert scan(h) == []
    assert h.wakes() == ro.WAKE_FRAMES


def test_path_disappears_and_returns_wakes_again():
    h = WakeHandle()
    scan(h)
    scan(h, infos=[])                     # unplugged: path forgotten
    h2 = WakeHandle()
    assert len(scan(h2)) == 1
    assert h2.wakes() == ro.WAKE_FRAMES


def test_other_path_present_keeps_state_of_first():
    h = WakeHandle()
    scan(h)
    h.armed = False
    assert scan(h, infos=[pa_info("p555"), pa_info("other", usage=0x0002)]) == []
    assert h.wakes() == ro.WAKE_FRAMES


def test_failed_first_write_releases_the_claim_for_a_later_scan():
    class FailingOnce(WakeHandle):
        fails = 1

        def write(self, pkt, timeout_ms=1000):
            if bytes(pkt) == WAKE and self.fails:
                self.fails -= 1
                return False                  # nothing went out
            return super().write(pkt, timeout_ms)

    h = FailingOnce()
    assert scan(h) == []
    assert h.wakes() == 0
    assert len(scan(h)) == 1                  # the next scan may try again
    assert h.wakes() == ro.WAKE_FRAMES


def test_wake_never_reaches_other_dongles_or_collections():
    h = WakeHandle()
    assert scan(h, infos=[pa_info("x", usage=0x0002)]) == []        # non-PA collection of the 0555
    assert h.wakes() == 0


def test_dropped_battery_get_never_wakes():
    h = FakePaHandle({0x20: [pa_reply(0x20, 1)], 0x21: [None, None], 0x2A: [None]})
    assert scan(h) == []
    assert WAKE not in h.sent


def test_wake_frame_rejected_when_flag_off_and_not_in_get_whitelist():
    h = FakePaHandle({})
    with pytest.raises(ValueError):
        ro._send_wake(h, time.monotonic() + 2, None, False)
    assert h.sent == []
    assert WAKE not in ro.ALLOWED_PA_FRAMES
    with pytest.raises(ValueError):
        ro._write_pa(h, WAKE, time.monotonic() + 2, None)
    assert ro._ALLOWED_WAKE_FRAME == WAKE
    with pytest.raises(ValueError):
        ro.build_pa_request(0xE1)


def test_wake_respects_deadline():
    h = WakeHandle()
    assert ro.read_odessa([pa_info()], time.monotonic() - 1, None,
                          handle_factory=lambda path, in_len=64: h, wake=True) == []
    assert h.sent == []


def test_config_default_and_validation(tmp_path):
    assert DEFAULTS["razer_dongle_wake"] is False
    p = tmp_path / "c.json"
    p.write_text('{"razer_dongle_wake": "yes"}', encoding="utf-8")
    c = Config(p)
    assert c.get("razer_dongle_wake") is False
    with pytest.raises(ValueError):
        c.set("razer_dongle_wake", 1)
    c.set("razer_dongle_wake", True)
    assert Config(p).get("razer_dongle_wake") is True


def test_settings_toggle_persists(tmp_path):
    from types import SimpleNamespace
    c = Config(tmp_path / "c.json")
    cb = SimpleNamespace(set_bool=lambda k, v: c.set(k, v))
    model = build_settings_model(c, cb)
    page = model.find("sources")
    t = next(n for n in page.children if n.id == "razer_dongle_wake")
    assert t.get() is False and t.tip()
    t.set(True)
    assert t.get() is True and Config(tmp_path / "c.json").get("razer_dongle_wake") is True


def test_wireless_flag_setter():
    wireless.set_razer_dongle_wake(True)
    try:
        assert wireless._razer_wake is True
    finally:
        wireless.set_razer_dongle_wake(False)
    assert wireless._razer_wake is False
