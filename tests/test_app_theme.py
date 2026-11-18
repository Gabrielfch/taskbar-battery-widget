"""App theme (auto / light / dark): config, Settings choice, sun/moon button, Personalization toolbar button."""
import json

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication

from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import APP_THEMES, DEFAULTS, Config
from taskbar_battery_widget.i18n import set_language, tr
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.placement import ScreenInfo
from taskbar_battery_widget.settings_model import Choice, SettingsCallbacks, build_settings_model
from taskbar_battery_widget.tray import TrayController, apply_color_scheme


def setup_function(_):
    set_language("en")


@pytest.fixture(autouse=True)
def _restore_scheme(qapp):
    yield
    apply_color_scheme("auto")      # the colour scheme is process-wide: never leak it into other tests


@pytest.fixture
def tray(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_devices([Device("a", "Mouse", "mouse", 80, True)])
    yield t
    if t._panel is not None:
        t._panel.close()
        t._panel.deleteLater()


# ------------------------------------------------------------------ config
def test_default_and_validation(tmp_path):
    assert DEFAULTS["app_theme"] == "auto" and APP_THEMES == ("auto", "light", "dark")
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("app_theme") == "auto"
    for good in APP_THEMES:
        cfg.set("app_theme", good)
        assert cfg.get("app_theme") == good
    for bad in ("blue", "", None, 1, True):
        with pytest.raises(ValueError):
            cfg.set("app_theme", bad)
    assert cfg.get("app_theme") == "dark"


def test_saved_value_roundtrips_and_invalid_file_value_falls_back(tmp_path):
    p = tmp_path / "c.json"
    cfg = Config(p)
    cfg.set("app_theme", "light")
    assert json.loads(p.read_text(encoding="utf-8"))["app_theme"] == "light"
    assert Config(p).get("app_theme") == "light"
    p.write_text(json.dumps({"app_theme": "purple"}), encoding="utf-8")
    assert Config(p).get("app_theme") == "auto"
    p.write_text(json.dumps({"low_threshold": 15}), encoding="utf-8")      # an old file without the key
    assert Config(p).get("app_theme") == "auto"


# ------------------------------------------------------------------ Settings > Personalization
def test_personalization_choice(tmp_path):
    cfg = Config(tmp_path / "c.json")
    calls = []
    cb = SettingsCallbacks(set_bool=lambda k, v: None, set_threshold=lambda v: None,
                           set_palette=lambda v: None, set_startup=lambda v: None, open_device_log=lambda: None,
                           open_logs_folder=lambda: None, startup_enabled=lambda: False,
                           set_app_theme=calls.append)
    m = build_settings_model(cfg, cb)
    node = m.find("app_theme")
    assert isinstance(node, Choice) and m.parent_of("app_theme").id == "personalization"
    assert node.label() == "Theme"
    assert [(o.value, o.label) for o in node.options()] == [
        ("auto", "Automatic (follow Windows)"), ("light", "Light"), ("dark", "Dark")]
    assert node.get() == "auto" and node.current_label() == "Automatic (follow Windows)"
    node.set("light")
    assert calls == ["light"]
    cfg.set("app_theme", "dark")
    assert node.get() == "dark"


def test_choice_in_the_panel_restyles_live_and_can_return_to_auto(tray):
    p = tray._ensure_panel()
    tray.set_dark(True)                         # the taskbar is dark
    p.set_model(build_panel_model(tray._devices, (), True, 20, True))
    p.show_settings("personalization")
    node = tray._settings.find("app_theme")
    node.set("light")
    assert tray._cfg.get("app_theme") == "light"
    assert p.dark is False and p.settings_view._dark is False       # panel and Settings page restyled
    assert p.in_settings and p.settings_view.page_id == "personalization"
    node.set("auto")
    assert tray._cfg.get("app_theme") == "auto" and p.dark is True  # back to the taskbar's look


# ------------------------------------------------------------------ sun / moon button
def _theme_btn(p):
    return p._buttons[("", "theme")]


def test_button_shows_moon_when_dark_and_sun_when_light(tray):
    p = tray._ensure_panel()
    tray.set_dark(True)
    tray.open_panel()
    b = _theme_btn(p)
    assert p.dark and b.toolTip() == "Switch to light theme" == b.accessibleName()
    assert not b.icon().isNull()
    dark_img = b.icon().pixmap(18, 18).toImage()
    tray._cfg.set("app_theme", "light")
    tray._set_app_theme("light")
    b = _theme_btn(p)
    assert not p.dark and b.toolTip() == "Switch to dark theme" == b.accessibleName()
    assert b.icon().pixmap(18, 18).toImage() != dark_img            # another glyph (sun)


def test_click_toggles_and_saves(tray, tmp_path):
    p = tray._ensure_panel()
    tray.set_dark(True)
    tray.open_panel()
    assert tray._cfg.get("app_theme") == "auto"
    _theme_btn(p).click()                       # dark -> light, explicit
    assert tray._cfg.get("app_theme") == "light" and not p.dark
    assert Config(tmp_path / "c.json").get("app_theme") == "light"      # saved to disk
    _theme_btn(p).click()                       # light -> dark
    assert tray._cfg.get("app_theme") == "dark" and p.dark


def test_toggle_from_auto_follows_the_effective_look(tray):
    tray.set_dark(False)                        # light taskbar: the panel is light, the click goes dark
    p = tray._ensure_panel()
    tray.open_panel()
    assert not p.dark
    _theme_btn(p).click()
    assert tray._cfg.get("app_theme") == "dark" and p.dark


def test_live_restyle_changes_the_stylesheet(tray):
    p = tray._ensure_panel()
    tray.set_dark(True)
    tray.open_panel()
    dark_css = p.styleSheet()
    tray._set_app_theme("light")
    assert p.styleSheet() != dark_css
    tray._set_app_theme("dark")
    assert p.styleSheet() == dark_css


def test_explicit_theme_ignores_taskbar_changes_and_auto_follows(tray):
    p = tray._ensure_panel()
    tray._set_app_theme("light")
    tray.set_dark(True)
    tray.set_dark(False)
    tray.set_dark(True)
    assert not p.dark
    tray._set_app_theme("auto")
    assert p.dark


def test_panel_created_after_the_choice_uses_it(tray):
    tray._set_app_theme("light")
    assert tray._ensure_panel().dark is False


# ------------------------------------------------------------------ Personalization toolbar button
def test_toolbar_button_opens_personalization_and_back_returns_to_devices(tray):
    p = tray._ensure_panel()
    b = p._toolbar["personalization"]
    assert list(p._toolbar).index("personalization") == list(p._toolbar).index("settings") + 1
    assert b.toolTip() == "Personalization" == b.accessibleName() and not b.icon().isNull()
    tray.open_panel()
    b.click()
    assert p.in_settings and p.settings_view.page_id == "personalization"
    assert p.settings_view.path_ids == ["personalization"]
    p.settings_view.back_button().click()       # Back: where the user came from, not the Settings root
    assert not p.in_settings


def test_gear_still_opens_the_settings_root(tray):
    p = tray._ensure_panel()
    tray.open_panel()
    p._toolbar["settings"].click()
    assert p.in_settings and p.settings_view.path_ids == ["root"]
    p.show_settings("position")                 # nested pages keep their normal path
    assert p.settings_view.path_ids == ["root", "personalization", "position"]


# ------------------------------------------------------------------ pills
P = ScreenInfo("aaa", "\\\\.\\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)


def test_pills_follow_the_taskbar_unless_floating(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    wd = w.TaskbarWidget(cfg)
    try:
        wd.set_light_theme(True)                # light taskbar
        cfg.set("app_theme", "dark")
        assert wd._pill_dark() is False         # on the taskbar the pills keep following it
        wd._floating = True
        assert wd._pill_dark() is True          # floating: the chosen theme
        cfg.set("app_theme", "light")
        wd.set_light_theme(False)
        assert wd._pill_dark() is False
        cfg.set("app_theme", "auto")
        assert wd._pill_dark() is True          # auto: the taskbar again (dark)
    finally:
        wd.stop()
        wd.deleteLater()


def test_strings_exist():
    for k in ("menu_theme", "theme_auto", "theme_light", "theme_dark", "theme_to_light", "theme_to_dark"):
        assert tr(k) != k


def test_color_scheme_is_set_even_when_it_equals_the_effective_one(monkeypatch):
    """Qt reports the EFFECTIVE scheme: "dark" while Windows is dark must still pin the override."""
    calls = []

    class Hints:
        def colorScheme(self):
            return Qt.ColorScheme.Dark

        def setColorScheme(self, s):
            calls.append(s)

    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda: Hints()))
    apply_color_scheme("dark")
    assert calls == [Qt.ColorScheme.Dark]


def test_color_scheme_is_pushed_to_qt(monkeypatch):
    """The offscreen platform ignores the setter, so a stand-in records what the app asks for."""
    class Hints:
        scheme = Qt.ColorScheme.Unknown

        def colorScheme(self):
            return self.scheme

        def setColorScheme(self, s):
            self.scheme = s

    hints = Hints()
    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda: hints))
    for theme, want in (("dark", Qt.ColorScheme.Dark), ("light", Qt.ColorScheme.Light),
                        ("auto", Qt.ColorScheme.Unknown), ("junk", Qt.ColorScheme.Unknown)):
        apply_color_scheme(theme)
        assert hints.scheme == want


