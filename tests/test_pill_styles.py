"""Pill styles (classic / soft / transparent / compact): config, Settings choice, geometry, state cues, contrast."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from taskbar_battery_widget import animation, colors, widget as w
from taskbar_battery_widget.config import DEFAULTS, PILL_STYLES, Config
from taskbar_battery_widget.i18n import set_language, tr
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.settings_model import Choice, SettingsCallbacks, build_settings_model
from taskbar_battery_widget.tray import TrayController


def setup_function(_):
    set_language("en")
    colors.set_palette("classic")


def teardown_function(_):
    colors.set_palette("classic")


DEVS = [Device("a", "Mouse", "mouse", 80, True), Device("b", "Pad", "gamepad", 12, True),
        Device("c", "Keys", "keyboard", 55, True)]


def make(tmp_path, style, devs=DEVS, light=False, **cfg_kw):
    cfg = Config(tmp_path / f"c-{style}-{light}.json")
    cfg.set("pill_style", style)
    cfg.set("animations", "off")
    for k, v in cfg_kw.items():
        cfg.set(k, v)
    wd = w.TaskbarWidget(cfg)
    wd._light = light
    wd.set_devices(list(devs))
    return wd


def render(wd) -> QImage:
    img = QImage(wd.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    wd.render(img)
    return img


# ------------------------------------------------------------------ config
def test_default_and_validation(tmp_path):
    assert PILL_STYLES == ("classic", "soft", "transparent", "compact")
    assert DEFAULTS["pill_style"] == "transparent"
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("pill_style") == "transparent"
    for good in PILL_STYLES:
        cfg.set("pill_style", good)
        assert cfg.get("pill_style") == good
    for bad in ("round", "", None, 2, True):
        with pytest.raises(ValueError):
            cfg.set("pill_style", bad)
    assert cfg.get("pill_style") == "compact"


def test_invalid_file_value_falls_back_to_default(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"pill_style": "neon"}', encoding="utf-8")
    assert Config(p).get("pill_style") == "transparent"


# ------------------------------------------------------------------ Settings choice + live apply
def test_choice_in_personalization(tmp_path):
    cfg = Config(tmp_path / "c.json")
    got = []
    model = build_settings_model(cfg, SettingsCallbacks(
        set_bool=print, set_threshold=print, set_palette=print, set_startup=print, open_device_log=print,
        open_logs_folder=print, startup_enabled=lambda: False, set_pill_style=got.append))
    ch = model.find("pill_style")
    assert isinstance(ch, Choice) and ch.label_key == "menu_pill_style"
    assert [o.value for o in ch.options()] == list(PILL_STYLES)
    assert [o.label for o in ch.options()] == ["Classic", "Soft", "Transparent", "Transparent compact"]
    assert ch.get() == "transparent"
    ch.set("soft")
    assert got == ["soft"]
    assert tr("menu_pill_style") == "Pill style"


def test_applies_live_without_rescan(qapp, tmp_path):
    t = TrayController(Config(tmp_path / "c.json"))
    seen = {"ui": 0, "full": 0}
    t.ui_settings_changed.connect(lambda: seen.__setitem__("ui", seen["ui"] + 1))
    t.settings_changed.connect(lambda: seen.__setitem__("full", seen["full"] + 1))
    t._set_pill_style("compact")
    assert t._cfg.get("pill_style") == "compact"
    assert seen == {"ui": 1, "full": 0}
    t._ensure_panel()
    t._settings.find("pill_style").set("classic")
    assert t._cfg.get("pill_style") == "classic" and seen == {"ui": 2, "full": 0}


def test_widget_relayouts_when_style_changes(qapp, tmp_path):
    wd = make(tmp_path, "classic")
    classic_w = wd.width()
    wd._cfg.set("pill_style", "compact")
    wd.refresh_style()          # what app.apply_ui_settings does
    assert wd.pill_style() == "compact" and wd.width() < classic_w


# ------------------------------------------------------------------ geometry
def test_padding_and_width_per_style(qapp, tmp_path):
    widths = {s: make(tmp_path, s).width() for s in PILL_STYLES}
    assert widths["classic"] == widths["soft"]
    assert widths["compact"] < widths["transparent"] < widths["classic"]
    pads = {s: make(tmp_path, s)._pad for s in PILL_STYLES}
    assert pads == {"classic": 8, "soft": 8, "transparent": 6, "compact": 3}
    gaps = {s: make(tmp_path, s)._rects for s in ("classic", "compact")}
    spacing = lambda r: r[1][0] - (r[0][0] + r[0][2])
    assert spacing(gaps["classic"]) == 4 and spacing(gaps["compact"]) == 2


def test_invalid_style_counts_as_transparent(qapp, tmp_path):
    wd = make(tmp_path, "classic")
    wd._cfg = type("C", (), {"get": lambda self, k: "weird" if k == "pill_style" else DEFAULTS[k]})()
    assert wd.pill_style() == "transparent"


def _separator_columns(wd, dark_taskbar=True):
    """x columns of the thin divider lines, found by scanning the empty gap between pills for painted pixels."""
    img = render(wd)
    out = []
    for (x, y, pw, h), (nx, *_rest) in zip(wd._rects, wd._rects[1:]):
        mid = int((x + pw + nx) / 2)
        cy = int(y + h / 2)
        out.append(img.pixelColor(mid, cy).alpha() > 0)
    return out


def test_separators_only_between_pills_for_transparent(qapp, tmp_path):
    assert _separator_columns(make(tmp_path, "transparent")) == [True, True]
    for style in ("classic", "soft", "compact"):
        assert _separator_columns(make(tmp_path, style)) == [False, False], style
    one = make(tmp_path, "transparent", devs=DEVS[:1])
    img = render(one)
    right_edge = one._rects[0][0] + one._rects[0][2]
    assert all(img.pixelColor(x, one.height() // 2).alpha() == 0 for x in range(right_edge, one.width()))


def test_separator_uses_subtle_secondary_colour(qapp):
    for dark in (True, False):
        c = colors.separator_color(dark)
        assert 0 < c.alpha() < 128
        assert (c.red(), c.green(), c.blue()) == ((255,) * 3 if dark else (0,) * 3)


def test_scale_changes_geometry(qapp, tmp_path):
    base = make(tmp_path, "transparent")
    big = make(tmp_path, "transparent", ui_scale=130)
    assert big._spacing >= base._spacing and big._pad >= base._pad


# ------------------------------------------------------------------ background / low marking
def _bg_pixel_alpha(wd, idx=0):
    x, y, pw, h = wd._rects[idx]
    img = render(wd)
    return img.pixelColor(int(x + 1), int(y + h / 2)).alpha()      # left edge of the pill, left of the icon


def test_no_background_for_transparent_styles(qapp, tmp_path):
    for style in ("transparent", "compact"):
        assert _bg_pixel_alpha(make(tmp_path, style)) == 0, style
    for style in ("classic", "soft"):
        assert _bg_pixel_alpha(make(tmp_path, style)) > 0, style


def test_soft_is_fainter_with_smaller_radius(qapp, tmp_path):
    classic, soft = make(tmp_path, "classic"), make(tmp_path, "soft")
    cx, cy = int(classic._rects[0][0] + classic._rects[0][2] / 2), 3
    assert 0 < render(soft).pixelColor(cx, cy).alpha() < render(classic).pixelColor(cx, cy).alpha()
    # square-ish corner: the very corner of a soft pill is painted, the one of a classic pill is not
    x, y = int(soft._rects[0][0]) + 1, int(soft._rects[0][1]) + 1
    assert render(soft).pixelColor(x, y).alpha() > render(classic).pixelColor(x, y).alpha()


def test_low_is_red_with_bang_and_no_outline_when_transparent(qapp, tmp_path):
    for style in ("transparent", "compact"):
        wd = make(tmp_path, style)
        low = next(p for p in wd._pills if p.level == 12)
        assert low.alert and low.warn and wd.pill_texts()[1] == "12%"
        assert wd._text_color(low, wd._colors(), True) == colors.band_color("red", True)
        # no outline / tint anywhere inside the low pill's box (only text, icon and level bar draw)
        img = render(wd)
        x, y, pw, h = wd._rects[1]
        assert img.pixelColor(int(x + 1), int(y + 1)).alpha() == 0
        assert img.pixelColor(int(x + pw - 2), int(y + h / 2)).alpha() == 0


def test_classic_keeps_outline_for_low(qapp, tmp_path):
    wd = make(tmp_path, "classic")
    x, y, pw, h = wd._rects[1]
    assert render(wd).pixelColor(int(x + pw / 2), int(y) + 1).alpha() > 0       # top outline of the low pill


# ------------------------------------------------------------------ contrast on the taskbar base
@pytest.mark.parametrize("palette", [p for p in colors.PALETTES if p != "mono"])
@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("style", ["transparent", "compact"])
def test_text_contrast_against_taskbar_base(qapp, tmp_path, palette, light, style):
    colors.set_palette(palette, {"high": "#FFFF00", "medium": "#FFEE88", "low": "#FFCCCC"})
    devs = [Device(str(n), f"d{n}", "mouse", n, True, charging=(n == 90)) for n in (95, 90, 50, 25, 10)]
    wd = make(tmp_path, style, devs=devs, light=light)
    base = colors.taskbar_base(not light)
    for pill in wd._pills:
        col = wd._text_color(pill, wd._colors(), not light)
        assert colors.contrast_ratio(col, base) >= 4.5, (palette, pill.level, light)


def test_contrast_correction_applies_to_poor_custom_colours(qapp, tmp_path):
    colors.set_palette("pastel")
    wd = make(tmp_path, "transparent", light=True, color_palette="custom",
              custom_colors={"high": "#FFFF00", "medium": "#FFFF00", "low": "#FFFF00"})
    pill = wd._pills[0]
    raw = wd.pill_color(pill)
    fixed = wd._text_color(pill, wd._colors(), False)
    assert colors.contrast_ratio(fixed, colors.taskbar_base(False)) >= 4.5
    assert raw is not None


def test_light_and_dark_foreground(qapp, tmp_path):
    dark = make(tmp_path, "transparent", light=False, color_mode="mono")
    light = make(tmp_path, "transparent", light=True, color_mode="mono")
    p = dark._pills[0]
    assert dark._text_color(p, dark._colors(), True).lightness() > 200
    assert light._text_color(light._pills[0], light._colors(), False).lightness() < 80


# ------------------------------------------------------------------ pulse target
def test_pulse_fades_text_not_background(qapp, tmp_path):
    assert animation.alert_text_opacity(0.0) == 1.0
    assert animation.alert_text_opacity(1.0) == pytest.approx(animation.ALERT_TEXT_MIN_OPACITY)
    assert 0.0 < animation.ALERT_TEXT_MIN_OPACITY < 1.0
    wd = make(tmp_path, "transparent", animations="always")
    wd.show()
    try:
        times = {}
        for name, t in (("still", 0), ("peak", animation.ALERT_PERIOD_MS // 2)):
            wd._anim_live = lambda: True
            wd._clock.now_ms = lambda t=t: t
            times[name] = render(wd)
        x, y, pw, h = wd._rects[1]
        # background pixels never change between the pulse extremes; the text pixels do
        bg = [(int(x + 1), int(y + 1)), (int(x + pw - 2), int(y + h - 2))]
        for pt in bg:
            assert times["still"].pixelColor(*pt) == times["peak"].pixelColor(*pt)
        assert times["still"] != times["peak"]
    finally:
        wd.hide()
