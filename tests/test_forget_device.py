"""Forgetting a known device from the panel (button, confirmation, cleanup of history / config / logs)."""
import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QToolButton

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget import device_history as dh
from taskbar_battery_widget import i18n
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.detail_log import DetailLogger
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.icons import trash_icon
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.stats_window import StatsWindows
from taskbar_battery_widget.tray import TrayController

KEY = visibility_key("known-1")
OTHER = visibility_key("other-2")


def setup_function(_):
    set_language("en")


def dev(i, name, connected):
    return Device(id=i, name=name, kind="keyboard", battery=70, connected=connected, link="2.4ghz")


def forget_buttons(p):
    return [b for b in p.findChildren(QToolButton) if b.accessibleName().startswith("Forget ")]


@pytest.fixture
def ctx(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "fedcba9876543210.png").write_bytes(b"x")
    (tmp_path / "icons" / "0123456789abcdef.png").write_bytes(b"x")
    cfg = Config(tmp_path / "c.json")
    cfg.set("device_aliases", {KEY: "Desk", OTHER: "Other"})
    cfg.set("custom_icons_device", {KEY: "0123456789abcdef.png", OTHER: "fedcba9876543210.png"})
    cfg.set("hidden_devices", [KEY, OTHER])
    cfg.set("detail_log_devices", [KEY, OTHER])
    cfg.set("detail_log_enabled", True)
    cfg.set("show_disconnected", True)
    t = TrayController(cfg)
    t.set_devices([dev("known-1", "Keys", False), dev("live-3", "Mouse", True)])
    p = t._ensure_panel()
    p.show_at(QPoint(300, 300))
    t._refresh_panel()
    yield cfg, t, p, tmp_path
    p.close()


def test_button_only_on_known_rows(ctx):
    _cfg, _t, p, _ = ctx
    [b] = forget_buttons(p)
    assert b.accessibleName() == "Forget Keys" and b.toolTip() == "Forget"
    assert len(p.findChildren(QToolButton)) > 1
    from PySide6.QtGui import QColor
    assert not trash_icon(18, QColor("white")).pixmap(18, 18).isNull()


def test_keyboard_reaches_the_button(ctx):
    _cfg, _t, p, _ = ctx
    [b] = forget_buttons(p)
    row = next(r for r in p._rows if b in r)
    assert row[-2] is b                       # right before the eye
    eye = row[-1]
    eye.setFocus()
    QTest.keyClick(eye, Qt.Key.Key_Left)
    assert p.focusWidget() is b


def test_cancel_changes_nothing(ctx, monkeypatch):
    cfg, t, p, _ = ctx
    monkeypatch.setattr(p, "_confirm_forget", lambda name, has_log: (False, True))
    got = []
    t.device_forgotten.connect(lambda k, w: got.append(k))
    forget_buttons(p)[0].click()
    assert not got
    assert KEY in cfg.get("device_aliases") and KEY in cfg.get("hidden_devices")


def test_confirm_cleans_config_and_keeps_the_panel(ctx, monkeypatch):
    cfg, t, p, tmp = ctx
    monkeypatch.setattr(p, "_confirm_forget", lambda name, has_log: (True, False))
    got, ui = [], []
    t.device_forgotten.connect(lambda k, w: got.append((k, w)))
    t.ui_settings_changed.connect(lambda: ui.append(1))
    forget_buttons(p)[0].click()
    assert got == [(KEY, False)] and ui
    assert cfg.get("device_aliases") == {OTHER: "Other"}
    assert cfg.get("custom_icons_device") == {OTHER: "fedcba9876543210.png"}
    assert cfg.get("hidden_devices") == [OTHER]
    assert cfg.get("detail_log_devices") == [OTHER] and cfg.get("detail_log_enabled") is True
    assert not (tmp / "icons" / "0123456789abcdef.png").exists() and (tmp / "icons" / "fedcba9876543210.png").exists()
    assert p.isVisible()


