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


# --------------------------------------------------------------------------- T33: monitors, left, floating
import pytest

from taskbar_battery_widget import placement as pl
from taskbar_battery_widget.placement import ScreenInfo

D1, D2, D3 = "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2", "\\\\.\\DISPLAY3"
PRIMARY = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
SECOND = ScreenInfo("bbb", D2, (1920, -360, 1080, 1920), (1920, -360, 1080, 1872), 1.0, False)
HIDPI = ScreenInfo("ccc", D3, (-1280, 100, 1280, 720), (-1280, 100, 1280, 680), 1.5, False)


def sec_info(**kw):
    base = dict(rect=(1920, 1512, 3000, 1560), tray_rect=(2850, 1512, 3000, 1560), edge="bottom",
                autohide=False, monitor_rect=(1920, -360, 3000, 1560), kind="secondary",
                has_tray=False, device_name=D2)
    base.update(kw)
    return tb.TaskbarInfo(**base)


def prim_info(**kw):
    return info(kind="primary", device_name=D1, **kw)


def test_resolve_screen_by_key_and_fallback():
    screens = [PRIMARY, SECOND]
    assert pl.resolve_screen("bbb", screens) is SECOND
    assert pl.resolve_screen("primary", screens) is PRIMARY
    assert pl.resolve_screen("gone", screens) is PRIMARY        # missing monitor -> primary
    assert pl.resolve_screen("bbb", [SECOND]) is SECOND
    assert pl.resolve_screen("gone", [SECOND]) is SECOND        # no flagged primary -> first
    assert pl.resolve_screen("x", []) is None


def test_taskbar_matched_by_device_name_not_size():
    a, b = prim_info(), sec_info()
    infos = [a, b]
    assert pl.taskbar_for_screen(SECOND, infos) is b
    assert pl.taskbar_for_screen(PRIMARY, infos) is a
    assert pl.taskbar_for_screen(HIDPI, infos) is None
    assert pl.taskbar_for_screen(None, infos) is None
    # same size as the primary but another name: still told apart
    twin = ScreenInfo("ddd", "\\\\.\\DISPLAY9", PRIMARY.geometry, PRIMARY.available)
    assert pl.taskbar_for_screen(twin, infos) is None


def test_secondary_taskbar_right_of_fallback_tray():
    e = Env(info=sec_info(), size=(200, 40), screen_name=D2, screen_origin=(1920, -360))
    r = compute_placement(e, Placement(monitor="bbb"))
    assert r.visible and r.reason == "ok" and not r.vertical
    assert r.rect == (2646, 1516, 200, 40)
    assert r.rect[0] + r.rect[2] <= 2850                 # just left of the fallback tray rect
    assert r.screen_name == D2


def test_secondary_on_dpr_screen_with_negative_origin():
    i = sec_info(rect=(-1920, 1032, 0, 1080), tray_rect=(-150, 1032, 0, 1080),
                 monitor_rect=(-1920, 0, 0, 1080), device_name=D3)
    e = Env(info=i, size=(200, 40), screen_name=D3, screen_origin=(-1280, 100), dpr=1.5)
    r = compute_placement(e, Placement())
    # physical 300x60: right edge 6 px left of the tray, centred on the bar; logical origin (-1280, 100)
    assert r.rect == (-1280 + round((-150 - 6 - 300 + 1920) / 1.5), 100 + round(1032 / 1.5), 200, 32)   # height clamped to the bar
    assert r.pill_h == 20


def test_left_anchor_centered_icons():
    r = compute_placement(env(info(), icons_centered=True), Placement(anchor="left"))
    assert r.visible and r.reason == "ok"
    assert r.rect == (4, 1036, 200, 40)           # 4 px from the left end, centred on the bar


def test_left_anchor_scaled_and_offset_screen():
    i = info(rect=(2880, 1500, 5760, 1620), tray_rect=(5000, 1500, 5760, 1620),
             monitor_rect=(2880, 0, 5760, 1620))
    r = compute_placement(env(i, dpr=1.5, screen_origin=(1920, 0), icons_centered=True),
                          Placement(anchor="left"))
    assert r.rect[0] == 1920 + 4 and r.rect[2:] == (200, 40)     # 6 physical px gap -> 4 logical


