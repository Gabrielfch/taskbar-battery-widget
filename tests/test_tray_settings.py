"""TrayController wiring of the settings model: toggles/choices persist into Config and notify."""
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController


def test_tray_settings_model_is_wired_to_config(qapp, tmp_path):
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    changes = []
    t.settings_changed.connect(lambda: changes.append(1))
    sm = t._settings
    assert [n.label() for n in sm.find("sources").children] == ["2.4 GHz and USB", "Bluetooth"]
    # toggles persist into the Config and emit settings_changed once each
    sm.find("wireless_enabled").set(False)
    sm.find("bluetooth_enabled").set(False)
    assert cfg.get("wireless_enabled") is False and cfg.get("bluetooth_enabled") is False
    assert len(changes) == 2
    # a choice persists too
    sm.find("low_threshold").set(25)
    assert cfg.get("low_threshold") == 25
    # and the live values follow changes made to the Config from outside
    cfg.set("bluetooth_enabled", True)
    cfg.set("low_threshold", 30)
    cfg.set("color_mode", "mono")
    assert sm.find("bluetooth_enabled").get() and sm.find("palette").get() == "mono"
    assert sm.find("low_threshold").get() == 30 and sm.find("low_threshold").current_label() == "30 %"
