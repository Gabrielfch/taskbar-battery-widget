import json

from taskbar_battery_widget.config import MAX_DETAIL_DEVICES, Config
from taskbar_battery_widget.models import is_visibility_key, visibility_key

RAW = "BTHENUM\\DEV_AABBCCDDEEFF"


def test_set_over_limit_truncates_instead_of_raising(tmp_path):
    c = Config(tmp_path / "c.json")
    keys = [visibility_key(f"id{i}") for i in range(MAX_DETAIL_DEVICES + 10)]
    c.set("detail_log_devices", keys)
    assert c.get("detail_log_devices") == keys[:MAX_DETAIL_DEVICES]
    assert MAX_DETAIL_DEVICES < 200


def test_raw_ids_in_detail_devices_are_migrated_and_scrubbed(tmp_path):
    p = tmp_path / "c.json"
    k = visibility_key("keep")
    p.write_text(json.dumps({"detail_log_devices": [RAW, k]}), encoding="utf-8")
    c = Config(p)
    got = c.get("detail_log_devices")
    assert k in got and visibility_key(RAW) in got and all(is_visibility_key(v) for v in got)
    assert "AABBCCDDEEFF" not in p.read_text(encoding="utf-8")


def test_stored_oversized_list_is_truncated_on_load(tmp_path):
    p = tmp_path / "c.json"
    keys = [visibility_key(f"id{i}") for i in range(MAX_DETAIL_DEVICES + 5)]
    p.write_text(json.dumps({"detail_log_devices": keys}), encoding="utf-8")
    assert Config(p).get("detail_log_devices") == keys[:MAX_DETAIL_DEVICES]