@pytest.mark.parametrize("centered", [False, None])
def test_left_anchor_falls_back_to_right(centered):
    left = compute_placement(env(info(), icons_centered=centered), Placement(anchor="left"))
    right = run(env(info()))
    assert left.visible and left.reason == "left_needs_centered_icons"
    assert left.rect == right.rect and left.max_extent == right.max_extent


def test_left_anchor_vertical_is_top_end():
    i = info(rect=(0, 0, 48, 1080), tray_rect=(0, 900, 48, 1080), edge="left")
    r = compute_placement(env(i, size=(40, 100), icons_centered=True), Placement(anchor="left"))
    assert r.vertical and r.rect == (4, 4, 40, 100)
    r2 = compute_placement(env(i, size=(40, 100), icons_centered=False), Placement(anchor="left"))
    assert r2.reason == "left_needs_centered_icons" and r2.rect == (4, 796, 40, 100)


def test_left_anchor_on_secondary_taskbar_and_clear_of_tray():
    e = Env(info=sec_info(), size=(200, 40), screen_name=D2, screen_origin=(1920, -360))
    r = compute_placement(e, Placement(anchor="left"))
    assert r.rect[0] == 1924 and r.rect[1] == 1516
    wide = Env(info=sec_info(), size=(925, 40), screen_name=D2, screen_origin=(1920, -360))
    r = compute_placement(wide, Placement(anchor="left"))
    assert r.rect[0] + r.rect[2] <= 2850 - 4                  # never overlaps the tray block


def test_left_anchor_hidden_cases_still_apply():
    assert compute_placement(env(info(), fullscreen=True), Placement(anchor="left")).reason == "fullscreen"
    assert compute_placement(Env(info=None), Placement(anchor="left")).reason == "no_taskbar"


# ---- floating
def fenv(screen, size=(200, 40), **kw):
    return Env(info=None, size=size, screen_name=screen.name, screen_origin=screen.geometry[:2],
               dpr=screen.dpr, available=screen.available, **kw)


def float_at(screen, anchor, dx=0, dy=0, size=(200, 40)):
    return compute_placement(fenv(screen, size), Placement(mode="floating", float_anchor=anchor, dx=dx, dy=dy))


@pytest.mark.parametrize("anchor,expected", [
    ("top-left", (0, 0)), ("top-center", (860, 0)), ("top-right", (1720, 0)),
    ("middle-left", (0, 496)), ("middle-right", (1720, 496)),
    ("bottom-left", (0, 992)), ("bottom-center", (860, 992)), ("bottom-right", (1720, 992)),
])
def test_floating_anchors_primary(anchor, expected):
    r = float_at(PRIMARY, anchor)
    assert r.visible and r.reason == "ok" and not r.vertical
    assert r.rect == expected + (200, 40)
    assert r.pill_h == pl.FLOATING_PILL_H


def test_floating_stays_above_taskbar_and_works_without_one():
    r = float_at(PRIMARY, "bottom-right")
    assert r.rect[1] + r.rect[3] <= 1032                 # inside availableGeometry, taskbar not covered
    assert compute_placement(fenv(PRIMARY), Placement(mode="floating")).visible   # info is None


def test_floating_default_offset_and_offsets():
    r = compute_placement(fenv(PRIMARY), Placement(mode="floating"))
    assert r.rect == (1720 - 12, 992 - 12, 200, 40)
    assert float_at(PRIMARY, "top-left", 30, 20).rect == (30, 20, 200, 40)
    assert float_at(PRIMARY, "bottom-right", -30, -20).rect == (1690, 972, 200, 40)


def test_floating_clamped_inside_available():
    assert float_at(PRIMARY, "top-left", -500, -500).rect[:2] == (0, 0)
    assert float_at(PRIMARY, "bottom-right", 900, 900).rect[:2] == (1720, 992)
    assert float_at(PRIMARY, "top-left", 5000, 5000).rect[:2] == (1720, 992)


