"""Settings page inside the device panel: navigation, toggles, accordion choices, keyboard, themes."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget.i18n import set_language, tr_in
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.settings_model import SettingsCallbacks, build_settings_model


class Cfg:
    def __init__(self):
        self.d = {"wireless_enabled": True, "bluetooth_enabled": True, "notifications_enabled": True,
                  "full_charge_alert": True, "detail_log_enabled": False, "start_with_windows": False,
                  "show_in_fullscreen": False, "low_threshold": 20, "color_palette": "classic",
                  "color_mode": "levels", "icon_style": "outline", "placement_mode": "taskbar",
                  "taskbar_anchor": "right", "placement_monitor": "primary", "floating_positions": {},
                  "panel_size": "normal", "ui_scale": 100, "language": "auto"}

    def get(self, k):
        return self.d[k]


class Env:
    def __init__(self):
        self.cfg = Cfg()
        self.calls = []
        cfg, calls = self.cfg, self.calls

        def set_bool(k, v):
            cfg.d[k] = bool(v)
            calls.append(("bool", k, v))

        def set_palette(v):
            cfg.d["color_palette"] = v
            cfg.d["color_mode"] = "mono" if v == "mono" else "levels"      # as the tray does
            calls.append(("palette", v))

        def set_icon_style(v):
            cfg.d["icon_style"] = v
            calls.append(("icon", v))

        def set_threshold(v):
            cfg.d["low_threshold"] = v
            calls.append(("thr", v))
        cb = SettingsCallbacks(
            set_bool=set_bool, set_threshold=set_threshold, set_palette=set_palette,
            set_startup=lambda v: calls.append(("startup", v)),
            open_device_log=lambda: calls.append(("device_log",)),
            open_logs_folder=lambda: calls.append(("logs_folder",)), startup_enabled=lambda: False,
            open_detail_folder=lambda: calls.append(("detail_folder",)), set_icon_style=set_icon_style,
            reset_position=lambda: calls.append(("reset",)),
            open_custom_icons=lambda: calls.append(("custom_icons",)))
        self.model = build_settings_model(cfg, cb)


@pytest.fixture
def env():
    set_language("en")
    return Env()


@pytest.fixture
def panel(qapp, env):
    p = dp.DevicePanel(dark=True)
    devs = [Device(id="a", name="Mouse", kind="mouse", battery=80, connected=True)]
    p.set_model(build_panel_model(devs, set(), False, 20, True, True))
    p.set_settings_model(env.model)
    p.settings_requested.connect(lambda _r: p.show_settings())     # the tray's answer to the gear
    p.show_at(QPoint(400, 400))
    yield p
    p.close()


def click(row):
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)


def key(widget, k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(widget, k, mod)


def open_settings(panel):
    panel._toolbar["settings"].click()
    assert panel.in_settings
    return panel.settings_view


def test_gear_alternates_and_is_marked(panel):
    gear = panel._toolbar["settings"]
    assert not gear.isChecked() and not panel.in_settings
    gear.click()
    assert panel.in_settings and gear.isChecked() and panel.isVisible()
    gear.click()
    assert not panel.in_settings and not gear.isChecked() and panel.isVisible()


def test_panel_keeps_position_and_width(panel):
    g0 = panel.geometry()
    open_settings(panel)
    g1 = panel.geometry()
    assert g1.width() == g0.width() and g1.x() == g0.x()
    avail = panel._screen_avail
    assert g1.height() <= avail.height() * dp.PANEL_SIZE_FRACTION["normal"] + 1


def test_root_rows_and_title(panel):
    v = open_settings(panel)
    assert v.title_text() == "Settings"
    assert list(v.rows()) == ["sources", "alerts", "logs", "start_with_windows", "personalization", "tutorial",
                              "whats_new"]
    assert all(r.height() >= dp.BTN_PX for r in v.rows().values())
    assert v.back_button().width() >= dp.BTN_PX and v.back_button().height() >= dp.BTN_PX


def test_enter_page_and_back_with_arrow(panel):
    v = open_settings(panel)
    click(v.row("sources"))
    assert v.path_ids == ["root", "sources"] and v.title_text() == "Sources"
    assert list(v.rows()) == ["wireless_enabled", "bluetooth_enabled", "razer_dongle_wake"]
    v.back_button().click()
    assert v.path_ids == ["root"] and panel.in_settings
    v.back_button().click()                              # from the root: the device list
    assert not panel.in_settings and panel.isVisible()
    assert not panel._toolbar["settings"].isChecked()


@pytest.mark.parametrize("k,mod", [(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
                                   (Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier),
                                   (Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)])
def test_back_keys(panel, k, mod):
    v = open_settings(panel)
    click(v.row("personalization"))
    click(v.row("position"))
    assert v.path_ids == ["root", "personalization", "position"]
    key(v.row("placement"), k, mod)
    assert v.path_ids == ["root", "personalization"]
    key(v.row("palette"), k, mod)
    assert v.path_ids == ["root"]
    key(v.row("sources"), k, mod)
    assert not panel.in_settings and panel.isVisible()


def test_escape_on_devices_closes_panel_but_not_in_settings(panel):
    v = open_settings(panel)
    key(panel._toolbar["about"], Qt.Key.Key_Escape)      # focus on the toolbar while in Settings
    assert not panel.in_settings and panel.isVisible()
    key(panel._toolbar["about"], Qt.Key.Key_Escape)
    assert not panel.isVisible()


def test_toggle_changes_config_and_keeps_panel(panel, env):
    v = open_settings(panel)
    click(v.row("sources"))
    assert v.row("wireless_enabled").isChecked()
    click(v.row("wireless_enabled"))
    assert env.cfg.d["wireless_enabled"] is False and ("bool", "wireless_enabled", False) in env.calls
    assert not v.row("wireless_enabled").isChecked() and panel.isVisible() and panel.in_settings
    click(v.row("wireless_enabled"))
    assert v.row("wireless_enabled").isChecked()


def test_toggle_reflects_real_state_when_the_change_fails(panel, env):
    v = open_settings(panel)
    click(v.row("start_with_windows"))                   # callback records but the real state stays off
    assert ("startup", True) in env.calls
    assert not v.row("start_with_windows").isChecked()


def test_choice_accordion_open_pick_and_stay_open(panel, env):
    v = open_settings(panel)
    click(v.row("personalization"))
    row = v.row("palette")
    assert row.value_text() == "Classic" and v.open_choice is None
    assert row.accessibleName() == "Colors, Classic, collapsed"
    click(row)
    assert v.open_choice == "palette"
    assert v.row("palette").accessibleName() == "Colors, Classic, expanded"
    assert [r for r in v.rows() if r.startswith("palette:")] == [
        "palette:classic", "palette:colorblind", "palette:pastel", "palette:high_contrast",
        "palette:mono", "palette:custom"]
    assert v.row("palette:classic").isChecked() and not v.row("palette:pastel").isChecked()
    click(v.row("palette:pastel"))
    assert env.cfg.d["color_palette"] == "pastel"
    assert v.open_choice == "palette" and v.row("palette:pastel").isChecked()      # still open
    assert not v.row("palette:classic").isChecked() and v.row("palette").value_text() == "Pastel"
    click(v.row("palette:mono"))                                                    # try several
    assert env.cfg.d["color_palette"] == "mono" and panel.isVisible()
    click(v.row("palette"))                                                         # same row folds it
    assert v.open_choice is None and "palette:mono" not in v.rows()
    assert v.row("palette").accessibleName() == "Colors, Monochrome, collapsed"


def test_only_one_accordion_open(panel):
    v = open_settings(panel)
    click(v.row("personalization"))
    click(v.row("palette"))
    assert v.open_choice == "palette"
    click(v.row("icon_style"))
    assert v.open_choice == "icon_style"
    assert not any(r.startswith("palette:") for r in v.rows())
    assert any(r.startswith("icon_style:") for r in v.rows())


def test_navigating_folds_the_accordion(panel):
    v = open_settings(panel)
    click(v.row("personalization"))
    click(v.row("palette"))
    click(v.row("position"))
    assert v.open_choice is None
    v.back_button().click()
    assert v.open_choice is None and not any(r.startswith("palette:") for r in v.rows())


def test_action_calls_callback(panel, env):
    v = open_settings(panel)
    click(v.row("logs"))
    click(v.row("open_device_log"))
    click(v.row("open_logs_folder"))
    click(v.row("open_detail_folder"))
    assert [c[0] for c in env.calls] == ["device_log", "logs_folder", "detail_folder"]
    assert panel.isVisible() and panel.in_settings


def test_reset_action_disabled_when_not_useful(panel, env):
    v = open_settings(panel)
    click(v.row("personalization"))
    click(v.row("position"))
    assert not v.row("reset_position").isEnabled()
    env.cfg.d["placement_mode"] = "floating"
    v.refresh()
    assert v.row("reset_position").isEnabled()
    click(v.row("reset_position"))
    assert env.calls[-1] == ("reset",)


def test_threshold_choice_labels(panel, env):
    v = open_settings(panel)
    click(v.row("alerts"))
    assert v.row("low_threshold").value_text() == "20 %"
    click(v.row("low_threshold"))
    click(v.row("low_threshold:25"))
    assert env.cfg.d["low_threshold"] == 25 and v.row("low_threshold").value_text() == "25 %"


def test_keyboard_navigation(panel, env):
    v = open_settings(panel)
    first = v.row("sources")
    first.setFocus()
    key(first, Qt.Key.Key_Down)
    assert panel.focusWidget() is v.row("alerts")
    key(v.row("alerts"), Qt.Key.Key_Up)
    assert panel.focusWidget() is first
    key(first, Qt.Key.Key_Up)
    assert panel.focusWidget() is v.back_button()
    key(first, Qt.Key.Key_Right)                          # Right enters the page
    assert v.path_ids == ["root", "sources"]
    assert panel.focusWidget() is v.row("wireless_enabled")
    key(v.row("wireless_enabled"), Qt.Key.Key_Return)     # Enter activates
    assert env.cfg.d["wireless_enabled"] is False
    key(v.row("wireless_enabled"), Qt.Key.Key_Space)      # Space too
    assert env.cfg.d["wireless_enabled"] is True
    key(v.row("wireless_enabled"), Qt.Key.Key_Left)       # Left goes back
    assert v.path_ids == ["root"] and panel.focusWidget() is v.row("sources")


def test_keyboard_accordion(panel, env):
    v = open_settings(panel)
    click(v.row("personalization"))
    key(v.row("palette"), Qt.Key.Key_Right)               # Right opens the list
    assert v.open_choice == "palette"
    key(v.row("palette"), Qt.Key.Key_Down)
    assert panel.focusWidget() is v.row("palette:classic")
    key(panel.focusWidget(), Qt.Key.Key_Down)
    key(panel.focusWidget(), Qt.Key.Key_Return)
    assert env.cfg.d["color_palette"] == "colorblind" and v.open_choice == "palette"
    assert panel.focusWidget() is v.row("palette:colorblind")
    key(panel.focusWidget(), Qt.Key.Key_Left)             # Left on an option folds the list
    assert v.open_choice is None and panel.focusWidget() is v.row("palette")
    key(v.row("palette"), Qt.Key.Key_Right)
    key(v.row("palette"), Qt.Key.Key_Left)                # Left on an open choice folds it, stays on the page
    assert v.open_choice is None and v.page_id == "personalization"


def test_toolbar_up_reaches_settings_and_tab_chain(panel):
    v = open_settings(panel)
    key(panel._toolbar["quit"], Qt.Key.Key_Up)
    assert panel.focusWidget() is v.row("whats_new")          # last row of the root page


def test_panel_closing_resets_to_devices(panel):
    v = open_settings(panel)
    click(v.row("personalization"))
    panel.hide()
    assert not panel.in_settings and not panel._toolbar["settings"].isChecked()
    panel.show_at(QPoint(400, 400))
    assert not panel.in_settings


def test_accessible_names_and_back_label(panel):
    v = open_settings(panel)
    assert v.back_button().accessibleName() == "Back" and v.back_button().toolTip() == "Back"
    assert v.row("sources").accessibleName() == "Sources"
    assert v.row("sources").accessibleDescription() == "Opens the page"
    click(v.row("sources"))
    assert v.row("wireless_enabled").accessibleName() == "2.4 GHz and USB"
    assert v.row("wireless_enabled").isCheckable()


def test_spanish_labels(qapp, env):
    set_language("es")
    p = dp.DevicePanel(dark=True)
    p.set_settings_model(env.model)
    p.settings_requested.connect(lambda _r: p.show_settings())
    p.show_at(QPoint(400, 400))
    try:
        v = open_settings(p)
        assert v.title_text() == "Configuración" and v.back_button().accessibleName() == "Volver"
        click(v.row("personalization"))
        assert v.row("palette").accessibleName() == "Colores, Clásica, contraído"
        click(v.row("palette"))
        assert v.row("palette").accessibleName() == "Colores, Clásica, desplegado"
    finally:
        p.close()
        set_language("en")


def test_back_keys_exist_in_both_languages():
    for key_ in ("back", "settings_collapsed", "settings_expanded", "settings_page_desc"):
        assert tr_in("en", key_) != key_ and tr_in("es", key_) != key_
    assert tr_in("en", "back") == "Back" and tr_in("es", "back") == "Volver"


def test_long_page_scrolls_within_the_cap(panel, env):
    v = open_settings(panel)
    click(v.row("personalization"))
    click(v.row("palette"))
    avail = panel._screen_avail
    assert panel.height() <= avail.height() * dp.PANEL_SIZE_FRACTION["normal"] + 1
    assert v.content_height() > 0


@pytest.mark.parametrize("dark", [True, False])
def test_themes_grab_without_errors(qapp, env, dark):
    p = dp.DevicePanel(dark=dark)
    p.set_model(build_panel_model([Device(id="a", name="Mouse", kind="mouse", battery=80, connected=True)],
                                  set(), False, 20, dark, True))
    p.set_settings_model(env.model)
    p.settings_requested.connect(lambda _r: p.show_settings())
    p.show_at(QPoint(400, 400))
    try:
        v = open_settings(p)
        click(v.row("personalization"))
        click(v.row("palette"))
        v.row("palette:pastel").setFocus(Qt.FocusReason.TabFocusReason)
        pm = p.grab()
        assert isinstance(pm, QPixmap) and not pm.isNull()
        p.set_dark(not dark)                              # live theme switch while in Settings
        assert not p.grab().isNull() and p.in_settings
    finally:
        p.close()


def test_gear_without_model_does_nothing(qapp):
    p = dp.DevicePanel(dark=True)
    p.show_at(QPoint(400, 400))
    try:
        p._toolbar["settings"].click()
        assert not p.in_settings and not p._toolbar["settings"].isChecked()
    finally:
        p.close()


def test_tray_integration_palette_custom_cancel_and_close(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget.config import Config
    from taskbar_battery_widget.custom_colors_dialog import CustomColorsDialog
    from taskbar_battery_widget.tray import TrayController
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_devices([Device(id="a", name="Mouse", kind="mouse", battery=80, connected=True)])
    t.show_settings_menu()
    p = t._panel
    v = p.settings_view
    assert p.isVisible() and p.in_settings
    click(v.row("personalization"))
    click(v.row("palette"))
    click(v.row("palette:pastel"))
    assert cfg.get("color_palette") == "pastel" and v.row("palette").value_text() == "Pastel"
    monkeypatch.setattr(CustomColorsDialog, "exec", lambda self: 0)       # cancelled dialog
    click(v.row("palette:custom"))
    assert cfg.get("color_palette") == "pastel" and v.row("palette:pastel").isChecked()
    assert not v.row("palette:custom").isChecked() and p.isVisible()
    t.set_devices([Device(id="a", name="Mouse", kind="mouse", battery=70, connected=True)])
    assert p.in_settings and v.page_id == "personalization"             # device updates do not leave Settings
    p._toolbar["settings"].click()                                       # gear: back to the devices
    assert not p.in_settings and p.isVisible()
    p.close()
