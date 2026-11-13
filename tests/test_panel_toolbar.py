"""Panel toolbar (refresh / show all / settings / about / quit), state tooltips and tray wiring."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import tray as tray_mod
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.icons import (about_icon, gear_icon, power_icon, refresh_icon,
                                          show_all_icon)
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController

ORDER = ["refresh", "show_all", "settings", "about", "quit"]
LABELS = {"en": ["Refresh", "Show all in the taskbar", "Settings", "About", "Quit"],
          "es": ["Actualizar", "Mostrar todos en la barra", "Configuración", "Acerca de", "Salir"]}


def setup_function(_):
    set_language("en")


def dev(i, name=None, battery=50, connected=True):
    return Device(id=i, name=name or f"Dev {i}", kind="mouse", battery=battery, connected=connected)


def model(devs, hidden=(), show_disc=True, lang="en"):
    return build_panel_model(devs, hidden, show_disc, 20, True, lang=lang)


@pytest.fixture
def panel(qapp):
    p = dp.DevicePanel(dark=True, show_logging=True)
    yield p
    p.close()
    p.deleteLater()


@pytest.mark.parametrize("lang", ["en", "es"])
def test_toolbar_buttons_icons_tooltips_names(qapp, lang):
    set_language(lang)
    p = dp.DevicePanel(dark=True)
    p.set_model(model([dev("a")], lang=lang))
    assert list(p._toolbar) == ORDER
    for key, label in zip(ORDER, LABELS[lang]):
        b = p._toolbar[key]
        assert (b.width(), b.height()) == (32, 32)
        assert not b.icon().isNull()
        assert b.toolTip() == label and b.accessibleName() == label
        assert b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    p.close()


def test_toolbar_actions_emit(panel):
    got = []
    panel.refresh_requested.connect(lambda: got.append("refresh"))
    panel.show_all_requested.connect(lambda: got.append("show_all"))
    panel.about_requested.connect(lambda: got.append("about"))
    panel.quit_requested.connect(lambda: got.append("quit"))
    panel.settings_requested.connect(lambda r: got.append(("settings", r.size().width())))
    panel.set_model(model([dev("a")], hidden=[visibility_key("a")]))
    for k in ORDER:
        panel._toolbar[k].click()
    assert got == ["refresh", "show_all", ("settings", 32), "about", "quit"]


def test_show_all_disabled_when_nothing_hidden(panel):
    panel.set_model(model([dev("a")]))
    assert not panel._toolbar["show_all"].isEnabled()
    panel.set_model(model([dev("a")], hidden=[visibility_key("a")]))
    assert panel._toolbar["show_all"].isEnabled()


def test_keyboard_toolbar(panel):
    got = []
    panel.refresh_requested.connect(lambda: got.append("refresh"))
    panel.set_model(model([dev("a")], hidden=[visibility_key("a")]))
    panel.show_at(QPoint(300, 300))
    t = panel._toolbar
    t["refresh"].setFocus()
    QTest.keyClick(t["refresh"], Qt.Key.Key_Return)         # Enter activates
    QTest.keyClick(t["refresh"], Qt.Key.Key_Space)          # Space activates
    assert got == ["refresh", "refresh"]
    QTest.keyClick(t["refresh"], Qt.Key.Key_Right)
    assert t["show_all"].hasFocus()
    QTest.keyClick(t["show_all"], Qt.Key.Key_Right)
    assert t["settings"].hasFocus()
    QTest.keyClick(t["settings"], Qt.Key.Key_Right)
    assert t["about"].hasFocus()
    QTest.keyClick(t["about"], Qt.Key.Key_Left)
    assert t["settings"].hasFocus()
    QTest.keyClick(t["settings"], Qt.Key.Key_Up)             # back to the device rows
    assert panel._nav[-1].hasFocus()


def test_keyboard_skips_disabled_show_all(panel):
    panel.set_model(model([dev("a")]))
    panel.show_at(QPoint(300, 300))
    panel._toolbar["refresh"].setFocus()
    QTest.keyClick(panel._toolbar["refresh"], Qt.Key.Key_Right)
    assert panel._toolbar["settings"].hasFocus()


def test_state_tooltips_update_after_toggle(panel):
    panel.set_model(model([dev("a", "Mouse"), dev("c", "Gone", connected=False)]))
    btns = panel.findChildren(QToolButton)
    eye = next(b for b in btns if b.accessibleName() == "Show Mouse in the taskbar")
    assert eye.toolTip() == "Hide Mouse from the taskbar"
    eye.click()
    assert eye.toolTip() == "Show Mouse in the taskbar"
    rec = next(b for b in btns if b.accessibleName() == "Record Mouse to the detailed log")
    assert rec.toolTip() == "Start per-minute recording of Mouse"
    rec.click()
    assert rec.toolTip() == "Stop per-minute recording of Mouse"
    known = next(b for b in btns if b.accessibleName() == "Show known devices in the taskbar")
    assert known.toolTip() == "Hide known devices from the taskbar"
    known.click()
    assert known.toolTip() == "Show known devices in the taskbar"


def test_open_log_tooltip_and_spanish(qapp):
    set_language("es")
    p = dp.DevicePanel(dark=True, show_logging=True)
    p.set_model(model([dev("a", "Raton"), dev("c", "Viejo", connected=False)], lang="es"),
                logging_keys=[visibility_key("a")])
    tips = {b.toolTip() for b in p.findChildren(QToolButton)}
    assert {"Ocultar Raton de la barra", "Detener el registro por minuto de Raton",
            "Abrir el registro de Raton", "Ocultar conocidos de la barra"} <= tips
    p.close()


def test_icons_render_1x_and_2x(qapp):
    for make in (refresh_icon, show_all_icon, gear_icon, about_icon, power_icon):
        ic = make(18, QColor(255, 255, 255))
        for side in (18, 36):
            pm = ic.pixmap(side, side)
            assert isinstance(pm, QPixmap) and not pm.isNull()
            img = pm.toImage()
            assert any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))


# ---------------------------------------------------------------- tray wiring
def make_tray(tmp_path):
    t = TrayController(Config(tmp_path / "c.json"))
    t.set_devices([Device("a", "A", "mouse", 80, True)])
    return t


def test_tray_has_no_context_menu(qapp, tmp_path):
    t = make_tray(tmp_path)
    assert t._tray.contextMenu() is None


def test_tray_toolbar_actions(qapp, tmp_path, monkeypatch):
    t = make_tray(tmp_path)
    t.open_panel()
    p = t._panel
    got = []
    t.refresh_requested.connect(lambda: got.append("refresh"))
    t.quit_requested.connect(lambda: got.append("quit"))
    monkeypatch.setattr(t, "show_about", lambda: got.append("about"))
    p._toolbar["refresh"].click()
    assert got == ["refresh"] and p.isVisible()             # refresh keeps the panel open
    p._toolbar["about"].click()
    assert got[-1] == "about" and not p.isVisible()
    t.open_panel()
    p._toolbar["quit"].click()
    assert got[-1] == "quit" and not p.isVisible()


def test_tray_gear_shows_settings_menu_and_closes_panel(qapp, tmp_path, monkeypatch):
    set_language("es")
    t = make_tray(tmp_path)
    shown = []
    monkeypatch.setattr(tray_mod, "popup_menu_foreground", lambda m, pos, *a: shown.append((m, pos)))
    t.open_panel()
    t._panel._toolbar["settings"].click()
    assert not t._panel.isVisible()
    assert len(shown) == 1 and shown[0][0] is t._settings.menu
    texts = [a.text() for a in shown[0][0].actions() if not a.isSeparator()]
    assert texts == ["Fuentes", "Alertas", "Registros", "Iniciar con Windows", "Personalización"]


def test_settings_menu_really_pops_up(qapp, tmp_path):
    t = make_tray(tmp_path)
    t.open_panel()
    t._panel._toolbar["settings"].click()
    menu = t._settings.menu
    try:
        assert menu.isVisible()
    finally:
        menu.hide()