def test_last_recorded_device_switches_the_global_log_off(ctx):
    cfg, t, _p, _ = ctx
    cfg.set("detail_log_devices", [KEY])
    t._on_forget(KEY, False)
    assert cfg.get("detail_log_devices") == [] and cfg.get("detail_log_enabled") is False


def test_connected_device_is_not_forgotten(ctx):
    cfg, t, _p, _ = ctx
    t.set_devices([dev("known-1", "Keys", True)])
    got = []
    t.device_forgotten.connect(lambda k, w: got.append(k))
    t._on_forget(KEY, True)
    assert not got and KEY in cfg.get("device_aliases")


def test_dialog_offers_the_log_option_only_when_a_log_exists(ctx, monkeypatch):
    _cfg, t, p, _ = ctx
    seen = []
    monkeypatch.setattr(p, "_confirm_forget", lambda name, has_log: seen.append(has_log) or (False, False))
    t.set_detail_files_fn(lambda k: k == KEY)
    forget_buttons(p)[0].click()
    t.set_detail_files_fn(None)
    forget_buttons(p)[0].click()
    assert seen == [True, False]


def test_real_dialog_defaults(ctx, monkeypatch):
    _cfg, _t, p, _ = ctx
    info = {}

    def fake_exec(self):
        info["default"] = self.defaultButton().text()
        info["check"] = self.checkBox().isChecked() if self.checkBox() else None
        info["text"] = self.text()
        info["title"] = self.windowTitle()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert p._confirm_forget("Keys", True) == (False, False)
    assert info == {"default": "Cancel", "check": False, "title": "Forget device",
                    "text": i18n.tr("forget_text", name="Keys")}
    p._confirm_forget("Keys", False)
    assert info["check"] is None


# ------------------------------------------------------------------ history
def test_history_forget_removes_and_saves(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev("known-1", "Keys", True)], 1_000_000.0)
    h.flush()
    assert KEY in h._known
    assert h.forget(KEY) is True and KEY not in h._known
    data = json.loads(dh.last_known_path(tmp_path).read_text(encoding="utf-8"))
    assert KEY not in data["devices"]
    assert h.forget(KEY) is False and h.forget(None) is False
    h.close()


# ------------------------------------------------------------------ detail log files
def test_files_for_key_and_delete_touch_only_that_device(tmp_path):
    lg = DetailLogger(tmp_path / "logs" / "devices")
    d, o = dev("known-1", "Keys", False), dev("other-2", "Other", False)
    lg.base.mkdir(parents=True)
    main = lg.path_for(d)
    paths = [main, main.with_name(main.name + ".1"), main.with_name(main.name + ".2")]
    for f in paths + [lg.path_for(o)]:
        f.write_text("x", encoding="utf-8")
    (lg.base / "notes.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "logs" / "app.log").write_text("keep", encoding="utf-8")
    assert lg.files_for_key(KEY) == sorted(paths)
    assert lg.delete_for_key(KEY) == 3
    assert lg.files_for_key(KEY) == []
    assert lg.path_for(o).exists() and (lg.base / "notes.txt").exists() and (tmp_path / "logs" / "app.log").exists()
    assert lg.files_for_key("") == [] and lg.delete_for_key("../x") == 0


def test_stats_windows_close_key(qapp, tmp_path):
    sw = StatsWindows()
    w = sw.open("k", "Keys", tmp_path / "x.csv")
    sw.close_key("k")
    assert not w.isVisible() and "k" not in sw._windows
    sw.close_key("missing")


# ------------------------------------------------------------------ i18n
def test_new_strings_exist_in_every_language():
    keys = ("panel_forget", "panel_tip_forget", "forget_title", "forget_text", "forget_log", "forget_btn",
            "forget_cancel", "animations_always", "animations_system", "animations_off")
    for lang in i18n.LANGUAGES:
        for k in keys:
            assert i18n.tr_in(lang, k, name="N") != k, (lang, k)
        assert "{name}" not in i18n.tr_in(lang, "forget_text", name="N")
