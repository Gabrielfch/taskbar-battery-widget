"""Placement matrix: every taskbar edge x anchor x primary/secondary, widgets button, no-taskbar floating."""
import pytest

from taskbar_battery_widget import app
from taskbar_battery_widget import taskbar as tb
from taskbar_battery_widget.placement import (Env, Placement, REASON_LEFT_NEEDS_CENTERED,
                                              REASON_NO_TASKBAR_FLOATING, _left_rect, compute_placement)

THICK = 48
MON_W, MON_H = 1920, 1080
EDGES = ["bottom", "top", "left", "right"]


def make_info(edge, kind, ox=0):
    """Taskbar on a 1920x1080 monitor whose top-left is (ox, 0); tray = last 300 / 150 px of the bar."""
    mon = (ox, 0, ox + MON_W, MON_H)
    if edge == "bottom":
        rect = (ox, MON_H - THICK, ox + MON_W, MON_H)
    elif edge == "top":
        rect = (ox, 0, ox + MON_W, THICK)
    elif edge == "left":
        rect = (ox, 0, ox + THICK, MON_H)
    else:
        rect = (ox + MON_W - THICK, 0, ox + MON_W, MON_H)
    size = 300 if kind == "primary" else 150
    tray = tb.fallback_tray_rect(rect, edge) if kind == "primary" else (
        (rect[2] - size, rect[1], rect[2], rect[3]) if edge in ("top", "bottom")
        else (rect[0], rect[3] - size, rect[2], rect[3]))
    return tb.TaskbarInfo(rect=rect, tray_rect=tray, edge=edge, autohide=False, monitor_rect=mon, kind=kind,
                          has_tray=kind == "primary", device_name="D")


def run(edge, kind, anchor, centered=True, widgets=False, size=None):
    ox = 0 if kind == "primary" else MON_W
    info = make_info(edge, kind, ox)
    vertical = edge in ("left", "right")
    size = size or ((40, 100) if vertical else (200, 36))
    env = Env(info=info, screen_name="D", screen_origin=(ox, 0), dpr=1.0, size=size,
              icons_centered=centered, widgets_button=widgets)
    res = compute_placement(env, Placement("taskbar", anchor, "primary"))
    return info, res


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def as_ltrb(rect):
    x, y, w, h = rect
    return (x, y, x + w, y + h)


@pytest.mark.parametrize("kind", ["primary", "secondary"])
@pytest.mark.parametrize("anchor", ["right", "left"])
@pytest.mark.parametrize("edge", EDGES)
def test_matrix(edge, anchor, kind):
    info, res = run(edge, kind, anchor)
    vertical = edge in ("left", "right")
    assert res.visible and res.vertical is vertical
    r = as_ltrb(res.rect)
    tbr, tray = info.rect, info.tray_rect
    assert tbr[0] <= r[0] and tbr[1] <= r[1] and r[2] <= tbr[2] and r[3] <= tbr[3], "inside the taskbar"
    assert not overlaps(r, tray), "clear of the tray / clock"
    fell_back = anchor == "left" and vertical
    assert res.reason == (REASON_LEFT_NEEDS_CENTERED if fell_back else "ok")
    if vertical:
        # right anchor (and the left fallback) = bottom end, next to the tray at the bottom
        assert r[3] <= tray[1] and tray[1] - r[3] <= 8
    elif anchor == "right":
        assert r[2] <= tray[0] and tray[0] - r[2] <= 8
    else:
        assert r[0] - tbr[0] <= 8 and r[2] <= tray[0]       # left end, widgets button off


@pytest.mark.parametrize("edge", ["left", "right"])
def test_vertical_left_anchor_geometry_is_top_end(edge):
    """_left_rect on a vertical bar = top end, clear of the tray (used if vertical icons ever centre)."""
    info = make_info(edge, "primary")
    r = _left_rect(info, 40, 100, 4, True)
    assert r[1] == info.rect[1] + 4 and r[3] <= info.tray_rect[1]
    assert info.rect[0] <= r[0] and r[2] <= info.rect[2]


@pytest.mark.parametrize("edge", EDGES)
def test_left_anchor_left_aligned_icons_falls_back_everywhere(edge):
    for kind in ("primary", "secondary"):
        for centered in (False, None):
            _, res = run(edge, kind, "left", centered=centered)
            assert res.reason == REASON_LEFT_NEEDS_CENTERED and res.visible


@pytest.mark.parametrize("edge", ["bottom", "top"])
def test_left_anchor_with_centered_icons_primary_widgets_button(edge):
    info, off = run(edge, "primary", "left", widgets=False)
    _, on = run(edge, "primary", "left", widgets=True)
    _, unknown = run(edge, "primary", "left", widgets=None)
    assert off.rect[0] == 4
    assert on.rect[0] >= THICK + 8 and unknown.rect[0] == on.rect[0]
    assert on.reason == "ok"


@pytest.mark.parametrize("edge", ["bottom", "top"])
def test_widgets_button_margin_ignored_on_secondary(edge):
    _, res = run(edge, "secondary", "left", widgets=True)
    assert res.rect[0] == MON_W + 4


def test_widgets_margin_scales_with_dpr_and_never_overlaps_tray():
    info = make_info("bottom", "primary")
    env = Env(info=info, screen_name="D", dpr=1.0, size=(200, 36), widgets_button=True)
    r = compute_placement(env, Placement("taskbar", "left", "primary")).rect
    assert r[0] == THICK + 8
    narrow = tb.TaskbarInfo(rect=(0, 1032, 500, 1080), tray_rect=(300, 1032, 500, 1080), edge="bottom",
                            autohide=False, monitor_rect=(0, 0, 500, 1080), device_name="D")
    r = compute_placement(Env(info=narrow, screen_name="D", size=(200, 36), widgets_button=True),
                          Placement("taskbar", "left", "primary")).rect
    assert r[0] + r[2] <= 300 - 4


