from taskbar_battery_widget import visibility
from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.app import full_device_list
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.device_history import DeviceHistory
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget import widget as w


def _cfg(tmp_path, **kw):
    cfg = Config(tmp_path / "c.json")
    for k, v in kw.items():
        cfg.set(k, v)
    return cfg


def test_set_hidden_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    assert not visibility.is_hidden(cfg, "k1")
    assert visibility.set_hidden(cfg, "k1", True)
    assert visibility.set_hidden(cfg, "k1", True)      # idempotent, no duplicates
    assert visibility.set_hidden(cfg, "k2", True)
    assert cfg.get("hidden_devices") == ["k1", "k2"]
    assert visibility.is_hidden(cfg, "k1")
    assert visibility.set_hidden(cfg, "k1", False)
    assert cfg.get("hidden_devices") == ["k2"]
    assert visibility.show_all(cfg)
    assert cfg.get("hidden_devices") == []


def test_set_hidden_caps_and_rejects_bad_key(tmp_path):
    cfg = _cfg(tmp_path)
    assert not visibility.set_hidden(cfg, "", True)
    for i in range(visibility.HIDDEN_MAX + 20):
        visibility.set_hidden(cfg, f"k{i}", True)
    hidden = cfg.get("hidden_devices")
    assert len(hidden) == visibility.HIDDEN_MAX
    assert hidden[-1] == f"k{visibility.HIDDEN_MAX + 19}"


def test_set_hidden_reports_save_failure():
    class Broken:
        def get(self, k):
            return []

        def set(self, k, v):
            raise ValueError("nope")
    assert visibility.set_hidden(Broken(), "k", True) is False


def test_full_device_list_keeps_hidden_and_disconnected(qapp, tmp_path):
    set_language("en")
    cfg = _cfg(tmp_path, hidden_devices=[visibility_key("a")], show_disconnected=False,
               low_threshold=20)
    history = DeviceHistory(load=False)
    now = 1000.0
    devs = [Device("a", "A", "keyboard", 5, True), Device("b", "B", "mouse", 50, True),
            Device("c", "C", "mouse", None, False)]
    out = full_device_list(history, devs, cfg, now)
    assert [d.id for d in out] == ["a", "b", "c"]          # hidden + disconnected stay in the model

    # widget pills: hidden never shown, disconnected only with show_disconnected
    wd = w.TaskbarWidget(cfg)
    wd.set_devices(out)
    assert wd.pill_texts() == ["50%"]

    # alerts: the hidden low device is filtered by the caller; disconnected never alerts
    am = AlertManager(_cfg(tmp_path, notifications_enabled=True))
    assert am.check(visibility.visible_devices(out, cfg), now) == []
    # sanity: without hiding, the low device would alert
    assert len(AlertManager(_cfg(tmp_path, notifications_enabled=True)).check(out, now)) == 1


def test_disconnected_low_device_does_not_alert(tmp_path):
    cfg = _cfg(tmp_path, notifications_enabled=True)
    d = Device("x", "X", "mouse", 3, False)
    assert AlertManager(cfg).check([d], 0) == []
