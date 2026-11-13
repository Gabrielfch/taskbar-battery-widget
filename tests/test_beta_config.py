"""Config keys for sources / beta readers / detailed log, and the experimental_readers migration."""
import json

import pytest

from taskbar_battery_widget.config import Config


def write(tmp_path, data):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_defaults(tmp_path):
    c = Config(tmp_path / "missing.json")
    assert c.get("beta_readers") is False
    assert c.get("bluetooth_enabled") is True
    assert c.get("wireless_enabled") is True
    assert c.get("dualsense_bt_extended") is True
    assert c.get("detail_log_enabled") is False
    assert c.get("detail_log_devices") == []
    assert c.get("experimental_readers") is None            # key no longer exists


def test_migration_true_turns_beta_on_and_drops_old_key(tmp_path):
    p = write(tmp_path, {"experimental_readers": True, "low_threshold": 30})
    c = Config(p)
    assert c.get("beta_readers") is True and c.get("low_threshold") == 30
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["beta_readers"] is True and "experimental_readers" not in saved


def test_migration_false_or_absent_keeps_beta_off(tmp_path):
    for data in ({"experimental_readers": False}, {}):
        p = write(tmp_path, data)
        assert Config(p).get("beta_readers") is False
        assert "experimental_readers" not in json.loads(p.read_text(encoding="utf-8"))


def test_migration_is_idempotent_and_beta_present_wins(tmp_path):
    p = write(tmp_path, {"experimental_readers": True})
    Config(p)
    first = p.read_text(encoding="utf-8")
    c = Config(p)
    assert p.read_text(encoding="utf-8") == first and c.get("beta_readers") is True
    c.set("beta_readers", False)                           # the user turned it off afterwards
    assert Config(p).get("beta_readers") is False
    p2 = write(tmp_path, {"beta_readers": False, "experimental_readers": True})
    assert Config(p2).get("beta_readers") is False         # explicit new key is never overridden


def test_bool_and_list_validation(tmp_path):
    c = Config(tmp_path / "c.json")
    for key in ("beta_readers", "bluetooth_enabled", "detail_log_enabled"):
        with pytest.raises(ValueError):
            c.set(key, 1)
    c.set("detail_log_devices", ["a" * 16, "b" * 16])
    assert c.get("detail_log_devices") == ["a" * 16, "b" * 16]
    for bad in ("x", [1], [""], ["x" * 201]):
        with pytest.raises(ValueError):
            c.set("detail_log_devices", bad)
    with pytest.raises(ValueError):
        c.set("experimental_readers", True)


def test_invalid_stored_values_fall_back_to_defaults(tmp_path):
    p = write(tmp_path, {"beta_readers": "yes", "bluetooth_enabled": 0, "detail_log_devices": "x"})
    c = Config(p)
    assert (c.get("beta_readers"), c.get("bluetooth_enabled"), c.get("detail_log_devices")) == \
        (False, True, [])