def test_floating_secondary_monitor_negative_y_and_dpr():
    r = float_at(SECOND, "top-right", -10, 10)
    assert r.rect == (1920 + 1080 - 200 - 10, -360 + 10, 200, 40) and r.screen_name == D2
    r = float_at(SECOND, "bottom-left")
    assert r.rect == (1920, -360 + 1872 - 40, 200, 40)         # above the secondary taskbar
    h = float_at(HIDPI, "middle-left", 5, 0)
    assert h.rect == (-1280 + 5, 100 + (680 - 40) // 2, 200, 40)
    h = float_at(HIDPI, "top-left", -9999, -9999)
    assert h.rect[:2] == (-1280, 100)


def test_floating_oversized_widget_shrinks_to_screen():
    r = float_at(SECOND, "top-left", size=(5000, 40))
    assert r.rect[2] == 1080


def test_floating_limit_and_hidden_cases():
    r = float_at(PRIMARY, "top-left")
    assert r.max_extent == int(1920 * 0.9)
    fs = compute_placement(fenv(PRIMARY, fullscreen=True), Placement(mode="floating"))
    assert not fs.visible and fs.reason == "fullscreen"
    assert compute_placement(fenv(PRIMARY, has_pills=False), Placement(mode="floating")).reason == "no_pills"
    assert compute_placement(Env(info=None, size=(10, 10)), Placement(mode="floating")).reason == "no_screen"


def test_lookup_floating():
    pos = {"bbb": {"anchor": "top-left", "dx": 3, "dy": 4}, "primary": {"anchor": "top-right", "dx": 1, "dy": 2}}
    assert pl.lookup_floating(pos, "bbb", "bbb") == ("top-left", 3, 4)
    assert pl.lookup_floating(pos, "aaa", "primary") == ("top-right", 1, 2)
    assert pl.lookup_floating(pos, "aaa", "aaa") == pl.DEFAULT_FLOATING
    assert pl.lookup_floating({}, "aaa", "primary") == pl.DEFAULT_FLOATING


# ---- snap_anchor
@pytest.mark.parametrize("rect,anchor", [
    ((10, 10, 200, 40), "top-left"), ((860, 5, 200, 40), "top-center"), ((1700, 10, 200, 40), "top-right"),
    ((5, 500, 200, 40), "middle-left"), ((1710, 500, 200, 40), "middle-right"),
    ((10, 980, 200, 40), "bottom-left"), ((900, 990, 200, 40), "bottom-center"),
    ((1700, 980, 200, 40), "bottom-right"),
])
def test_snap_anchor_nearest(rect, anchor):
    assert pl.snap_anchor(rect, PRIMARY.available)[0] == anchor


def test_snap_anchor_center_of_screen_picks_an_edge_anchor():
    a, _, _ = pl.snap_anchor((860, 496, 200, 40), PRIMARY.available)   # exact centre
    assert a in ("top-center", "bottom-center", "middle-left", "middle-right")


def test_snap_anchor_offsets_roundtrip():
    for screen in (PRIMARY, SECOND, HIDPI):
        ax, ay, aw, ah = screen.available
        for rect in [(ax + 40, ay + 25, 200, 40), (ax + aw - 260, ay + ah - 90, 200, 40),
                     (ax + aw // 2 - 90, ay + 7, 200, 40)]:
            anchor, dx, dy = pl.snap_anchor(rect, screen.available)
            back = compute_placement(fenv(screen), Placement(mode="floating", float_anchor=anchor, dx=dx, dy=dy))
            assert back.rect == rect


def test_snap_anchor_offsets_values():
    assert pl.snap_anchor((30, 20, 200, 40), PRIMARY.available) == ("top-left", 30, 20)
    assert pl.snap_anchor((1690, 972, 200, 40), PRIMARY.available) == ("bottom-right", -30, -20)