# --------------------------------------------------------------------------- edge detection
@pytest.mark.parametrize("edge", EDGES)
def test_derive_edge_all_four(edge):
    for ox in (0, MON_W, -MON_W):
        info = make_info(edge, "secondary", ox)
        assert tb.derive_edge(info.rect, info.monitor_rect) == edge


@pytest.mark.parametrize("edge", EDGES)
def test_fallback_tray_is_at_far_end(edge):
    rect = make_info(edge, "primary").rect
    vertical = edge in ("left", "right")
    sec = tb.fallback_tray_rect(rect, edge)
    assert (sec[3] == rect[3] and sec[2] == rect[2]) and (sec[1] > rect[1] if vertical else sec[0] > rect[0])


@pytest.mark.live_taskbar
@pytest.mark.parametrize("edge", EDGES)
def test_secondary_tray_rect_fallback_all_edges(edge, monkeypatch):
    monkeypatch.setattr(tb._user32, "FindWindowExW", lambda *a: None, raising=False)
    rect = make_info(edge, "secondary").rect
    tray = tb.secondary_tray_rect(1, rect, edge)
    if edge in ("top", "bottom"):
        assert tray == (rect[2] - 150, rect[1], rect[2], rect[3])
    else:
        assert tray == (rect[0], rect[3] - 150, rect[2], rect[3])


# --------------------------------------------------------------------------- no taskbar: floating
def test_no_taskbar_floating_default_bottom_right_of_available():
    env = Env(info=None, screen_name="D", size=(200, 36), available=(1920, 0, 1920, 1032), floating_fallback=True)
    res = compute_placement(env, Placement("taskbar", "right", "k"))
    assert res.visible and res.reason == REASON_NO_TASKBAR_FLOATING and not res.vertical
    assert (res.rect[0] + res.rect[2], res.rect[1] + res.rect[3]) == (1920 + 1920 - 12, 1032 - 12)


def test_no_taskbar_floating_uses_saved_anchor():
    env = Env(info=None, size=(200, 36), available=(0, 0, 1000, 800), floating_fallback=True)
    res = compute_placement(env, Placement("taskbar", "right", "k", "top-left", 10, 20))
    assert res.rect[:2] == (10, 20) and res.reason == REASON_NO_TASKBAR_FLOATING


def test_no_taskbar_without_fallback_hides_and_fallback_respects_fullscreen():
    env = Env(info=None, size=(200, 36), available=(0, 0, 1000, 800))
    assert compute_placement(env, Placement()).reason == "no_taskbar"
    env = Env(info=None, size=(200, 36), available=(0, 0, 1000, 800), floating_fallback=True, fullscreen=True)
    assert not compute_placement(env, Placement()).visible
    env = Env(info=None, size=(200, 36), available=(0, 0, 1000, 800), floating_fallback=True, has_pills=False)
    assert compute_placement(env, Placement()).reason == "no_pills"


# --------------------------------------------------------------------------- taskbar_widgets_button
class _Key:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_winreg(monkeypatch, value):
    import sys
    import types

    def query(k, name):
        if value is FileNotFoundError:
            raise FileNotFoundError
        return (value, 4)
    mod = types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=lambda *a: _Key(value), QueryValueEx=query)
    monkeypatch.setitem(sys.modules, "winreg", mod)


@pytest.mark.parametrize("value,expected", [(1, True), (0, False), (FileNotFoundError, True)])
def test_widgets_button_win11(monkeypatch, value, expected):
    monkeypatch.setattr(tb, "_is_windows_11", lambda: True)
    _fake_winreg(monkeypatch, value)
    assert tb.taskbar_widgets_button() is expected


def test_widgets_button_win10_false_and_failure_none(monkeypatch):
    monkeypatch.setattr(tb, "_is_windows_11", lambda: False)
    _fake_winreg(monkeypatch, 1)
    assert tb.taskbar_widgets_button() is False
    monkeypatch.setattr(tb, "_is_windows_11", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "winreg", None)     # import fails
    assert tb.taskbar_widgets_button() is None


# --------------------------------------------------------------------------- menu suffix
def test_screen_menu_entries_marks_monitors_without_taskbar(qapp, monkeypatch):
    from taskbar_battery_widget import widget as w
    from taskbar_battery_widget.i18n import tr
    pairs = w.screen_keys()
    name0 = pairs[0][1].name()
    monkeypatch.setattr(tb, "get_taskbar_infos", lambda: [tb.TaskbarInfo(
        (0, 0, 1, 1), (0, 0, 1, 1), "bottom", False, (0, 0, 1, 1), device_name=name0)])
    entries = app.screen_menu_entries()
    assert not entries[0][1].endswith(tr("monitor_no_taskbar"))
    for (_k, label), (_k2, sc) in list(zip(entries, pairs))[1:]:
        assert label.endswith(tr("monitor_no_taskbar"))
    monkeypatch.setattr(tb, "get_taskbar_infos", lambda: [])         # no info at all: no suffix
    assert all(tr("monitor_no_taskbar") not in lbl for _k, lbl in app.screen_menu_entries())


def test_menu_suffix_texts():
    from taskbar_battery_widget import i18n
    assert i18n.tr_in("es", "monitor_no_taskbar") == " (sin barra: flotante)"
    assert i18n.tr_in("en", "monitor_no_taskbar") == " (no taskbar: floating)"
