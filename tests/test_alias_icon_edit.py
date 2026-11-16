"""Alias editing in the device panel: the row icon becomes a picker (and a remove cross) while editing."""
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox, QToolButton

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import i18n
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController

KEY = visibility_key("dev-1")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    ci.set_maps({}, {})
    i18n.set_language("en")
    yield
    ci.set_maps({}, {})
    ci.clear_cache()


def make_png(tmp_path, name="red", color="red"):
    img = QImage(32, 32, QImage.Format.Format_ARGB32)
    img.fill(QColor(color))
    src = tmp_path / f"{name}.png"
    assert img.save(str(src))
    return src


def model():
    return build_panel_model([Device(id="dev-1", name="Mouse", kind="mouse", battery=50, connected=True)],
                             (), True, 20, True, lang="en")


@pytest.fixture
def panel(qapp):
    p = dp.DevicePanel(dark=True)
    p.set_model(model())
    p.show_at(QPoint(300, 300))
    yield p
    p.close()
    p.deleteLater()


def start(p):
    p._buttons[(KEY, "alias")].click()
    assert p._edit is not None
    return p._edit


def by_name(p, name):
    return next((b for b in p.findChildren(QToolButton) if b.accessibleName() == name), None)


def focus_out(edit, reason):
    QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, reason))


def test_icon_not_clickable_until_editing(panel):
    assert by_name(panel, "Change icon of Mouse") is None
    assert panel._edit_icon is None
    start(panel)
    icon = by_name(panel, "Change icon of Mouse")
    assert icon is not None and icon is panel._edit_icon
    assert icon.toolTip() == "Change icon"
    assert icon.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert icon.focusPolicy() == Qt.FocusPolicy.TabFocus
    panel._finish_edit(False)
    QTest.qWait(20)
    assert panel._edit_icon is None and by_name(panel, "Change icon of Mouse") is None


def test_cross_only_with_own_icon(panel):
    start(panel)
    assert not panel._edit_clear.isVisible()
    panel._finish_edit(False)
    ci.set_maps({"mouse": "0123456789abcdef.png"}, {})      # a kind icon is not "own"
    start(panel)
    assert not panel._edit_clear.isVisible()
    panel._finish_edit(False)
    ci.set_maps({}, {KEY: "0123456789abcdef.png"})
    start(panel)
    clear = panel._edit_clear
    assert clear.isVisible() and clear.toolTip() == "Remove icon"
    assert clear.accessibleName() == "Remove icon of Mouse"


def test_signals_and_editor_survive(panel):
    edit = start(panel)
    edit.setText("My mouse")
    got = []
    panel.icon_change_requested.connect(lambda k: (got.append(k), focus_out(edit, Qt.FocusReason.ActiveWindowFocusReason)))
    panel._edit_icon.click()
    assert got == [KEY]
    assert panel._edit is edit and edit.text() == "My mouse"
    assert edit.isVisible()
    assert panel.isVisible()


def test_popup_focus_out_does_not_save(panel):
    edit = start(panel)
    edit.setText("typed")
    saved = []
    panel.alias_changed.connect(lambda k, a: saved.append((k, a)))
    focus_out(edit, Qt.FocusReason.PopupFocusReason)
    assert panel._edit is edit and not saved
    focus_out(edit, Qt.FocusReason.MouseFocusReason)
    assert panel._edit is None and saved == [(KEY, "typed")]


def test_hide_with_open_editor_saves_and_unblocks(panel):
    edit = start(panel)
    edit.setText("hidden")
    saved = []
    panel.alias_changed.connect(lambda k, a: saved.append((k, a)))
    panel.hide()
    assert panel._edit is None and saved == [(KEY, "hidden")]
    m = build_panel_model([Device(id="dev-1", name="Mouse", kind="mouse", battery=80, connected=True)],
                          (), True, 20, True, lang="en")
    panel.set_model(m)
    assert panel._model is m


