import json

import pytest

from taskbar_battery_widget.config import DEFAULTS, MAX_FLOATING, Config


def _cfg(tmp_path):
    return Config(tmp_path / "config.json")


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _pos(anchor="top-left", dx=0, dy=0):
    return {"anchor": anchor, "dx": dx, "dy": dy}


def _key(i):
    return f"{i:012x}"  # 12 hex chars: a valid monitor key


def test_defaults(tmp_path):
    c = _cfg(tmp_path)
    assert c.get("placement_mode") == "taskbar"
    assert c.get("taskbar_anchor") == "right"
    assert c.get("placement_monitor") == "primary"
    assert c.get("floating_positions") == {}
    assert c.get("show_in_fullscreen") is False
    for key in ("placement_mode", "taskbar_anchor", "placement_monitor",
                "floating_positions", "show_in_fullscreen"):
        assert key in DEFAULTS


def test_defaults_do_not_leak_between_instances(tmp_path):
    a = Config(tmp_path / "a.json")
    a.get("floating_positions")["x"] = _pos()          # mutating a returned copy must not leak
    a.set("floating_positions", {"primary": _pos()})
    b = Config(tmp_path / "b.json")
    assert b.get("floating_positions") == {}
    assert DEFAULTS["floating_positions"] == {}


@pytest.mark.parametrize("value", ["taskbar", "floating"])
def test_placement_mode_valid(tmp_path, value):
    c = _cfg(tmp_path)
    c.set("placement_mode", value)
    assert c.get("placement_mode") == value


@pytest.mark.parametrize("value", ["Floating", "", "top", None, 1, True])
def test_placement_mode_invalid(tmp_path, value):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("placement_mode", value)
    assert c.get("placement_mode") == "taskbar"


@pytest.mark.parametrize("value", ["right", "left"])
def test_taskbar_anchor_valid(tmp_path, value):
    c = _cfg(tmp_path)
    c.set("taskbar_anchor", value)
    assert c.get("taskbar_anchor") == value


@pytest.mark.parametrize("value", ["center", "Right", "", None, 0])
def test_taskbar_anchor_invalid(tmp_path, value):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("taskbar_anchor", value)
    assert c.get("taskbar_anchor") == "right"


@pytest.mark.parametrize("value", ["primary", "a1b2c3d4e5f6", "a1b2c3d4e5f6#2", "#", "a" * 40])
def test_placement_monitor_valid(tmp_path, value):
    c = _cfg(tmp_path)
    c.set("placement_monitor", value)
    assert c.get("placement_monitor") == value


@pytest.mark.parametrize("value", ["", "A1B2C3", "xyz", "a" * 41, "monitor1", "a1 b2", None, 5])
def test_placement_monitor_invalid(tmp_path, value):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("placement_monitor", value)
    assert c.get("placement_monitor") == "primary"


@pytest.mark.parametrize("value", [True, False])
def test_show_in_fullscreen_valid(tmp_path, value):
    c = _cfg(tmp_path)
    c.set("show_in_fullscreen", value)
    assert c.get("show_in_fullscreen") is value


@pytest.mark.parametrize("value", ["yes", 1, 0, None])
def test_show_in_fullscreen_invalid(tmp_path, value):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("show_in_fullscreen", value)
    assert c.get("show_in_fullscreen") is False


def test_floating_positions_valid_set_and_get(tmp_path):
    c = _cfg(tmp_path)
    value = {"primary": _pos("bottom-right", 10, -20), "a1b2c3d4e5f6#2": _pos("middle-left")}
    c.set("floating_positions", value)
    assert c.get("floating_positions") == value


@pytest.mark.parametrize("value", [
    [],
    "primary",
    None,
    {"bad key!": _pos()},
    {"primary": "top-left"},
    {"primary": _pos("center")},
    {"primary": {"anchor": "top-left", "dx": 1}},
    {"primary": _pos(dx=True)},
    {"primary": _pos(dy=1.5)},
    {"primary": _pos(dx="3")},
    {"primary": _pos()} | {_key(i): _pos() for i in range(MAX_FLOATING)},  # 17 entries
])
def test_floating_positions_invalid_set_raises(tmp_path, value):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("floating_positions", value)
    assert c.get("floating_positions") == {}


