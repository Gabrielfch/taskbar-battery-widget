"""One-time hint about `razer_dongle_wake` when a 0555 dongle is silent and the option is off."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import time

import pytest
from PySide6.QtWidgets import QApplication

from taskbar_battery_widget import razer_odessa as ro
from taskbar_battery_widget.config import Config, DEFAULTS
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController
from tests.test_razer_wake import WakeHandle, scan


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean():
    set_language("en")
    ro._woken.clear()
    ro.take_wake_hint()
    yield
    ro._woken.clear()
    ro.take_wake_hint()


def test_recorded_when_off_and_silent():
    assert scan(WakeHandle(), wake=False) == []
    assert ro.take_wake_hint() == {0x0555}


def test_not_recorded_when_answering():
    assert len(scan(WakeHandle(needs_wake=False), wake=False)) == 1
    assert ro.take_wake_hint() == set()


def test_not_recorded_when_wake_on():
    scan(WakeHandle(), wake=True)
    assert ro.take_wake_hint() == set()


def test_take_clears():
    scan(WakeHandle(), wake=False)
    assert ro.take_wake_hint()
    assert ro.take_wake_hint() == set()


def test_hint_sends_no_extra_packets():
    h = WakeHandle()
    scan(h, wake=False)
    assert len(h.sent) == 1


def test_config_flag_default_and_validation(tmp_path):
    assert DEFAULTS["razer_wake_hint_shown"] is False
    p = tmp_path / "c.json"
    p.write_text('{"razer_wake_hint_shown": "yes"}', encoding="utf-8")
    assert Config(p).get("razer_wake_hint_shown") is False


def _tray(tmp_path, **cfg_values):
    cfg = Config(tmp_path / "c.json")
    for k, v in cfg_values.items():
        cfg.set(k, v)
    tray = TrayController(cfg)
    shown = []
    tray.notify = lambda title, body, info=False: shown.append((title, body))
    return tray, cfg, shown


def test_notice_shown_once_and_persisted(qapp, tmp_path):
    tray, cfg, shown = _tray(tmp_path)
    tray.show_razer_wake_hint()
    tray.show_razer_wake_hint()
    assert len(shown) == 1 and "Razer" in shown[0][0] and "Synapse" in shown[0][1]
    assert cfg.get("razer_wake_hint_shown") is True
    assert Config(tmp_path / "c.json").get("razer_wake_hint_shown") is True


def test_notice_not_shown_when_flag_already_set(qapp, tmp_path):
    tray, _, shown = _tray(tmp_path, razer_wake_hint_shown=True)
    tray.show_razer_wake_hint()
    assert shown == []


def test_notice_not_shown_when_option_on(qapp, tmp_path):
    tray, cfg, shown = _tray(tmp_path, razer_dongle_wake=True)
    tray.show_razer_wake_hint()
    assert shown == [] and cfg.get("razer_wake_hint_shown") is False


def test_notice_not_shown_when_notifications_off(qapp, tmp_path):
    tray, cfg, shown = _tray(tmp_path, notifications_enabled=False)
    tray.show_razer_wake_hint()
    assert shown == [] and cfg.get("razer_wake_hint_shown") is False


def test_click_opens_sources(qapp, tmp_path):
    tray, _, _ = _tray(tmp_path)
    opened = []
    tray.show_sources_settings = lambda: opened.append(1)
    tray.show_razer_wake_hint()
    tray._on_message_clicked()
    tray._on_message_clicked()
    assert opened == [1]


def test_monitor_emits_hint_signal(qapp):
    from taskbar_battery_widget.monitor import DeviceMonitor
    m = DeviceMonitor(use_watchers=False)
    got = []
    m.razer_wake_hint.connect(lambda: got.append(1))
    m._wake_hint_ready.emit()
    assert got == [1]
