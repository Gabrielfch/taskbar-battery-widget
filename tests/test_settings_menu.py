"""Settings submenu: sources toggles, persistence and sync."""
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController


def test_sources_has_two_toggles_that_persist(qapp, tmp_path):
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    changes = []
    t.settings_changed.connect(lambda: changes.append(1))
    st = [a.menu() for a in t.menu.actions() if a.menu() and a.text() == "Settings"][0]
    sources = [a.menu() for a in st.actions() if a.text() == "Sources"][0]
    assert [a.text() for a in sources.actions()] == ["2.4 GHz and USB", "Bluetooth"]
    t._act_wireless.setChecked(False)
    t._act_bluetooth.setChecked(False)
    assert cfg.get("wireless_enabled") is False and cfg.get("bluetooth_enabled") is False
    assert len(changes) == 2


def test_sync_reflects_external_config_changes(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    cfg.set("bluetooth_enabled", False)
    cfg.set("low_threshold", 30)
    cfg.set("color_mode", "mono")
    t._sync_menu()
    assert not t._act_bluetooth.isChecked() and not t._act_colors.isChecked()
    assert [v for v, a in t._thr_actions if a.isChecked()] == [30]


def test_threshold_action_persists(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    dict(t._thr_actions)[25].trigger()
    assert cfg.get("low_threshold") == 25