def test_floating_positions_max_entries_on_set(tmp_path):
    c = _cfg(tmp_path)
    full = {_key(i): _pos() for i in range(MAX_FLOATING)}
    c.set("floating_positions", full)
    assert len(c.get("floating_positions")) == MAX_FLOATING
    with pytest.raises(ValueError):
        c.set("floating_positions", {**full, "primary": _pos()})


def test_floating_positions_partial_drop_on_load(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"floating_positions": {
        "primary": _pos("top-right", 5, 6),
        "a1b2c3d4e5f6": {"anchor": "sideways", "dx": 0, "dy": 0},
    }})
    c = Config(path)
    assert c.get("floating_positions") == {"primary": _pos("top-right", 5, 6)}


def test_floating_positions_load_drops_bad_keys_and_bool_offsets(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"floating_positions": {
        "primary": _pos(),
        "NOT-HEX": _pos(),
        "a1b2c3d4e5f6": {"anchor": "top-left", "dx": True, "dy": 0},
        "b1b2c3d4e5f6": "nope",
        "c1b2c3d4e5f6": _pos("bottom-center", -3, 4),
    }})
    c = Config(path)
    assert c.get("floating_positions") == {
        "primary": _pos(),
        "c1b2c3d4e5f6": _pos("bottom-center", -3, 4),
    }


def test_floating_positions_load_keeps_at_most_max_entries(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"floating_positions": {_key(i): _pos() for i in range(MAX_FLOATING + 4)}})
    c = Config(path)
    assert len(c.get("floating_positions")) == MAX_FLOATING


def test_floating_positions_non_dict_on_load_falls_back_to_default(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"floating_positions": [_pos()]})
    assert Config(path).get("floating_positions") == {}


@pytest.mark.parametrize("dx,dy,expected", [
    (99999, -99999, (10000, -10000)),
    (10000, -10000, (10000, -10000)),
    (-10001, 10001, (-10000, 10000)),
    (0, 0, (0, 0)),
])
def test_floating_positions_offsets_clamped_on_set(tmp_path, dx, dy, expected):
    c = _cfg(tmp_path)
    c.set("floating_positions", {"primary": _pos("top-left", dx, dy)})
    got = c.get("floating_positions")["primary"]
    assert (got["dx"], got["dy"]) == expected


def test_floating_positions_offsets_clamped_on_load(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"floating_positions": {"primary": _pos("bottom-left", 123456, -987654)}})
    got = Config(path).get("floating_positions")["primary"]
    assert (got["dx"], got["dy"]) == (10000, -10000)


def test_round_trip_save_and_load(tmp_path):
    path = tmp_path / "config.json"
    c = Config(path)
    c.set("placement_mode", "floating")
    c.set("taskbar_anchor", "left")
    c.set("placement_monitor", "a1b2c3d4e5f6#2")
    c.set("show_in_fullscreen", True)
    c.set("floating_positions", {"primary": _pos("middle-right", 50, -60),
                                 "a1b2c3d4e5f6#2": _pos("bottom-center", 0, 0)})
    reloaded = Config(path)
    assert reloaded.get("placement_mode") == "floating"
    assert reloaded.get("taskbar_anchor") == "left"
    assert reloaded.get("placement_monitor") == "a1b2c3d4e5f6#2"
    assert reloaded.get("show_in_fullscreen") is True
    assert reloaded.get("floating_positions") == {
        "primary": _pos("middle-right", 50, -60),
        "a1b2c3d4e5f6#2": _pos("bottom-center", 0, 0),
    }


def test_invalid_stored_scalars_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    _write(path, {"placement_mode": "dock", "taskbar_anchor": "top",
                  "placement_monitor": "Monitor 1", "show_in_fullscreen": "yes"})
    c = Config(path)
    assert c.get("placement_mode") == "taskbar"
    assert c.get("taskbar_anchor") == "right"
    assert c.get("placement_monitor") == "primary"
    assert c.get("show_in_fullscreen") is False


def test_existing_keys_still_validated(tmp_path):
    c = _cfg(tmp_path)
    with pytest.raises(ValueError):
        c.set("low_threshold", 0)
    with pytest.raises(ValueError):
        c.set("hidden_devices", "abc")
    c.set("low_threshold", 35)
    assert c.get("low_threshold") == 35
