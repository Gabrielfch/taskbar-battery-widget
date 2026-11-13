"""TaskbarWidget placement: floating drag, monitor selection, hot-plug, fullscreen (offscreen Qt)."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent

from taskbar_battery_widget import taskbar as tb
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import ScreenInfo

D1, D2 = "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
B = ScreenInfo("bbb", D2, (1920, -360, 1080, 1920), (1920, -360, 1080, 1872), 1.0, False)


@pytest.fixture
def make(qapp, tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: False)
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: True)

    def _make(screens=(P, B), **cfg_values):
        cfg = Config(tmp_path / "config.json")
        for k, v in cfg_values.items():
            cfg.set(k, v)
        real = QGuiApplication.primaryScreen()
        wd = w.TaskbarWidget(cfg)
        wd._screens = lambda: [(s, real) for s in wd._fake_screens]
        wd._fake_screens = list(screens)
        wd.set_devices([Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 50, True)])
        made.append(wd)
        return wd, cfg

    yield _make
    for wd in made:
        wd.stop()
        wd.deleteLater()


def mouse(kind, wd, local, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    glob = QPointF(wd.pos()) + QPointF(local)
    return QMouseEvent(kind, QPointF(local), glob, button, buttons, Qt.KeyboardModifier.NoModifier)


def press(wd, local):
    wd.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, wd, local))


def move_to(wd, start_global, delta):
    g = QPointF(start_global) + QPointF(delta)
    wd.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, g - QPointF(wd.pos()), g,
                                  Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                  Qt.KeyboardModifier.NoModifier))


def release(wd, delta):
    g = QPointF(wd._press_global) + QPointF(delta) if wd._press_global is not None else QPointF(wd.pos())
    wd.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, g - QPointF(wd.pos()), g,
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                     Qt.KeyboardModifier.NoModifier))


def pill_centre(wd):
    x, y, ww, hh = wd._rects[0]
    return QPoint(int(x + ww / 2), int(y + hh / 2))


def drag(wd, delta):
    local = pill_centre(wd)
    start = wd.pos() + local
    press(wd, local)
    move_to(wd, start, delta)
    release(wd, delta)


def inside(wd, avail):
    ax, ay, aw, ah = avail
    return ax <= wd.x() and ay <= wd.y() and wd.x() + wd.width() <= ax + aw and wd.y() + wd.height() <= ay + ah


# --------------------------------------------------------------------------- floating placement
def test_floating_default_bottom_right_of_primary(make):
    wd, _ = make(placement_mode="floating")
    assert wd.isVisible() and wd._floating
    assert (wd.x() + wd.width(), wd.y() + wd.height()) == (1920 - 12, 1032 - 12)
    assert wd.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert wd.windowFlags() & Qt.WindowType.Tool and wd.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_floating_uses_stored_position_and_monitor(make):
    pos = {"bbb": {"anchor": "top-left", "dx": 20, "dy": 30}}
    wd, _ = make(placement_mode="floating", placement_monitor="bbb", floating_positions=pos)
    assert (wd.x(), wd.y()) == (1920 + 20, -360 + 30)


def test_floating_visible_without_any_taskbar(make):
    wd, _ = make(placement_mode="floating")
    assert tb.get_taskbar_infos() == [] and wd.isVisible()


def test_default_config_is_taskbar_right(make, monkeypatch):
    info = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [info])
    wd, _ = make()
    assert wd.isVisible() and not wd._floating
    assert wd.x() + wd.width() == 1602 - 4 and wd.y() == 1056 - wd.height() // 2


def test_taskbar_matched_by_name_on_selected_monitor(make, monkeypatch):
    prim = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    sec = tb.TaskbarInfo(rect=(1920, 1512, 3000, 1560), tray_rect=(2850, 1512, 3000, 1560), edge="bottom",
                         autohide=False, monitor_rect=(1920, -360, 3000, 1560), kind="secondary",
                         has_tray=False, device_name=D2)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [prim, sec])
    wd, _ = make(placement_monitor="bbb")
    assert wd.x() + wd.width() == 2850 - 4 and wd._cur_screen.key == "bbb"
    assert wd.y() >= 1500


def _prim():
    return tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)


def test_taskbar_missing_on_chosen_monitor_floats_bottom_right(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_prim()])
    wd, cfg = make(placement_monitor="bbb")
    assert wd.isVisible() and wd._floating and wd._last_reason == "no_taskbar_floating"
    assert (wd.x() + wd.width(), wd.y() + wd.height()) == (1920 + 1080 - 12, -360 + 1872 - 12)
    assert cfg.get("placement_mode") == "taskbar"


def test_taskbar_missing_uses_saved_floating_position(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_prim()])
    pos = {"bbb": {"anchor": "top-left", "dx": 20, "dy": 30}}
    wd, _ = make(placement_monitor="bbb", floating_positions=pos)
    assert (wd.x(), wd.y()) == (1920 + 20, -360 + 30) and wd._last_reason == "no_taskbar_floating"


def test_no_primary_taskbar_info_still_hides(make):
    wd, _ = make(placement_monitor="bbb")      # get_taskbar_infos() == [] (explorer restarting)
    assert not wd.isVisible() and wd._last_reason == "no_taskbar"


def test_drag_in_no_taskbar_fallback_saves_for_that_monitor_only(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_prim()])
    wd, cfg = make(placement_monitor="bbb")
    drag(wd, QPoint(-500, -900))
    saved = cfg.get("floating_positions")
    assert list(saved) == ["bbb"] and saved["bbb"]["anchor"].endswith("left")
    assert cfg.get("placement_mode") == "taskbar" and cfg.get("placement_monitor") == "bbb"
    assert inside(wd, B.available) and wd._last_reason == "no_taskbar_floating"


def test_drag_in_fallback_cannot_leave_monitor_or_change_choice(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_prim()])
    wd, cfg = make(placement_monitor="bbb")
    drag(wd, QPoint(-1500, 0))                   # centre would land on the primary monitor
    assert cfg.get("placement_monitor") == "bbb" and list(cfg.get("floating_positions")) == ["bbb"]
    assert inside(wd, B.available)


def test_widgets_button_margin_applied_on_win11(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_prim()])
    monkeypatch.setattr(w.taskbar, "taskbar_widgets_button", lambda: True)
    wd, _ = make(taskbar_anchor="left")
    assert wd.x() >= 48 + 8 and wd._last_reason == "ok"


def test_left_anchor_fallback_emits_warning_once(make, monkeypatch):
    info = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [info])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: False)
    wd, _ = make(taskbar_anchor="left")
    got = []
    wd.placement_warning.connect(got.append)
    wd._last_reason = "ok"
    wd.update_position()
    wd.update_position()
    assert got == ["left_needs_centered_icons"]
    assert wd.x() + wd.width() == 1602 - 4               # fell back to the right end


def test_left_anchor_when_centered(make, monkeypatch):
    info = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [info])
    wd, _ = make(taskbar_anchor="left")
    assert wd.x() == 4 and wd._last_reason == "ok"


# --------------------------------------------------------------------------- drag
def test_small_move_stays_a_click(make):
    wd, cfg = make(placement_mode="floating")
    before = wd.pos()
    drag(wd, QPoint(3, 0))
    assert wd.pos() == before and not wd._dragging
    assert cfg.get("floating_positions") == {}
    assert cfg.get("placement_monitor") == "primary"


def test_click_still_shows_popup_in_floating_mode(make):
    wd, _ = make(placement_mode="floating")
    press(wd, pill_centre(wd))
    assert wd._hover == 0
    release(wd, QPoint(0, 0))
    assert wd._hover == 0 and not wd._dragging


def test_drag_moves_snaps_and_saves(make):
    wd, cfg = make(placement_mode="floating")
    drag(wd, QPoint(-1700, -900))              # towards the top-left corner of the primary monitor
    saved = cfg.get("floating_positions")
    assert list(saved) == ["aaa"]
    assert saved["aaa"]["anchor"] == "top-left"
    assert (wd.x(), wd.y()) == (max(0, saved["aaa"]["dx"]), max(0, saved["aaa"]["dy"]))
    assert inside(wd, P.available)
    assert cfg.get("placement_monitor") == "primary"        # dropped on the primary: choice untouched


def test_drag_onto_other_monitor_saves_under_its_key(make):
    wd, cfg = make(placement_mode="floating")
    drag(wd, QPoint(600, -300))                  # centre lands on the secondary monitor
    assert "bbb" in cfg.get("floating_positions")
    assert cfg.get("placement_monitor") == "bbb"
    assert wd.x() >= 1920 and inside(wd, B.available)


def test_drag_is_ignored_in_taskbar_mode(make, monkeypatch):
    info = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [info])
    wd, cfg = make()
    before = wd.pos()
    drag(wd, QPoint(-400, -400))
    assert wd.pos() == before and cfg.get("floating_positions") == {}


def test_right_click_still_requests_menu(make):
    wd, _ = make(placement_mode="floating")
    got = []
    wd.context_menu_requested.connect(got.append)
    wd.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, wd, QPoint(5, 5), Qt.MouseButton.RightButton,
                             Qt.MouseButton.RightButton))
    assert len(got) == 1


# --------------------------------------------------------------------------- hot-plug
def test_screen_removed_falls_back_to_primary_keeping_choice(make, qapp):
    pos = {"bbb": {"anchor": "top-left", "dx": 20, "dy": 30}}
    wd, cfg = make(placement_mode="floating", placement_monitor="bbb", floating_positions=pos)
    assert wd.x() >= 1920
    wd._fake_screens = [P]
    qapp.screenRemoved.emit(QGuiApplication.primaryScreen())
    assert wd._cur_screen.key == "aaa" and inside(wd, P.available)
    assert cfg.get("placement_monitor") == "bbb"            # stored choice is not overwritten
    assert cfg.get("floating_positions") == pos
    wd._fake_screens = [P, B]
    qapp.screenAdded.emit(QGuiApplication.primaryScreen())
    assert (wd.x(), wd.y()) == (1920 + 20, -360 + 30)        # monitor is back: the choice applies again


def test_primary_changed_and_geometry_changed_reposition(make, qapp):
    wd, _ = make(placement_mode="floating")
    wd._fake_screens = [ScreenInfo("aaa", D1, (0, 0, 1280, 720), (0, 0, 1280, 672), 1.0, True)]
    qapp.primaryScreenChanged.emit(QGuiApplication.primaryScreen())
    assert (wd.x() + wd.width(), wd.y() + wd.height()) == (1280 - 12, 672 - 12)
    wd._fake_screens = [ScreenInfo("aaa", D1, (0, 0, 1024, 600), (0, 0, 1024, 552), 1.0, True)]
    QGuiApplication.primaryScreen().geometryChanged.emit(QGuiApplication.primaryScreen().geometry())
    assert (wd.x() + wd.width(), wd.y() + wd.height()) == (1024 - 12, 552 - 12)


# --------------------------------------------------------------------------- fullscreen
def test_fullscreen_hides_unless_flag(make, monkeypatch):
    wd, cfg = make(placement_mode="floating")
    assert wd.isVisible()
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: name == D1)
    wd.update_position()
    assert not wd.isVisible()
    cfg.set("show_in_fullscreen", True)
    wd.update_position()
    assert wd.isVisible()
    cfg.set("show_in_fullscreen", False)
    wd.update_position()
    assert not wd.isVisible()


def test_fullscreen_is_per_monitor(make, monkeypatch):
    wd, _ = make(placement_mode="floating")
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: name == D2)
    wd.update_position()
    assert wd.isVisible()                                   # the fullscreen app is on the other monitor


# --------------------------------------------------------------------------- helpers
def test_screen_keys_unique_and_stable(qapp):
    pairs = w.screen_keys()
    assert pairs and len({k for k, _ in pairs}) == len(pairs)
    assert [k for k, _ in w.screen_keys()] == [k for k, _ in pairs]
    k, sc = pairs[0]
    assert k == tb.monitor_key(sc.manufacturer(), sc.model(), sc.serialNumber(), sc.name(), [])


def test_screen_infos_flags_primary(qapp):
    infos = w.screen_infos()
    assert sum(1 for i, _ in infos if i.primary) == 1
    assert all(i.name == q.name() for i, q in infos)