def test_keyboard_reaches_icon_and_cross(panel):
    ci.set_maps({}, {KEY: "0123456789abcdef.png"})
    start(panel)
    icon, clear, pen = panel._edit_icon, panel._edit_clear, panel._buttons[(KEY, "alias")]
    row = panel._rows[panel._row_of(pen)]
    assert row.index(icon) + 1 == row.index(clear) and row.index(clear) + 1 == row.index(pen)
    panel._move_in_row(pen, -1)
    assert clear.hasFocus() or panel._edit is not None
    got = []
    panel.icon_change_requested.connect(got.append)
    QTest.keyClick(icon, Qt.Key.Key_Return)
    assert got == [KEY]
    panel.icon_reset_requested.connect(got.append)
    QTest.keyClick(clear, Qt.Key.Key_Return)
    assert got == [KEY, KEY]
    QTest.keyClick(clear, Qt.Key.Key_Escape)
    assert panel._edit is None and panel.isVisible()


def test_tab_to_icon_keeps_editing_and_leaving_ends_it(panel, monkeypatch):
    edit = start(panel)
    icon = panel._edit_icon
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: icon))
    focus_out(edit, Qt.FocusReason.TabFocusReason)
    QTest.qWait(20)
    assert panel._edit is edit
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: panel._buttons[(KEY, "alias")]))
    focus_out(icon, Qt.FocusReason.TabFocusReason)
    QTest.qWait(20)
    assert panel._edit is None


def tray_with_panel(tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_devices([Device(id="dev-1", name="Mouse", kind="mouse", battery=50, connected=True)])
    p = t._ensure_panel()
    p.show_at(QPoint(300, 300))
    t._refresh_panel()
    return cfg, t, p


def test_pick_image_saves_device_icon(tmp_path, monkeypatch):
    cfg, t, p = tray_with_panel(tmp_path)
    src = make_png(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(src), "")))
    ui = []
    t.ui_settings_changed.connect(lambda: ui.append(1))
    edit = start(p)
    edit.setText("keep me")
    p._edit_icon.click()
    name = cfg.get("custom_icons_device")[KEY]
    assert ci.safe_name(name) and (ci.icons_dir() / name).exists()
    assert ci.file_for("mouse", KEY) == name and ui
    assert p._edit is edit and edit.text() == "keep me" and p.isVisible()
    assert p._edit_clear.isVisible()


def test_cancelled_dialog_changes_nothing(tmp_path, monkeypatch):
    cfg, t, p = tray_with_panel(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    start(p)
    p._edit_icon.click()
    assert cfg.get("custom_icons_device") == {} and p._edit is not None


def test_import_error_shows_message_and_panel_stays(tmp_path, monkeypatch):
    cfg, t, p = tray_with_panel(tmp_path)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    edit = start(p)
    edit.setText("still here")
    p._edit_icon.click()
    assert shown == [i18n.tr("icon_err_unreadable")]
    assert cfg.get("custom_icons_device") == {}
    assert p.isVisible() and p._edit is edit and edit.text() == "still here"


def test_cross_removes_own_icon(tmp_path, monkeypatch):
    cfg, t, p = tray_with_panel(tmp_path)
    src = make_png(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(src), "")))
    start(p)
    p._edit_icon.click()
    name = cfg.get("custom_icons_device")[KEY]
    assert p._edit_clear.isVisible()
    p._edit_clear.click()
    assert cfg.get("custom_icons_device") == {}
    assert ci.device_file(KEY) is None
    assert not p._edit_clear.isVisible()
    assert not (ci.icons_dir() / name).exists()     # orphan cleaned
    assert p._edit is not None


def test_reset_falls_back_to_kind_icon(tmp_path):
    cfg, t, p = tray_with_panel(tmp_path)
    kind_name = "aaaaaaaaaaaaaaaa.png"
    cfg.set("custom_icons_kind", {"mouse": kind_name})
    cfg.set("custom_icons_device", {KEY: "bbbbbbbbbbbbbbbb.png"})
    t._appearance_changed()
    start(p)
    assert p._edit_clear.isVisible()
    p._edit_clear.click()
    assert ci.file_for("mouse", KEY) == kind_name


def test_new_keys_in_every_language():
    for lang, table in i18n._TABLES.items():
        for k in ("panel_tip_icon_change", "panel_tip_icon_reset", "panel_icon_change", "panel_icon_reset"):
            assert table[k].strip(), (lang, k)
        assert "{name}" in table["panel_icon_change"] and "{name}" in table["panel_icon_reset"]
