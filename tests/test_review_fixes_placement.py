"""Review fixes for the 1.6.0 placement: stuck drag, keys, fullscreen, left cap, caching, UI details."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QFocusEvent, QGuiApplication, QMouseEvent

from taskbar_battery_widget import placement as pl
from taskbar_battery_widget import taskbar as tb
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import ScreenInfo

D1, D2 = "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
B = ScreenInfo("bbb", D2, (1920, -360, 1080, 1920), (1920, -360, 1080, 1872), 1.0, False)


def _info(device=D1, kind="primary"):
    return tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), kind=kind, device_name=device)


@pytest.fixture
def make(qapp, tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: False)
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: False)
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


def _mouse(kind, wd, local, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(local), QPointF(wd.pos()) + QPointF(local), button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def press(wd, local):
    wd.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, wd, local))


def move_to(wd, start_global, delta):
    g = QPointF(start_global) + QPointF(delta)
    wd.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, g - QPointF(wd.pos()), g, Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def release(wd, local):
    wd.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, wd, local, buttons=Qt.MouseButton.NoButton))


def centre(wd):
    x, y, ww, hh = wd._rects[0]
    return QPoint(int(x + ww / 2), int(y + hh / 2))


def inside(wd, avail):
    ax, ay, aw, ah = avail
    return ax <= wd.x() and ay <= wd.y() and wd.x() + wd.width() <= ax + aw and wd.y() + wd.height() <= ay + ah


def buttons(monkeypatch, value):
    monkeypatch.setattr(QGuiApplication, "mouseButtons", staticmethod(lambda: value))


# --------------------------------------------------------------------------- 1. stuck drag
def test_stuck_drag_is_cancelled_by_update_position(make, monkeypatch):
    wd, cfg = make(placement_mode="floating")
    home = wd.pos()
    c = centre(wd)
    press(wd, c)
    move_to(wd, home + c, QPoint(-300, -200))
    assert wd._dragging and wd.pos() != home
    buttons(monkeypatch, Qt.MouseButton.LeftButton)
    wd.update_position()
    assert wd._dragging                     # button really down: the drag goes on
    buttons(monkeypatch, Qt.MouseButton.NoButton)
    wd.update_position()                    # release was lost
    assert not wd._dragging and wd._press_global is None
    assert wd.pos() == home                 # back in its stored place
    assert cfg.get("floating_positions") == {}


def test_stuck_press_is_cancelled_when_button_is_up(make, monkeypatch):
    wd, _ = make(placement_mode="floating")
    press(wd, centre(wd))
    assert wd._press_global is not None
    buttons(monkeypatch, Qt.MouseButton.NoButton)
    wd.update_position()
    assert wd._press_global is None


def test_hide_and_focus_out_cancel_the_drag(make, monkeypatch):
    wd, _ = make(placement_mode="floating")
    c = centre(wd)
    press(wd, c)
    move_to(wd, wd.pos() + c, QPoint(-100, -100))
    assert wd._dragging
    wd.hide()
    assert not wd._dragging and wd._press_global is None
    wd.update_position()
    press(wd, centre(wd))
    buttons(monkeypatch, Qt.MouseButton.LeftButton)
    wd.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert wd._press_global is not None     # still pressed: kept
    buttons(monkeypatch, Qt.MouseButton.NoButton)
    wd.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert wd._press_global is None


# --------------------------------------------------------------------------- 2. key / save / override
def test_current_screen_key(make):
    wd, _ = make(placement_mode="floating", placement_monitor="bbb")
    assert wd.current_screen_key() == "bbb"
    wd._fake_screens = [P]
    wd.update_position()
    assert wd.current_screen_key() == "aaa"


def test_drop_on_primary_removes_stale_primary_entry(make):
    stale = {"primary": {"anchor": "top-right", "dx": 1, "dy": 2}}
    wd, cfg = make(placement_mode="floating", floating_positions=stale)
    assert wd.current_screen_key() == "aaa"
    c = centre(wd)
    press(wd, c)
    move_to(wd, wd.pos() + c, QPoint(-800, -700))
    release(wd, c)
    saved = cfg.get("floating_positions")
    assert list(saved) == ["aaa"] and cfg.get("placement_monitor") == "primary"


def test_failed_save_still_applies_this_session(make, monkeypatch):
    wd, cfg = make(placement_mode="floating")

    def boom(*_a, **_k):
        raise OSError("read-only")
    monkeypatch.setattr(cfg, "set", boom)
    c = centre(wd)
    press(wd, c)
    move_to(wd, wd.pos() + c, QPoint(-1700, -900))
    release(wd, c)
    assert cfg.get("floating_positions") == {}
    assert wd.x() < 100 and wd.y() < 100        # the dropped spot (top-left anchor) is still applied
    wd.update_position()
    assert wd.x() < 100 and wd.y() < 100


def test_override_ignored_after_config_changes(make, monkeypatch):
    wd, cfg = make(placement_mode="floating")
    real_set = cfg.set
    monkeypatch.setattr(cfg, "set", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    wd.set_floating_anchor("top-left")
    monkeypatch.setattr(cfg, "set", real_set)
    real_set("floating_positions", {"aaa": {"anchor": "bottom-left", "dx": 0, "dy": 0}})   # e.g. a reset
    wd.update_position()
    assert wd.y() + wd.height() == 1032         # config wins again


# --------------------------------------------------------------------------- 3. primary matching
def test_primary_taskbar_with_empty_device_name_matches_primary_screen():
    info = _info(device="")
    assert pl.taskbar_for_screen(P, [info], {D1, D2}) is info
    assert pl.taskbar_for_screen(B, [info], {D1, D2}) is None           # never for a secondary screen


def test_primary_fallback_when_device_name_matches_no_screen():
    info = _info(device="\\\\.\\DISPLAY9")
    assert pl.taskbar_for_screen(P, [info], {D1, D2}) is info
    assert pl.taskbar_for_screen(P, [_info(device=D2)], {D1, D2}) is None   # belongs to another screen
    assert pl.taskbar_for_screen(P, [_info(device="", kind="secondary")], {D1}) is None


def test_widget_docks_with_empty_device_name(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_info(device="")])
    wd, _ = make(screens=(P,))
    assert wd.isVisible() and not wd._floating and wd.x() + wd.width() == 1602 - 4


# --------------------------------------------------------------------------- 4. fullscreen semantics
def test_single_monitor_uses_global_verdict(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_info()])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: True)
    wd, _ = make(screens=(P,))
    assert not wd.isVisible() and wd._last_reason == "fullscreen"       # no rect check on a single monitor


def test_two_monitors_use_per_monitor_check(make, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_info()])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: True)
    wd, _ = make(screens=(P, B))
    assert wd.isVisible()                    # the global verdict alone does not hide it with 2 monitors
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: name == D1)
    wd.update_position()
    assert not wd.isVisible()


# --------------------------------------------------------------------------- 5. left anchor cap
def _env(**kw):
    base = dict(info=_info(), size=(300, 28), icons_centered=True, widgets_button=False,
                screen_name=D1, available=(0, 0, 1920, 1032))
    base.update(kw)
    return pl.Env(**base)


def test_left_anchor_extent_is_capped():
    res = pl.compute_placement(_env(), pl.Placement("taskbar", "left"))
    assert res.max_extent == int(1920 * 0.25 - 4) < int(1602 * pl.FREE_SPACE_SHARE)
    right = pl.compute_placement(_env(), pl.Placement("taskbar", "right"))
    assert right.max_extent == int(1602 * pl.FREE_SPACE_SHARE)         # right anchor untouched


def test_left_anchor_cap_accounts_for_widgets_button():
    res = pl.compute_placement(_env(widgets_button=True), pl.Placement("taskbar", "left"))
    assert res.max_extent == int(1920 * 0.25 - (48 + 8))


# --------------------------------------------------------------------------- 6. stable monitor id
def test_device_names_matched_by_native_origin(qapp, monkeypatch):
    g = QGuiApplication.primaryScreen().geometry()
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
    other = (g.x() + 9000, 0, g.x() + 9100, 100)
    mine = (g.x(), g.y(), g.x() + round(g.width() * dpr), g.y() + round(g.height() * dpr))
    monkeypatch.setattr(w.taskbar, "monitor_devices", lambda: [(D2, other), (D1, mine)])
    assert w._device_names([QGuiApplication.primaryScreen()]) == [D1]
    assert w.screen_infos()[0][0].name == D1
    monkeypatch.setattr(w.taskbar, "monitor_devices", lambda: [(D2, other)])
    assert w._device_names([QGuiApplication.primaryScreen()]) == [""]


@pytest.mark.live_taskbar
def test_monitor_stable_id_defaults_and_hashing(monkeypatch):
    assert tb.monitor_stable_id("") == ""

    def fake(name, idx, ref, flags):
        assert flags == tb.EDD_GET_DEVICE_INTERFACE_NAME and idx == 0
        ref._obj.DeviceID = "\\\\?\\DISPLAY#GSM5B09#5&abc&0&UID4352#{guid}"
        return 1
    monkeypatch.setattr(tb, "_user32", SimpleNamespace(EnumDisplayDevicesW=fake))
    sid = tb.monitor_stable_id(D1)
    assert len(sid) == 16 and int(sid, 16) >= 0 and "gsm" not in sid
    assert sid == tb.monitor_stable_id(D2)         # depends on the interface path only
    monkeypatch.setattr(tb, "_user32", SimpleNamespace(EnumDisplayDevicesW=lambda *a: 0))
    assert tb.monitor_stable_id(D1) == ""
    monkeypatch.setattr(tb, "_user32", SimpleNamespace(EnumDisplayDevicesW=lambda *a: (_ for _ in ()).throw(OSError())))
    assert tb.monitor_stable_id(D1) == ""


def test_monitor_key_uses_stable_id_only_without_qt_identity():
    a = tb.monitor_key("", "", "", D1, [], "abc123")
    b = tb.monitor_key("", "", "", D2, [], "abc123")
    assert a == b                                     # DISPLAYn renumbering no longer changes the key
    assert a != tb.monitor_key("", "", "", D1, [], "")
    assert tb.monitor_key("DEL", "M", "S", D1, [], "abc123") == tb.monitor_key("DEL", "M", "S", D2, [], "zzz")
    assert "abc123" not in a


@pytest.mark.live_taskbar
def test_monitor_devices_live_shape():
    for name, rect in tb.monitor_devices():
        assert name.startswith("\\\\.\\DISPLAY") and len(rect) == 4


@pytest.mark.live_taskbar
def test_monitor_stable_id_live_shape():
    sid = tb.monitor_stable_id(D1)
    assert sid == "" or (len(sid) == 16 and all(c in "0123456789abcdef" for c in sid))
    assert tb.monitor_stable_id("\\\\.\\DISPLAY99") == ""


# --------------------------------------------------------------------------- 7. caching
def test_screen_infos_cached_until_screen_signal(qapp, monkeypatch):
    calls = []
    real = w.screen_infos
    monkeypatch.setattr(w, "screen_infos", lambda: calls.append(1) or real())
    wd = w.TaskbarWidget(None)
    try:
        for _ in range(3):
            wd._screens()
        assert len(calls) == 1
        wd._on_screens_changed()
        wd._screens()
        assert len(calls) == 2
    finally:
        wd.deleteLater()


def test_registry_reads_cached_with_ttl(make, monkeypatch):
    calls = []
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_info()])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: calls.append(1) or True)
    wd, _ = make(taskbar_anchor="left")
    n = len(calls)
    assert n >= 1
    for _ in range(5):
        wd._last_geom = None
        wd.update_position()
    assert len(calls) == n
    key = next(iter(wd._reg_cache))
    wd._reg_cache[key] = (wd._reg_cache[key][0] - w.REG_CACHE_TTL - 1, wd._reg_cache[key][1])
    wd.update_position()
    assert len(calls) == n + 1


def test_screen_removed_cleans_watched(make, qapp):
    wd, _ = make(placement_mode="floating")
    sc = QGuiApplication.primaryScreen()
    assert any(sc == s for s in wd._watched)
    qapp.screenRemoved.emit(sc)
    assert not any(sc == s for s in wd._watched)
    qapp.screenAdded.emit(sc)
    assert any(sc == s for s in wd._watched) and len(wd._watched) == len({id(s) for s in wd._watched})


# --------------------------------------------------------------------------- 8. lookup_floating
@pytest.mark.parametrize("entry", [{"anchor": "top-left", "dx": "x", "dy": 1},
                                   {"anchor": "top-left", "dx": None, "dy": 1},
                                   {"anchor": "top-left", "dx": 1, "dy": [1]},
                                   {"anchor": "top-left", "dx": float("inf"), "dy": 0},
                                   {"anchor": "top-left", "dx": float("nan"), "dy": 0}])
def test_lookup_floating_bad_numbers(entry):
    assert pl.lookup_floating({"k": entry}, "k") == pl.DEFAULT_FLOATING
    assert pl.lookup_floating("nonsense", "k") == pl.DEFAULT_FLOATING
    assert pl.lookup_floating({"k": {"anchor": "top-left", "dx": "7", "dy": 2.9}}, "k") == ("top-left", 7, 2)


# --------------------------------------------------------------------------- 9. UI details
def test_floating_uses_move_cursor(make, monkeypatch):
    wd, cfg = make(placement_mode="floating")
    assert wd.cursor().shape() == Qt.CursorShape.SizeAllCursor
    cfg.set("placement_mode", "taskbar")
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [_info()])
    wd.update_position()
    assert not wd._floating and wd.cursor().shape() != Qt.CursorShape.SizeAllCursor


def _with_overflow(wd):
    wd._pills = list(wd._all_pills[:1]) + [w._Pill("overflow", "+2", "", members=wd._all_pills[1:])]
    wd._rects = [(2, 2, 40, 24), (46, 2, 30, 24)]


def test_overflow_pill_opens_on_release_in_floating(make):
    wd, _ = make(placement_mode="floating")
    _with_overflow(wd)
    got = []
    wd.panel_requested.connect(got.append)
    press(wd, QPoint(50, 10))
    assert got == []
    release(wd, QPoint(50, 10))
    assert len(got) == 1 and isinstance(got[0], QRect)


def test_overflow_pill_drag_does_not_open_panel(make):
    wd, _ = make(placement_mode="floating")
    _with_overflow(wd)
    got = []
    wd.panel_requested.connect(got.append)
    press(wd, QPoint(50, 10))
    move_to(wd, wd.pos() + QPoint(50, 10), QPoint(-300, -300))
    assert wd._dragging
    release(wd, QPoint(50, 10))
    assert got == [] and not wd._dragging


def test_floating_backdrop_alpha_and_paint(make):
    assert w.FLOATING_BG_ALPHA == 245
    wd, _ = make(placement_mode="floating")
    img = wd.grab().toImage()
    mid = img.height() // 2
    assert img.pixelColor(img.width() // 2, 0).alpha() > 0 or img.pixelColor(img.width() // 2, 1).alpha() > 0
    assert img.pixelColor(1, mid).alpha() > 0


def test_drag_is_clamped_to_monitor_under_cursor(make):
    wd, _ = make(placement_mode="floating")
    c = centre(wd)
    press(wd, c)
    move_to(wd, wd.pos() + c, QPoint(-5000, -5000))
    assert wd._dragging and inside(wd, P.available)
    move_to(wd, wd.pos() + c, QPoint(5000, 5000))
    assert inside(wd, P.available) or inside(wd, B.available)


def test_drag_threshold_follows_style_hints(make):
    wd, _ = make(placement_mode="floating")
    thr = max(4, QGuiApplication.styleHints().startDragDistance())
    assert wd._drag_threshold() == thr
    c = centre(wd)
    press(wd, c)
    move_to(wd, wd.pos() + c, QPoint(thr, 0))
    assert not wd._dragging
    move_to(wd, wd.pos() + c, QPoint(thr + 2, 0))
    assert wd._dragging


# --------------------------------------------------------------------------- 10. keyboard anchor
def test_set_floating_anchor_saves_and_repositions(make):
    wd, cfg = make(placement_mode="floating")
    wd.set_floating_anchor("top-left")
    assert cfg.get("floating_positions") == {"aaa": {"anchor": "top-left", "dx": 0, "dy": 0}}
    assert (wd.x(), wd.y()) == (0, 0)
    wd.set_floating_anchor("middle-right")
    assert cfg.get("floating_positions")["aaa"]["anchor"] == "middle-right"
    assert wd.x() + wd.width() == 1920
    wd.set_floating_anchor("bogus")
    assert cfg.get("floating_positions")["aaa"]["anchor"] == "middle-right"


def test_set_floating_anchor_uses_current_monitor(make):
    wd, cfg = make(placement_mode="floating", placement_monitor="bbb")
    wd.set_floating_anchor("top-right")
    assert list(cfg.get("floating_positions")) == ["bbb"]
    assert (wd.x() + wd.width(), wd.y()) == (1920 + 1080, -360)
