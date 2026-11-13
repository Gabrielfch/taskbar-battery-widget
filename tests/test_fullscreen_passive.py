"""Fullscreen passive mode: click-through unless Shift is held (offscreen Qt, Win32 mocked)."""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent

from taskbar_battery_widget import widget as w
from taskbar_battery_widget import win_window as ww
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import Env, Placement, ScreenInfo, compute_placement

D1 = "\\\\.\\DISPLAY1"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    state = {"fs": False, "shift": False, "ct": []}
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: state["fs"])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: state["fs"])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: True)
    monkeypatch.setattr(w, "set_click_through", lambda hwnd, on: state["ct"].append(bool(on)) or True)
    monkeypatch.setattr(w, "shift_down", lambda: state["shift"])
    cfg = Config(tmp_path / "config.json")
    cfg.set("placement_mode", "floating")
    cfg.set("show_in_fullscreen", True)
    real = QGuiApplication.primaryScreen()
    wd = w.TaskbarWidget(cfg)
    wd._screens = lambda: [(P, real)]
    wd.set_devices([Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 50, True)])
    state["wd"], state["cfg"] = wd, cfg
    yield state
    wd.stop()
    wd.deleteLater()


def _press(wd, local):
    glob = QPointF(wd.pos()) + QPointF(local)
    wd.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local), glob,
                                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier))


def _enter_fullscreen(env):
    env["fs"] = True
    env["wd"].update_position()


def test_normal_state_is_interactive(env):
    wd = env["wd"]
    wd.update_position()
    assert wd.isVisible() and not wd._passive and not wd._shift_timer.isActive()
    assert env["ct"] == []


def test_entering_fullscreen_goes_passive(env):
    wd = env["wd"]
    wd.update_position()
    _enter_fullscreen(env)
    assert wd.isVisible() and wd._passive
    assert env["ct"][-1] is True
    assert wd._shift_timer.isActive()


def test_passive_blocks_popup_and_press(env):
    wd = env["wd"]
    wd.update_position()
    _enter_fullscreen(env)
    wd._hover = 0
    wd._show_popup()
    assert wd._popup is None or not wd._popup.isVisible()
    rx, ry, rw, rh = wd._rects[0]
    _press(wd, QPointF(rx + 2, ry + 2))
    assert wd._press_global is None
    assert wd._popup is None or not wd._popup.isVisible()


def test_shift_enables_and_releases(env):
    wd = env["wd"]
    wd.update_position()
    _enter_fullscreen(env)
    env["shift"] = True
    wd._shift_tick()
    assert env["ct"][-1] is False and wd._interactive()
    wd._hover = 0
    wd._show_popup()
    assert wd._popup is not None and wd._popup.isVisible()
    rx, ry, rw, rh = wd._rects[0]
    _press(wd, QPointF(rx + 2, ry + 2))
    assert wd._press_global is not None             # normal click handling is back
    env["shift"] = False
    wd._shift_tick()
    assert env["ct"][-1] is True and not wd._interactive()
    assert not wd._popup.isVisible()
    assert wd._press_global is None and wd._hover == -1


def test_leaving_fullscreen_restores(env):
    wd = env["wd"]
    wd.update_position()
    _enter_fullscreen(env)
    env["shift"] = True
    wd._shift_tick()
    env["fs"] = False
    wd.update_position()
    assert not wd._passive and not wd._shift_held
    assert env["ct"][-1] is False
    assert not wd._shift_timer.isActive()
    assert wd._interactive()


def test_entering_passive_cancels_drag(env):
    wd = env["wd"]
    wd.update_position()
    wd._press_global, wd._dragging = QPointF(1, 1).toPoint(), True
    wd._left_button_down = lambda: True
    wd._set_passive(True)
    assert wd._press_global is None and not wd._dragging


def test_flag_off_still_hides(env):
    wd = env["wd"]
    wd.update_position()
    env["cfg"].set("show_in_fullscreen", False)
    _enter_fullscreen(env)
    assert not wd.isVisible() and not wd._passive
    assert not wd._shift_timer.isActive()


def test_hide_while_passive_resets(env):
    wd = env["wd"]
    wd.update_position()
    _enter_fullscreen(env)
    env["cfg"].set("show_in_fullscreen", False)
    wd.update_position()
    assert not wd.isVisible() and not wd._passive and not wd._shift_timer.isActive()
    assert env["ct"][-1] is False


def test_placement_result_flag():
    e = Env(info=None, fullscreen=True, size=(100, 30), available=(0, 0, 1000, 800), show_in_fullscreen=True)
    r = compute_placement(e, Placement(mode="floating"))
    assert r.visible and r.fullscreen_passive
    r = compute_placement(Env(info=None, fullscreen=False, size=(100, 30), available=(0, 0, 1000, 800),
                              show_in_fullscreen=True), Placement(mode="floating"))
    assert r.visible and not r.fullscreen_passive
    r = compute_placement(Env(info=None, fullscreen=True, size=(100, 30), available=(0, 0, 1000, 800)),
                          Placement(mode="floating"))
    assert not r.visible and r.reason == "fullscreen" and not r.fullscreen_passive


def test_win_helpers_safe_off_windows(monkeypatch):
    monkeypatch.setattr(ww.sys, "platform", "linux")
    assert ww.set_click_through(123, True) is False
    assert ww.shift_down() is False
    assert ww.set_click_through(0, True) is False
