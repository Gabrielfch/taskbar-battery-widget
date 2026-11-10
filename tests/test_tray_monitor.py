import time

import pytest

from taskbar_battery_widget import monitor as mon, scanner, startup, tray as tray_mod
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device


def pump(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


# ---------------------------------------------------------------- config
def test_config_set_validates(tmp_path):
    c = Config(tmp_path / "c.json")
    with pytest.raises(ValueError):
        c.set("nope", 1)
    with pytest.raises(ValueError):
        c.set("low_threshold", "20")
    with pytest.raises(ValueError):
        c.set("notifications_enabled", 1)
    with pytest.raises(ValueError):
        c.set("notification_cooldown_min", 0)
    c.set("notification_cooldown_min", 1)
    assert c.get("notification_cooldown_min") == 1


# ---------------------------------------------------------------- i18n in widget
def test_pill_tooltip_translated_kind(qapp):
    from taskbar_battery_widget import widget as w

    class Cfg:
        def get(self, k):
            return {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False}[k]

    set_language("es")
    try:
        wd = w.TaskbarWidget(Cfg())
        wd.set_devices([Device("1", "KB", "keyboard", 80, True)])
        assert "Teclado" in wd._pills[0].tooltip
        wd.set_radio(False)
        assert wd._pills[0].text == "OFF"
    finally:
        set_language("en")


# ---------------------------------------------------------------- tray startup toggle
def test_tray_startup_failure_reverts_and_does_not_persist(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "is_enabled", lambda: False)

    def boom(_e):
        raise OSError("denied")
    monkeypatch.setattr(startup, "set_enabled", boom)
    cfg = Config(tmp_path / "c.json")
    t = tray_mod.TrayController(cfg)
    assert not t._act_start.isChecked()
    t._act_start.setChecked(True)          # user toggles; set_enabled fails
    assert not t._act_start.isChecked()
    assert cfg.get("start_with_windows") is False


def test_tray_startup_success_and_sync(qapp, tmp_path, monkeypatch):
    state = {"on": False}
    monkeypatch.setattr(startup, "is_enabled", lambda: state["on"])
    monkeypatch.setattr(startup, "set_enabled", lambda e: state.update(on=e))
    cfg = Config(tmp_path / "c.json")
    t = tray_mod.TrayController(cfg)
    t._act_start.setChecked(True)
    assert state["on"] and cfg.get("start_with_windows") is True
    state["on"] = False                     # changed externally
    t._sync_menu()
    assert not t._act_start.isChecked()


# ---------------------------------------------------------------- monitor
@pytest.fixture
def fake_scan(monkeypatch):
    st = {"radio": True, "devices": [Device("a", "KB", "keyboard", 50, True)]}

    async def is_on():
        return st["radio"]

    async def scan():
        return [Device(d.id, d.name, d.kind, d.battery, d.connected) for d in st["devices"]]

    monkeypatch.setattr(scanner, "is_radio_on", is_on)
    monkeypatch.setattr(scanner, "scan", scan)
    return st


def test_monitor_emits_refresh_radio_and_stops(qapp, fake_scan):
    m = mon.DeviceMonitor(use_watchers=False)
    got, radios = [], []
    m.devices_changed.connect(got.append)
    m.radio_changed.connect(radios.append)
    m.start()
    thread = m._thread
    try:
        assert pump(qapp, lambda: len(got) >= 1)
        assert [d.id for d in got[0]] == ["a"]
        assert radios == [True]

        n = len(got)
        fake_scan["devices"] = [Device("b", "M", "mouse", 40, True)]
        m.refresh()                          # immediate, no 30 s wait
        assert pump(qapp, lambda: len(got) > n and [d.id for d in got[-1]] == ["b"], 3)

        fake_scan["radio"] = False
        m.refresh()
        assert pump(qapp, lambda: got[-1] == [] and radios[-1] is False)
    finally:
        m.stop()
    assert thread is not None and not thread.is_alive()
    assert m._thread is None


def test_monitor_survives_scan_error(qapp, fake_scan, monkeypatch, caplog):
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scan failed")
        return [Device("a", "KB", "keyboard", 50, True)]

    monkeypatch.setattr(scanner, "scan", flaky)
    m = mon.DeviceMonitor(use_watchers=False)
    got = []
    m.devices_changed.connect(got.append)
    m.start()
    try:
        assert pump(qapp, lambda: calls["n"] >= 1)
        m.refresh()
        assert pump(qapp, lambda: len(got) >= 1)
        assert "device scan failed" in caplog.text
    finally:
        m.stop()


def test_monitor_forgets_stale_estimator_state(qapp, fake_scan):
    m = mon.DeviceMonitor(use_watchers=False)
    m._estimator.update("old", 50, 0.0)
    got = []
    m.devices_changed.connect(got.append)
    m.start()
    try:
        assert pump(qapp, lambda: len(got) >= 1)
        assert "old" not in m._estimator._states
    finally:
        m.stop()


def test_monitor_restarts_after_crash(qapp, fake_scan, monkeypatch):
    monkeypatch.setattr(mon, "RESTART_BACKOFF_S", 0.05)
    real = mon.DeviceMonitor._run
    calls = {"n": 0}

    async def crashy(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("init failed")
        return await real(self)

    monkeypatch.setattr(mon.DeviceMonitor, "_run", crashy)
    m = mon.DeviceMonitor(use_watchers=False)
    got = []
    m.devices_changed.connect(got.append)
    t0 = time.monotonic()
    m.start()
    assert time.monotonic() - t0 < 4        # start() did not hang
    try:
        assert pump(qapp, lambda: len(got) >= 1)
        assert calls["n"] >= 2
    finally:
        m.stop()
