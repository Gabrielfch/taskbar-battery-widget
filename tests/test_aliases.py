import json

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.aliases import apply_aliases, set_alias
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.detail_log import DetailLogger
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.icons import pencil_icon
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController

KEY = visibility_key("dev-1")


def setup_function(_):
    set_language("en")


def dev(i="dev-1", name="Mouse", battery=50, connected=True, **kw):
    return Device(id=i, name=name, kind="mouse", battery=battery, connected=connected, **kw)


# ---------------------------------------------------------------- config
def test_config_default_and_roundtrip(tmp_path):
    c = Config(tmp_path / "c.json")
    assert c.get("device_aliases") == {}
    c.set("device_aliases", {KEY: "Work mouse"})
    assert Config(tmp_path / "c.json").get("device_aliases") == {KEY: "Work mouse"}


def test_config_load_sanitises(tmp_path):
    k2 = visibility_key("b")
    raw = {"device_aliases": {
        "not-a-key": "x", KEY: "A\x00B\x07C<b>" + "z" * 100, k2: "", visibility_key("c"): 5}}
    (tmp_path / "c.json").write_text(json.dumps(raw), encoding="utf-8")
    al = Config(tmp_path / "c.json").get("device_aliases")
    assert list(al) == [KEY]
    assert len(al[KEY]) <= 48 and "\x00" not in al[KEY] and "<" not in al[KEY] and al[KEY].startswith("ABC")


def test_config_load_caps_entries(tmp_path):
    raw = {"device_aliases": {visibility_key(str(i)): f"n{i}" for i in range(300)}}
    (tmp_path / "c.json").write_text(json.dumps(raw), encoding="utf-8")
    assert len(Config(tmp_path / "c.json").get("device_aliases")) == 200


def test_config_set_sanitises_and_rejects(tmp_path):
    c = Config(tmp_path / "c.json")
    c.set("device_aliases", {KEY: "x" * 80, "bad": "y"})
    assert c.get("device_aliases") == {KEY: "x" * 48}
    with pytest.raises(ValueError):
        c.set("device_aliases", {visibility_key(str(i)): "a" for i in range(201)})
    with pytest.raises(ValueError):
        c.set("device_aliases", ["nope"])


def test_set_alias_failure_logs_no_alias_values(tmp_path, caplog):
    c = Config(tmp_path / "c.json")
    secret = "SecretAliasName"
    for i in range(200):
        c._data["device_aliases"][visibility_key(str(i))] = secret
    with caplog.at_level("DEBUG"):
        assert set_alias(c, visibility_key("new"), "x") is False
    assert caplog.records
    assert secret not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    with pytest.raises(ValueError) as e:
        c.set("device_aliases", {visibility_key(str(i)): secret for i in range(201)})
    assert secret not in str(e.value)


# ---------------------------------------------------------------- application
def test_apply_and_remove(tmp_path):
    c = Config(tmp_path / "c.json")
    assert set_alias(c, KEY, "  Desk  mouse ")
    out = apply_aliases([dev(), dev("other", "Keys")], c.get("device_aliases"))
    assert out[0].name == "Desk mouse" and out[0].original_name == "Mouse"
    assert out[1].name == "Keys" and out[1].original_name == ""
    assert set_alias(c, KEY, "")
    assert c.get("device_aliases") == {}
    assert apply_aliases([dev()], c.get("device_aliases"))[0].name == "Mouse"
    assert not set_alias(c, "nope", "x")


def test_apply_is_idempotent_and_does_not_mutate():
    d = dev()
    once = apply_aliases([d], {KEY: "A"})
    twice = apply_aliases(once, {KEY: "A"})
    assert twice[0].name == "A" and twice[0].original_name == "Mouse" and d.name == "Mouse"


def test_alerts_use_alias(tmp_path):
    c = Config(tmp_path / "c.json")
    [d] = apply_aliases([dev(battery=10)], {KEY: "Desk mouse"})
    [(_t, body)] = AlertManager(c).check([d], 0)
    assert "Desk mouse" in body


def test_detail_log_path_unchanged_by_alias(tmp_path):
    lg = DetailLogger(tmp_path / "logs" / "devices")
    d = dev()
    [aliased] = apply_aliases([d], {KEY: "Totally different"})
    assert lg.path_for(aliased) == lg.path_for(d)


