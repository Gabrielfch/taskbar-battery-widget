"""Different position over a fullscreen app: pure choice, widget transitions/drag, config, settings model."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent

from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import FLOATING_ANCHORS, FULLSCREEN_PLACEMENTS, Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import (DEFAULT_FLOATING, Env, Placement, ScreenInfo, choose_placement,
                                              compute_placement)
from taskbar_battery_widget.settings_model import SettingsCallbacks, build_settings_model

D1 = "\\\\.\\DISPLAY1"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
NORMAL = Placement("floating", "right", "primary", "top-left", 10, 20)


def env(fs=True, show=True):
    return Env(info=None, fullscreen=fs, show_in_fullscreen=show, screen_name=D1, size=(200, 28),
               available=P.available)


# --------------------------------------------------------------------------- pure
def test_same_keeps_placement():
    assert choose_placement(env(), NORMAL, "same", {}, "aaa") is NORMAL


@pytest.mark.parametrize("fs,show", [(False, True), (True, False), (False, False)])
def test_no_fullscreen_or_not_visible_keeps_placement(fs, show):
    assert choose_placement(env(fs, show), NORMAL, "top-right", {}, "aaa") is NORMAL
    assert choose_placement(env(fs, show), NORMAL, "floating", {}, "aaa") is NORMAL


def test_anchor_floats_on_that_anchor():
    for a in FLOATING_ANCHORS:
        p = choose_placement(env(), Placement("taskbar", "left", "primary"), a, {}, "aaa")
        assert (p.mode, p.float_anchor, p.dx, p.dy) == ("floating", a, 0, 0)
        assert p.monitor == "primary"


def test_floating_uses_stored_or_default():
    stored = {"aaa": {"anchor": "bottom-left", "dx": 5, "dy": -6}}
    p = choose_placement(env(), NORMAL, "floating", stored, "aaa")
    assert (p.mode, p.float_anchor, p.dx, p.dy) == ("floating", "bottom-left", 5, -6)
    p = choose_placement(env(), NORMAL, "floating", {}, "aaa")
    assert (p.float_anchor, p.dx, p.dy) == DEFAULT_FLOATING
    p = choose_placement(env(), NORMAL, "floating", stored, "other")        # other monitor: default
    assert (p.float_anchor, p.dx, p.dy) == DEFAULT_FLOATING


def test_unknown_choice_is_ignored_and_result_is_visible_passive():
    assert choose_placement(env(), NORMAL, "bogus", {}, "aaa") is NORMAL
    e = env()
    res = compute_placement(e, choose_placement(e, NORMAL, "top-right", {}, "aaa"))
    assert res.visible and res.fullscreen_passive and res.rect[1] == 0 and res.rect[0] + res.rect[2] == 1920


# --------------------------------------------------------------------------- config
def test_config_validates(tmp_path):
    c = Config(tmp_path / "c.json")
    assert c.get("fullscreen_placement") == "same" and c.get("fullscreen_floating_positions") == {}
    for v in FULLSCREEN_PLACEMENTS:
        c.set("fullscreen_placement", v)
        assert c.get("fullscreen_placement") == v
    for bad in ("top", "", 3, None):
        with pytest.raises(ValueError):
            c.set("fullscreen_placement", bad)
    c.set("fullscreen_floating_positions", {"aaa": {"anchor": "top-left", "dx": 99999, "dy": -3}})
    assert c.get("fullscreen_floating_positions") == {"aaa": {"anchor": "top-left", "dx": 10000, "dy": -3}}
    with pytest.raises(ValueError):
        c.set("fullscreen_floating_positions", {"aaa": {"anchor": "nope", "dx": 0, "dy": 0}})
    with pytest.raises(ValueError):
        c.set("fullscreen_floating_positions", {f"{i:x}": {"anchor": "top-left", "dx": 0, "dy": 0}
                                                 for i in range(17)})


def test_config_load_drops_bad(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"fullscreen_placement": "bogus", "fullscreen_floating_positions": '
                    '{"aaa": {"anchor": "top-left", "dx": 1, "dy": 2}, "bbb": {"anchor": "x"}}}', encoding="utf-8")
    c = Config(path)
    assert c.get("fullscreen_placement") == "same"
    assert c.get("fullscreen_floating_positions") == {"aaa": {"anchor": "top-left", "dx": 1, "dy": 2}}


# --------------------------------------------------------------------------- widget
@pytest.fixture
def ctx(qapp, tmp_path, monkeypatch):
    st = {"fs": False, "shift": False}
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: st["fs"])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: st["fs"])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: True)
    monkeypatch.setattr(w, "set_click_through", lambda hwnd, on: True)
    monkeypatch.setattr(w, "shift_down", lambda: st["shift"])
    cfg = Config(tmp_path / "config.json")
    cfg.set("placement_mode", "floating")
    cfg.set("show_in_fullscreen", True)
    cfg.set("floating_positions", {"aaa": {"anchor": "bottom-right", "dx": -12, "dy": -12}})
    real = QGuiApplication.primaryScreen()
    wd = w.TaskbarWidget(cfg)
    wd._screens = lambda: [(P, real)]
    wd.set_devices([Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 50, True)])
    st["wd"], st["cfg"] = wd, cfg
    yield st
    wd.stop()
    wd.deleteLater()


def test_transition_normal_fullscreen_normal(ctx):
    wd, cfg = ctx["wd"], ctx["cfg"]
    wd.update_position()
    normal = wd.pos()
    assert normal.x() > 1000 and normal.y() > 900 and not wd._passive      # bottom-right
    cfg.set("fullscreen_placement", "top-left")
    wd.update_position()
    assert wd.pos() == normal                                               # no fullscreen yet
    ctx["fs"] = True
    wd.update_position()
    assert (wd.x(), wd.y()) == (0, 0) and wd._passive and wd._fs_override
    ctx["fs"] = False
    wd.update_position()
    assert wd.pos() == normal and not wd._passive and not wd._fs_override


def test_same_does_not_move_in_fullscreen(ctx):
    wd = ctx["wd"]
    wd.update_position()
    normal = wd.pos()
    ctx["fs"] = True
    wd.update_position()
    assert wd.pos() == normal and wd._passive and not wd._fs_override


def test_works_from_taskbar_mode_and_needs_show_in_fullscreen(ctx, monkeypatch):
    from taskbar_battery_widget import taskbar as tb
    info = tb.TaskbarInfo(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080), edge="bottom",
                          autohide=False, monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [info])
    wd, cfg = ctx["wd"], ctx["cfg"]
    cfg.set("placement_mode", "taskbar")
    cfg.set("fullscreen_placement", "top-center")
    wd.update_position()
    on_bar = wd.pos()
    assert on_bar.y() >= 1032 - 20
    ctx["fs"] = True
    wd.update_position()
    assert wd.y() == 0 and wd._fs_override and abs(wd.x() + wd.width() // 2 - 960) <= 1
    ctx["fs"] = False
    wd.update_position()
    assert wd.pos() == on_bar
    cfg.set("show_in_fullscreen", False)
    ctx["fs"] = True
    wd.update_position()
    assert not wd.isVisible() and not wd._fs_override


def _drag(wd, delta):
    x, y, ww, hh = wd._rects[0]
    local = QPoint(int(x + ww / 2), int(y + hh / 2))
    start = QPointF(wd.pos()) + QPointF(local)

    def ev(kind, g, buttons, button=Qt.MouseButton.LeftButton):
        return QMouseEvent(kind, g - QPointF(wd.pos()), g, button, buttons, Qt.KeyboardModifier.NoModifier)
    wd.mousePressEvent(ev(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton))
    wd.mouseMoveEvent(ev(QEvent.Type.MouseMove, start + QPointF(delta), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.NoButton))
    wd.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, start + QPointF(delta), Qt.MouseButton.NoButton))


def test_drag_in_fullscreen_saves_only_fullscreen_position(ctx):
    wd, cfg = ctx["wd"], ctx["cfg"]
    normal_pos = cfg.get("floating_positions")
    cfg.set("fullscreen_placement", "bottom-left")
    ctx["fs"] = True
    wd.update_position()
    assert wd._passive
    ctx["shift"] = True
    wd._shift_held = True               # Shift makes the passive widget interactive
    _drag(wd, QPoint(300, -400))
    saved = cfg.get("fullscreen_floating_positions")
    assert list(saved) == ["aaa"] and saved["aaa"]["anchor"] in FLOATING_ANCHORS
    assert cfg.get("floating_positions") == normal_pos
    assert cfg.get("fullscreen_placement") == "floating"
    assert cfg.get("placement_monitor") == "primary"
    spot = wd.pos()
    wd._shift_held = False
    ctx["fs"] = False
    wd.update_position()
    assert wd.pos() != spot and wd.x() > 1000                  # back at the normal bottom-right spot
    ctx["fs"] = True
    wd.update_position()
    assert wd.pos() == spot and wd._passive                     # remembered, still click-through


def test_passive_without_shift_ignores_drag(ctx):
    wd, cfg = ctx["wd"], ctx["cfg"]
    cfg.set("fullscreen_placement", "top-left")
    ctx["fs"] = True
    wd.update_position()
    _drag(wd, QPoint(300, 300))
    assert cfg.get("fullscreen_floating_positions") == {} and (wd.x(), wd.y()) == (0, 0)


# --------------------------------------------------------------------------- settings model
class Cfg:
    def __init__(self, **kw):
        self.d = {"show_in_fullscreen": False, "fullscreen_placement": "same", "placement_mode": "taskbar",
                  "taskbar_anchor": "right", "placement_monitor": "primary", "floating_positions": {}}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


def _model(**kw):
    set_language("en")
    cfg, calls = Cfg(**kw), []
    cb = SettingsCallbacks(set_bool=lambda *a: None, set_threshold=lambda v: None, set_palette=lambda v: None,
                           set_startup=lambda v: None, open_device_log=lambda: None,
                           open_logs_folder=lambda: None, startup_enabled=lambda: False,
                           set_fullscreen_placement=lambda v: calls.append(v))
    return cfg, calls, build_settings_model(cfg, cb)


def test_settings_option_present_disabled_without_show_in_fullscreen():
    cfg, calls, m = _model()
    ch = m.find("fullscreen_placement")
    opts = ch.options()
    assert [o.value for o in opts] == ["same", "floating", *FLOATING_ANCHORS]
    assert not any(o.enabled for o in opts) and all(o.tip for o in opts)
    cfg.d["show_in_fullscreen"] = True
    opts = ch.options()
    assert all(o.enabled and not o.tip for o in opts)
    assert ch.current_label() == "Same as usual"
    ch.set("top-right")
    assert calls == ["top-right"]


def test_reset_enabled_with_fullscreen_floating():
    cfg, _calls, m = _model()
    assert not m.find("reset_position").enabled()
    cfg.d["fullscreen_placement"] = "floating"
    assert m.find("reset_position").enabled()
