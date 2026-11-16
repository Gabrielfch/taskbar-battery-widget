"""Colour palettes, custom colours, config validation/migration, menu and dialog."""
import json

import pytest
from PySide6.QtGui import QColor

from taskbar_battery_widget import colors
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.custom_colors_dialog import CustomColorsDialog
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController, apply_appearance

BANDS = ("green", "amber", "orange", "red", "grey")


@pytest.mark.parametrize("name", colors.PALETTES)
def test_every_palette_defines_all_bands(name):
    colors.set_palette(name, {"high": "#00AAFF", "medium": "#FFFF00", "low": "#FF0000"})
    assert colors.get_palette() == name
    for band in BANDS:
        for dark in (True, False):
            assert colors.band_color(band, dark).isValid()


@pytest.mark.parametrize("name", ("classic", "colorblind", "pastel", "high_contrast", "mono"))
def test_builtin_palettes_are_readable(name):
    colors.set_palette(name)
    for dark in (True, False):
        for bg in colors.pill_backgrounds(dark).values():
            for band in BANDS:
                assert colors.contrast_ratio(colors.band_color(band, dark), bg) >= 4.5


def test_band_color_follows_active_palette():
    classic = colors.band_color("green", True).name()
    colors.set_palette("colorblind")
    assert colors.band_color("green", True).name() != classic
    assert colors.battery_color(90, False, 20, True).name() == colors.band_color("green", True).name()
    colors.set_palette("nonsense")
    assert colors.get_palette() == "classic" and colors.band_color("green", True).name() == classic


def test_ensure_contrast_darkens_light_yellow_on_light_bg():
    bg = colors.pill_backgrounds(False)["normal"]
    yellow = QColor(255, 255, 100)
    assert colors.contrast_ratio(yellow, bg) < 3
    assert colors.contrast_ratio(colors.ensure_contrast(yellow, bg), bg) >= 3.0
    ok = QColor(0, 0, 0)
    assert colors.ensure_contrast(ok, bg) == ok


def test_ensure_contrast_lightens_on_dark_bg():
    bg = colors.pill_backgrounds(True)["normal"]
    out = colors.ensure_contrast(QColor(20, 20, 60), bg)
    assert colors.contrast_ratio(out, bg) >= 3.0


@pytest.mark.parametrize("custom", (
    {"high": "#FFFF66", "medium": "#FFFF99", "low": "#202040"},
    {"high": "#808080", "medium": "#777777", "low": "#999999"},
    {"high": "#7F7F7F", "medium": "#8A8A8A", "low": "#6E6E6E"}))
def test_custom_palette_is_legible_in_both_themes(custom):
    colors.set_palette("custom", custom)
    for dark in (True, False):
        for bg in colors.pill_backgrounds(dark).values():
            for band in ("green", "amber", "orange", "red"):
                assert colors.contrast_ratio(colors.band_color(band, dark), bg) >= 4.5


def test_config_defaults_validation_and_migration(tmp_path):
    c = Config(tmp_path / "a.json")
    assert c.get("color_palette") == "classic" and c.get("icon_style") == "outline"
    assert c.get("custom_colors") == colors.CUSTOM_DEFAULTS
    for key, bad in (("color_palette", "neon"), ("icon_style", "x"), ("custom_colors", {"high": "#FFF"}),
                     ("custom_colors", {"high": "#GGGGGG", "medium": "#000000", "low": "#000000"})):
        with pytest.raises(ValueError):
            c.set(key, bad)
    c.set("custom_colors", {"high": "#00ff00", "medium": "#FFFF00", "low": "#FF0000"})
    c.set("icon_style", "none")
    assert Config(tmp_path / "a.json").get("icon_style") == "none"
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"color_mode": "mono"}), encoding="utf-8")
    assert Config(p).get("color_palette") == "mono"
    p.write_text(json.dumps({"color_mode": "levels", "color_palette": "pastel", "icon_style": "bad"}),
                 encoding="utf-8")
    c2 = Config(p)
    assert c2.get("color_palette") == "pastel" and c2.get("icon_style") == "outline"


def test_invalid_palette_with_mono_mode_keeps_mono(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"color_mode": "mono", "color_palette": "neon"}), encoding="utf-8")
    c = Config(p)
    assert c.get("color_palette") == "mono" and c.get("color_mode") == "mono"


def test_menu_radios_and_color_mode(qapp, tmp_path):
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    sm = t._settings
    pal, icons_ = sm.find("palette"), sm.find("icon_style")
    assert {o.value for o in pal.options()} == set(colors.PALETTES)
    assert {o.value for o in icons_.options()} == {"outline", "filled", "simple", "retro", "battery", "none"}
    assert pal.get() == "classic" and icons_.get() == "outline"
    changed = []
    t.ui_settings_changed.connect(lambda: changed.append(1))
    pal.set("mono")
    assert cfg.get("color_mode") == "mono" and cfg.get("color_palette") == "mono"
    assert colors.get_palette() == "mono" and pal.get() == "mono"
    pal.set("colorblind")
    assert cfg.get("color_mode") == "levels" and colors.get_palette() == "colorblind"
    assert pal.get() == "colorblind"
    icons_.set("retro")
    assert cfg.get("icon_style") == "retro" and icons_.get() == "retro"
    assert len(changed) == 3
    apply_appearance(cfg)
    assert colors.get_palette() == "colorblind"


def test_custom_choice_cancel_keeps_previous(qapp, tmp_path, monkeypatch):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    monkeypatch.setattr(CustomColorsDialog, "exec", lambda self: 0)
    t._settings.find("palette").set("custom")
    assert cfg.get("color_palette") == "classic"
    assert t._settings.find("palette").get() == "classic"
    monkeypatch.setattr(CustomColorsDialog, "exec", lambda self: 1)
    t._settings.find("palette").set("custom")
    assert cfg.get("color_palette") == "custom" and colors.get_palette() == "custom"


def test_dialog_returns_colors_and_resets(qapp):
    d = CustomColorsDialog({"high": "#112233", "medium": "#445566", "low": "#778899"})
    assert d.colors() == {"high": "#112233", "medium": "#445566", "low": "#778899"}
    d.set_color("low", "#ff0000")
    assert d.colors()["low"] == "#FF0000"
    d.reset()
    assert d.colors() == colors.CUSTOM_DEFAULTS