# ------------------------------------------------------------------ review follow-ups
def test_theme_change_does_not_rescan_devices(tray):
    seen = {"ui": 0, "full": 0}
    tray.ui_settings_changed.connect(lambda: seen.__setitem__("ui", seen["ui"] + 1))
    tray.settings_changed.connect(lambda: seen.__setitem__("full", seen["full"] + 1))
    tray._set_app_theme("light")
    tray._on_theme_toggle()
    assert seen == {"ui": 2, "full": 0}     # settings_changed is what triggers monitor.refresh() in the app


def test_app_wiring_theme_change_never_calls_monitor_refresh(tray):
    """app.py connects settings_changed -> monitor.refresh; the theme must only use the UI-only signal."""
    refreshed = []
    tray.settings_changed.connect(lambda: refreshed.append(1))
    tray._ensure_panel()
    tray._on_theme_toggle()
    tray._settings.find("app_theme").set("auto")
    assert refreshed == []


def test_direct_personalization_checks_its_own_button_not_the_gear(tray):
    p = tray._ensure_panel()
    tray.open_panel()
    p._toolbar["personalization"].click()
    assert p._toolbar["personalization"].isChecked() and not p._toolbar["settings"].isChecked()
    p._toolbar["settings"].click()              # the gear opens the Settings root from there
    assert p.in_settings and p.settings_view.path_ids == ["root"]
    assert p._toolbar["settings"].isChecked() and not p._toolbar["personalization"].isChecked()
    p._toolbar["personalization"].click()
    p._toolbar["personalization"].click()       # toggles back to the device list
    assert not p.in_settings
    assert not p._toolbar["personalization"].isChecked() and not p._toolbar["settings"].isChecked()