def test_tray_alias_signal_saves_and_refreshes(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    seen = []
    tray.ui_settings_changed.connect(lambda: seen.append(1))
    tray._on_alias(KEY, "Desk")
    assert cfg.get("device_aliases") == {KEY: "Desk"} and seen
    tray._on_alias(KEY, "")
    assert cfg.get("device_aliases") == {}


def test_pencil_icon_draws(qapp):
    img = pencil_icon(18, QColor("white")).pixmap(18, 18).toImage()
    assert any(img.pixelColor(x, y).alpha() for x in range(18) for y in range(18))


# ---------------------------------------------------------------- panel
def model(devs):
    return build_panel_model(devs, (), True, 20, True, lang="en")


@pytest.fixture
def panel(qapp):
    p = dp.DevicePanel(dark=True)
    yield p
    p.close()
    p.deleteLater()


def pencils(p):
    return [b for b in p.findChildren(QToolButton) if b.accessibleName().startswith("Set an alias for")]


def test_pencil_per_row_and_before_eye(panel):
    panel.set_model(model([dev("a", "A"), dev("b", "B", connected=False)]))
    assert len(pencils(panel)) == 2
    for row in (r for r in panel._rows if len(r) > 1):
        names = [b.accessibleName() for b in row]
        assert names[-1].startswith("Show ") and names[-2].startswith("Set an alias for")
    assert pencils(panel)[0].toolTip() == "Alias"


def test_alias_row_tooltip_and_edit_tip(panel):
    [d] = apply_aliases([dev()], {KEY: "Desk"})
    panel.set_model(model([d]))
    assert pencils(panel)[0].toolTip() == "Edit alias"
    lab = next(lb for lb in panel.findChildren(QLabel) if lb.text() == "Desk")
    assert "Original name: Mouse" in lab.toolTip()


def test_keyboard_reaches_pencil_and_tab_order(panel):
    panel.set_model(model([dev()]))
    panel.show_at(QPoint(300, 300))
    eye = next(b for b in panel.findChildren(QToolButton) if b.accessibleName().startswith("Show Mouse"))
    pen = pencils(panel)[0]
    QTest.keyClick(eye, Qt.Key.Key_Left)
    assert panel.focusWidget() is pen
    order = list(panel._buttons.values())
    assert order.index(pen) + 1 == order.index(eye)


def start(panel):
    panel.set_model(model([dev()]))
    panel.show_at(QPoint(300, 300))
    pencils(panel)[0].click()
    edit = panel.findChild(QLineEdit)
    assert edit is not None and panel._edit is edit
    return edit


def test_edit_enter_saves(panel):
    got = []
    panel.alias_changed.connect(lambda k, a: got.append((k, a)))
    edit = start(panel)
    assert edit.placeholderText() == "Mouse"
    edit.setText("Desk")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert got == [(KEY, "Desk")] and panel._edit is None and panel.isVisible()


def test_edit_escape_cancels_and_keeps_panel(panel):
    got = []
    panel.alias_changed.connect(lambda k, a: got.append((k, a)))
    edit = start(panel)
    edit.setText("Desk")
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert got == [] and panel._edit is None and panel.isVisible()


def test_edit_focus_loss_saves(panel):
    got = []
    panel.alias_changed.connect(lambda k, a: got.append((k, a)))
    edit = start(panel)
    edit.setText("Desk")
    QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
    assert got == [(KEY, "Desk")]


def test_empty_removes_alias_and_unchanged_is_silent(panel):
    got = []
    panel.alias_changed.connect(lambda k, a: got.append((k, a)))
    [d] = apply_aliases([dev()], {KEY: "Desk"})
    panel.set_model(model([d]))
    panel.show_at(QPoint(300, 300))
    pencils(panel)[0].click()
    edit = panel.findChild(QLineEdit)
    assert edit.text() == "Desk" and edit.placeholderText() == "Mouse"
    QTest.keyClick(edit, Qt.Key.Key_Return)             # unchanged
    assert got == []
    pencils(panel)[0].click()
    edit = panel._edit
    edit.setText("")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert got == [(KEY, "")]


def test_model_updates_deferred_while_editing(panel):
    edit = start(panel)
    panel.set_model(model([dev(battery=40)]))           # a scan arrives mid-edit
    assert panel.findChild(QLineEdit) is edit
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert panel._edit is None and panel.findChild(QLineEdit) is None
    assert any("40%" in lb.text() for lb in panel.findChildren(QLabel))
