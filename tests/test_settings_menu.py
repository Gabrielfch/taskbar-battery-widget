"""Settings model: sources toggles, persistence and live values."""
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController


def test_sources_has_two_toggles_that_persist(qapp, tmp_path):
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    changes = []
    t.settings_changed.connect(lambda: changes.append(1))
    sources = t._settings.find("sources")
    assert [n.label() for n in sources.children] == ["2.4 GHz and USB", "Bluetooth"]
    t._settings.find("wireless_enabled").set(False)
    t._settings.find("bluetooth_enabled").set(False)
    assert cfg.get("wireless_enabled") is False and cfg.get("bluetooth_enabled") is False
    assert len(changes) == 2


def test_values_reflect_external_config_changes(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    cfg.set("bluetooth_enabled", False)
    cfg.set("low_threshold", 30)
    cfg.set("color_mode", "mono")
    sm = t._settings
    assert not sm.find("bluetooth_enabled").get() and sm.find("palette").get() == "mono"
    assert sm.find("low_threshold").get() == 30 and sm.find("low_threshold").current_label() == "30 %"


def test_threshold_choice_persists(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    assert [o.value for o in t._settings.find("low_threshold").options()] == [10, 15, 20, 25, 30]
    t._settings.find("low_threshold").set(25)
    assert cfg.get("low_threshold") == 25
