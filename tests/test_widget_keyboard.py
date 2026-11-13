"""Keyboard access, accessible names and the foreground-menu helper of the taskbar widget."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu

from taskbar_battery_widget import widget as w
from taskbar_battery_widget import win_window
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _en(monkeypatch):
    set_language("en")
    monkeypatch.setattr(w.taskbar, "get_taskbar_info", lambda: None)
    yield


class Cfg:
    def get(self, k):
        return {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False,
                "hidden_devices": [], "color_mode": "levels"}[k]


def _key(widget, key, mods=Qt.KeyboardModifier.NoModifier):
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def _widget():
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "Alpha", "keyboard", 80, True), Device("b", "Beta", "mouse", 10, True)])
    wd.show()
    return wd


def test_arrows_move_focus_and_set_accessible_name(qapp):
    wd = _widget()
    assert wd.accessibleName() == "Taskbar Battery Widget"
    _key(wd, Qt.Key.Key_Right)
    assert wd._kfocus == 0 and "Alpha" in wd.accessibleName()
    _key(wd, Qt.Key.Key_Right)
    assert wd._kfocus == 1 and "Beta" in wd.accessibleName()
    _key(wd, Qt.Key.Key_Right)
    assert wd._kfocus == 1                      # clamped
    _key(wd, Qt.Key.Key_Left)
    assert wd._kfocus == 0
    wd._set_kfocus(-1)
    assert wd.accessibleName() == "Taskbar Battery Widget"
    wd.hide()


def test_enter_and_space_show_card(qapp):
    wd = _widget()
    wd._set_kfocus(1)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        wd._hide_popup()
        _key(wd, key)
        assert wd._popup is not None and wd._popup.isVisible()
    _key(wd, Qt.Key.Key_Escape)
    assert not wd._popup.isVisible()
    wd.hide()


def test_menu_key_and_shift_f10_open_menu(qapp, monkeypatch):
    wd = _widget()
    opened = []
    monkeypatch.setattr(w, "popup_menu_foreground", lambda m, pos, hwnd=0: opened.append(pos))
    wd.set_menu(QMenu())
    wd._set_kfocus(0)
    _key(wd, Qt.Key.Key_Menu)
    _key(wd, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    _key(wd, Qt.Key.Key_F10)                    # without Shift: nothing
    assert len(opened) == 2
    wd.hide()


def test_tab_walks_pills_then_leaves(qapp):
    wd = _widget()
    wd._set_kfocus(0)
    assert wd.focusNextPrevChild(True) and wd._kfocus == 1
    assert wd.focusNextPrevChild(False) and wd._kfocus == 0
    wd.hide()


def test_focus_clamped_when_pills_shrink(qapp):
    wd = _widget()
    wd._set_kfocus(1)
    wd.set_devices([Device("a", "Alpha", "keyboard", 80, True)])
    assert wd._kfocus == 0
    wd.set_devices([])
    assert wd._kfocus == -1
    wd.hide()


# ---- foreground-menu helper ------------------------------------------------------------
class FakeU32:
    def __init__(self):
        self.calls = []

    def SetForegroundWindow(self, h):
        self.calls.append(("fg", h.value))

    def PostMessageW(self, h, msg, wp, lp):
        self.calls.append(("post", h.value, msg, wp, lp))


@pytest.mark.skipif(not w.sys.platform == "win32", reason="win32 only")
def test_popup_menu_foreground_sets_foreground_then_posts_wm_null(qapp, monkeypatch):
    fake = FakeU32()
    monkeypatch.setattr(win_window, "_u32", fake)
    menu = QMenu()
    menu.addAction("x")
    w.popup_menu_foreground(menu, qapp.primaryScreen().geometry().center(), owner_hwnd=1234)
    mh = int(menu.winId())
    assert fake.calls[0] == ("fg", mh)
    menu.hide()
    assert ("post", 1234, 0, 0, 0) in fake.calls
    n = len(fake.calls)
    menu.popup(qapp.primaryScreen().geometry().center())
    menu.hide()
    assert len(fake.calls) == n                 # the one-shot hook was disconnected


@pytest.mark.skipif(not w.sys.platform == "win32", reason="win32 only")
def test_helpers_ignore_null_hwnd(monkeypatch):
    fake = FakeU32()
    monkeypatch.setattr(win_window, "_u32", fake)
    w._foreground(0)
    w._post_null(0)
    assert fake.calls == []
