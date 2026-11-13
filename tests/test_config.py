import json

from taskbar_battery_widget.config import DEFAULTS, Config
from taskbar_battery_widget.i18n import tr, set_language
from taskbar_battery_widget.models import KINDS


def test_legacy_config_is_copied_not_moved(tmp_path, monkeypatch):
    from taskbar_battery_widget import config as cfgmod
    monkeypatch.setattr(cfgmod, "_appdata", lambda: str(tmp_path))
    old = tmp_path / "BtBatteryBar" / "config.json"
    old.parent.mkdir()
    old.write_text('{"low_threshold": 33}', encoding="utf-8")
    c = Config()
    assert c.path == tmp_path / "TaskbarBatteryWidget" / "config.json"
    assert c.get("low_threshold") == 33
    assert old.exists()


def test_legacy_config_does_not_overwrite_new(tmp_path, monkeypatch):
    from taskbar_battery_widget import config as cfgmod
    monkeypatch.setattr(cfgmod, "_appdata", lambda: str(tmp_path))
    (tmp_path / "BtBatteryBar").mkdir()
    (tmp_path / "BtBatteryBar" / "config.json").write_text('{"low_threshold": 33}', encoding="utf-8")
    (tmp_path / "TaskbarBatteryWidget").mkdir()
    (tmp_path / "TaskbarBatteryWidget" / "config.json").write_text('{"low_threshold": 44}', encoding="utf-8")
    assert Config().get("low_threshold") == 44


def test_missing_file_gives_defaults(tmp_path):
    c = Config(tmp_path / "none" / "config.json")
    assert all(c.get(k) == v for k, v in DEFAULTS.items())


def test_corrupt_file_gives_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert Config(p).get("low_threshold") == 20


def test_non_dict_json_gives_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[1,2]", encoding="utf-8")
    assert Config(p).get("notifications_enabled") is True


def test_invalid_values_fall_back_per_key(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"low_threshold": "x", "show_disconnected": True,
                             "notification_cooldown_min": -5}), encoding="utf-8")
    c = Config(p)
    assert c.get("low_threshold") == 20
    assert c.get("show_disconnected") is True
    assert c.get("notification_cooldown_min") == 60


def test_set_persists_and_creates_dir(tmp_path):
    p = tmp_path / "sub" / "config.json"
    c = Config(p)
    c.set("low_threshold", 30)
    assert Config(p).get("low_threshold") == 30
    assert c.path == p


def test_i18n_keys_complete():
    keys = ["app_name", "bluetooth_off", "no_devices", "refresh",
            "notifications", "low_threshold", "start_with_windows", "quit", "devices",
            "low_title", "low_body", "charging", "disconnected", "battery_unknown"]
    keys += [f"kind_{k}" for k in KINDS]
    for lang in ("en", "es"):
        set_language(lang)
        for k in keys:
            assert tr(k) != k, (lang, k)
    set_language("en")
    assert tr("low_body", name="A", level=5) == "A is at 5%"
    assert tr("nonexistent_key") == "nonexistent_key"