def test_direct_page_survives_refresh_and_model_replacement(tray):
    p = tray._ensure_panel()
    tray.open_panel()
    p.show_settings("personalization", direct=True)
    p.settings_view.refresh()
    p.refresh_settings()
    assert p.settings_view.path_ids == ["personalization"]
    p.set_settings_model(tray._build_settings())        # a new tree: the stack must stay direct
    assert p.settings_view.path_ids == ["personalization"]
    p.show_settings("position", direct=True)
    p.set_settings_model(tray._build_settings())
    assert p.settings_view.path_ids == ["position"]
    p.settings_view.back_button().click()
    assert not p.in_settings


def test_nested_direct_path_keeps_its_base_across_a_model_replacement(tray):
    p = tray._ensure_panel()
    tray.open_panel()
    p.show_settings("personalization", direct=True)
    p.settings_view.row("position").click()
    assert p.settings_view.path_ids == ["personalization", "position"]
    p.set_settings_model(tray._build_settings())
    assert p.settings_view.path_ids == ["personalization", "position"]


@pytest.mark.parametrize("scale", [90, 100, 115, 130])
def test_six_button_toolbar_fits_the_smallest_panel(qapp, scale):
    from taskbar_battery_widget import device_panel as dp
    p = dp.DevicePanel(dark=True)
    try:
        p.set_metrics("compact", scale)
        p.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        p._screen_avail = QRect(0, 0, 800, 600)     # tiny screen: the minimum width
        p._fit(p._screen_avail)
        p.show()
        qapp.processEvents()
        tb = p._toolbar_widget
        assert len(p._toolbar) == 6
        assert tb.layout().minimumSize().width() <= p.width() - 2 * p.layout().contentsMargins().left()
        for b in p._toolbar.values():
            assert tb.rect().contains(b.geometry())
    finally:
        p.close()


def test_floating_pills_render_with_the_chosen_theme(qapp, tmp_path):
    from PySide6.QtGui import QColor
    cfg = Config(tmp_path / "c.json")
    wd = w.TaskbarWidget(cfg)
    try:
        wd.set_devices([Device("1", "KB", "keyboard", 80, True)])
        wd.set_light_theme(False)                   # dark taskbar
        wd._floating = True
        luma = []
        for theme in ("light", "dark"):
            cfg.set("app_theme", theme)
            wd.set_config(cfg)                      # (an unplaced widget resets the flag: set it again)
            wd._floating = True
            wd._rebuild()
            img = wd.grab().toImage()
            row = [QColor(img.pixelColor(x, img.height() // 2)) for x in range(img.width())]
            c = next(c for c in row if c.alpha() > 200)             # the backdrop (not the faint border)
            luma.append(c.lightness())
        assert luma[0] > 200 and luma[1] < 60       # light backdrop for "light", dark for "dark", on a dark taskbar
    finally:
        wd.stop()
        wd.deleteLater()
