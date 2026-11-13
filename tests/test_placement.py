"""Pure placement maths: right-anchored pills next to the tray (current behaviour, no Qt widgets)."""
from taskbar_battery_widget import taskbar as tb
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.placement import Env, Placement, compute_placement


def info(**kw):
    base = dict(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080),
                edge="bottom", autohide=False, monitor_rect=(0, 0, 1920, 1080))
    base.update(kw)
    return tb.TaskbarInfo(**base)


def env(i, size=(200, 40), **kw):
    return Env(info=i, size=size, screen_name="DISPLAY1", **kw)


def run(e):
    return compute_placement(e, Placement())


def test_defaults_are_current_behaviour():
    p = Placement()
    assert (p.mode, p.anchor, p.monitor) == ("taskbar", "right", "primary")


def test_horizontal_bottom():
    r = run(env(info()))
    assert r.visible and r.reason == "ok" and not r.vertical
    assert r.rect == (1398, 1036, 200, 40)
    assert r.pill_h == 32 and r.max_extent == 640 and r.screen_name == "DISPLAY1"


def test_horizontal_top():
    r = run(env(info(rect=(0, 0, 1920, 48), tray_rect=(1602, 0, 1920, 48), edge="top")))
    assert r.visible and not r.vertical and r.rect == (1398, 4, 200, 40)


def test_vertical_right():
    i = info(rect=(1872, 0, 1920, 1080), tray_rect=(1872, 900, 1920, 1080), edge="right")
    r = run(env(i, size=(40, 100)))
    assert r.visible and r.vertical and r.rect == (1876, 796, 40, 100)
    assert r.pill_h == 32 and r.max_extent == 360


def test_vertical_left():
    i = info(rect=(0, 0, 48, 1080), tray_rect=(0, 900, 48, 1080), edge="left")
    r = run(env(i, size=(40, 100)))
    assert r.visible and r.vertical and r.rect == (4, 796, 40, 100)


def test_dpr_and_screen_origin():
    i = info(rect=(2880, 1500, 5760, 1620), tray_rect=(5000, 1500, 5760, 1620), edge="bottom",
             monitor_rect=(2880, 0, 5760, 1620))
    r = run(env(i, size=(200, 40), dpr=1.5, screen_origin=(1920, 0)))
    # physical 300x60, right edge 6 px left of the tray, centred on the tray row
    assert r.rect == (3129, 1020, 200, 40)
    assert r.pill_h == 32 and r.max_extent == max(120, int((5000 - 2880) / 1.5 * 0.4))


def test_limit_floor():
    i = info(tray_rect=(100, 1032, 1920, 1080))
    assert run(env(i)).max_extent == 120


def test_autohide_tucked_is_hidden():
    r = run(env(info(rect=(0, 1078, 1920, 1080), autohide=True)))
    assert not r.visible and r.reason == "tucked" and r.rect is None


def test_autohide_shown_is_visible():
    assert run(env(info(autohide=True))).visible


def test_fullscreen_hidden():
    r = run(env(info(), fullscreen=True))
    assert not r.visible and r.reason == "fullscreen"


def test_no_taskbar_and_no_pills():
    assert run(Env(info=None)).reason == "no_taskbar"
    assert run(env(info(), has_pills=False)).reason == "no_pills"


def test_widget_reexports_placement_helpers():
    assert w.pill_height_for(48) == 32 and w.pill_height_for(30) == 20
    assert w.center_rect_y((10, 0, 50, 30), 100) == (10, 85, 50, 115)
