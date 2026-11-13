"""Pure placement maths: where the pills go for a given taskbar / screen / placement choice.

No Qt widgets and no Win32 calls: everything the decision needs arrives in `Env`, so it is unit-testable.
Modes: ``taskbar`` (right or left end of a taskbar, primary or secondary) and ``floating`` (free rect
inside a monitor's available geometry, positioned by one of 8 anchors plus an offset).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import taskbar
from .config import FLOATING_ANCHORS

MIN_PILL_H, MAX_PILL_H = 20, 32
FREE_SPACE_SHARE = 0.4   # share of the taskbar run left of the tray the pills may use
MIN_LIMIT = 120
LEFT_GAP = 4             # logical px between the taskbar's left/top end and the pills
LEFT_MAX_SHARE = 0.25    # left anchor: max share of the taskbar length the pills may use
LEFT_MIN_LIMIT = 60
FLOATING_PILL_H = 28
FLOATING_SPACE_SHARE = 0.9   # share of the available width the floating pills may use
DEFAULT_FLOATING = ("bottom-right", -12, -12)   # (anchor, dx, dy) when nothing is stored
REASON_LEFT_NEEDS_CENTERED = "left_needs_centered_icons"
REASON_NO_TASKBAR_FLOATING = "no_taskbar_floating"
WIDGETS_BUTTON_GAP = 8   # logical px after the Win11 Widgets button (which is as wide as the bar is thick)


def pill_height_for(taskbar_logical_thickness: float) -> int:
    return int(max(MIN_PILL_H, min(MAX_PILL_H, round(taskbar_logical_thickness) - 14)))


def center_rect_y(rect: tuple, cy: int) -> tuple:
    """Shift a (l, t, r, b) rect vertically so its centre is `cy`."""
    h = rect[3] - rect[1]
    top = cy - h // 2
    return (rect[0], top, rect[2], top + h)


@dataclass
class Env:
    """Everything the placement decision reads."""
    info: taskbar.TaskbarInfo | None       # physical-pixel taskbar geometry (None: not found)
    fullscreen: bool = False               # a fullscreen app covers the taskbar's monitor
    has_pills: bool = True
    screen_name: str = ""                  # QScreen.name() of the screen holding the taskbar
    screen_origin: tuple = (0, 0)          # logical top-left of that screen
    dpr: float = 1.0
    size: tuple = (0, 0)                   # current logical widget size (w, h)
    available: tuple | None = None         # logical (x, y, w, h) available geometry (floating mode)
    icons_centered: bool | None = True     # taskbar_icons_centered(); the left anchor needs True
    widgets_button: bool | None = False    # Win11 Widgets button shown at the left end (None: unknown = shown)
    floating_fallback: bool = False        # taskbar mode, monitor without a taskbar: float on it instead


@dataclass
class Placement:
    mode: str = "taskbar"        # "taskbar" | "floating"
    anchor: str = "right"        # taskbar mode: "right" | "left" (left = top end on vertical bars)
    monitor: str = "primary"     # "primary" or a monitor key (resolved by the caller)
    float_anchor: str = DEFAULT_FLOATING[0]     # floating mode: one of FLOATING_ANCHORS
    dx: int = DEFAULT_FLOATING[1]
    dy: int = DEFAULT_FLOATING[2]


@dataclass
class PlacementResult:
    rect: tuple | None         # logical (x, y, w, h); None when not visible
    screen_name: str
    vertical: bool
    max_extent: int | None
    visible: bool
    reason: str                # "ok", "no_taskbar", "no_pills", "tucked", "fullscreen", "no_screen",
                               # "left_needs_centered_icons" (visible, fell back to the right end),
                               # "no_taskbar_floating" (visible, floating on a monitor without a taskbar)
    pill_h: int = 0


def _hidden(env: Env, reason: str) -> PlacementResult:
    return PlacementResult(None, env.screen_name, False, None, False, reason)


@dataclass
class ScreenInfo:
    """One Qt screen as the placement code sees it (all geometry logical, i.e. device independent)."""
    key: str
    name: str                    # QScreen.name(); equals MONITORINFOEXW.szDevice on Windows
    geometry: tuple              # (x, y, w, h)
    available: tuple             # (x, y, w, h) without taskbars
    dpr: float = 1.0
    primary: bool = False


def resolve_screen(monitor: str, screens: list) -> ScreenInfo | None:
    """Screen chosen by `monitor` ("primary" or a key); a missing key falls back to the primary screen."""
    if not screens:
        return None
    if monitor and monitor != "primary":
        for s in screens:
            if s.key == monitor:
                return s
    for s in screens:
        if s.primary:
            return s
    return screens[0]


def taskbar_for_screen(screen: ScreenInfo | None, infos: list, screen_names=None):
    """Taskbar on `screen`, matched by szDevice == QScreen.name() (never by size); None if absent.

    Fallback (keeps 1.5.0 behaviour): on the primary screen, the primary taskbar whose device name is
    empty or matches none of `screen_names` (all QScreen names) is used.
    """
    if screen is None or not screen.name:
        return None
    for i in infos or ():
        if i.device_name and i.device_name == screen.name:
            return i
    if screen.primary:
        names = set(screen_names or ())
        for i in infos or ():
            if i.kind == "primary" and (not i.device_name or i.device_name not in names):
                return i
    return None


def lookup_floating(positions: dict, screen_key: str, monitor_choice: str = "primary") -> tuple:
    """(anchor, dx, dy) stored for this screen, else the "primary" entry when primary is the choice."""
    if not isinstance(positions, dict):
        return DEFAULT_FLOATING
    entry = positions.get(screen_key)
    if entry is None and monitor_choice == "primary":
        entry = positions.get("primary")
    if isinstance(entry, dict) and entry.get("anchor") in FLOATING_ANCHORS:
        try:
            return entry["anchor"], int(entry.get("dx", 0)), int(entry.get("dy", 0))
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_FLOATING
    return DEFAULT_FLOATING


def _anchor_xy(anchor: str, avail: tuple, w: int, h: int) -> tuple:
    """Top-left of a (w, h) rect sitting on `anchor` of `avail` (x, y, w, h), before the offset."""
    ax, ay, aw, ah = avail
    v, _, hz = anchor.partition("-")
    x = {"left": ax, "center": ax + (aw - w) // 2, "right": ax + aw - w}.get(hz, ax + aw - w)
    y = {"top": ay, "middle": ay + (ah - h) // 2, "bottom": ay + ah - h}.get(v, ay + ah - h)
    return x, y


def snap_anchor(widget_rect: tuple, available: tuple) -> tuple:
    """(anchor, dx, dy) for a dropped widget: nearest anchor to its centre + offset from that anchor."""
    x, y, w, h = widget_rect
    cx, cy = x + w / 2, y + h / 2
    best = None
    for a in FLOATING_ANCHORS:
        ax, ay = _anchor_xy(a, available, w, h)
        d = (ax + w / 2 - cx) ** 2 + (ay + h / 2 - cy) ** 2
        if best is None or d < best[0]:
            best = (d, a, ax, ay)
    _, a, ax, ay = best
    return a, int(round(x - ax)), int(round(y - ay))


def _floating(env: Env, placement: Placement, reason: str = "ok") -> PlacementResult:
    av = env.available
    if av is None:
        return _hidden(env, "no_screen")
    ax, ay, aw, ah = av
    w, h = min(env.size[0], aw), min(env.size[1], ah)
    anchor = placement.float_anchor if placement.float_anchor in FLOATING_ANCHORS else DEFAULT_FLOATING[0]
    bx, by = _anchor_xy(anchor, av, w, h)
    x = max(ax, min(bx + placement.dx, ax + aw - w))
    y = max(ay, min(by + placement.dy, ay + ah - h))
    limit = max(MIN_LIMIT, int(aw * FLOATING_SPACE_SHARE))
    return PlacementResult((x, y, w, h), env.screen_name, False, limit, True, reason, FLOATING_PILL_H)


def _left_rect(info: taskbar.TaskbarInfo, w: int, h: int, gap: int, vertical: bool) -> tuple:
    """Physical rect at the left (top for vertical bars) end of the taskbar, kept clear of the tray."""
    tb, tray = info.rect, info.tray_rect
    if not vertical:
        h = min(h, taskbar.rect_h(tb))
        left = max(tb[0], min(tb[0] + gap, tray[0] - gap - w))
        top = (tb[1] + tb[3]) // 2 - h // 2
        return (left, top, left + w, top + h)
    w = min(w, taskbar.rect_w(tb))
    top = max(tb[1], min(tb[1] + gap, tray[1] - gap - h))
    left = (tb[0] + tb[2]) // 2 - w // 2
    return (left, top, left + w, top + h)


def compute_placement(env: Env, placement: Placement) -> PlacementResult:
    if placement.mode == "floating":
        if not env.has_pills:
            return _hidden(env, "no_pills")
        if env.fullscreen:
            return _hidden(env, "fullscreen")
        return _floating(env, placement)
    info = env.info
    if info is None and env.floating_fallback:
        if not env.has_pills:
            return _hidden(env, "no_pills")
        if env.fullscreen:
            return _hidden(env, "fullscreen")
        return _floating(env, placement, REASON_NO_TASKBAR_FLOATING)
    if info is None:
        return _hidden(env, "no_taskbar")
    if not env.has_pills:
        return _hidden(env, "no_pills")
    if taskbar.is_tucked(info.rect, info.monitor_rect, info.edge, info.autohide):
        return _hidden(env, "tucked")
    if env.fullscreen:
        return _hidden(env, "fullscreen")
    dpr = env.dpr or 1.0
    vertical = info.edge in (taskbar.EDGE_LEFT, taskbar.EDGE_RIGHT)
    thickness = (taskbar.rect_w(info.rect) if vertical else taskbar.rect_h(info.rect)) / dpr
    ph = pill_height_for(thickness)
    run = (info.tray_rect[1] - info.rect[1]) if vertical else (info.tray_rect[0] - info.rect[0])
    limit = max(MIN_LIMIT, int(run / dpr * FREE_SPACE_SHARE))
    w_phys, h_phys = round(env.size[0] * dpr), round(env.size[1] * dpr)
    reason = "ok"
    left = placement.anchor == "left"
    # Icons start at the top of a vertical bar and are never centred along it: no free top end there.
    if left and (vertical or env.icons_centered is not True):
        left, reason = False, REASON_LEFT_NEEDS_CENTERED
    if left:
        margin = round(LEFT_GAP * dpr)
        if info.kind == "primary" and env.widgets_button is not False:   # leave the Widgets button free
            margin = max(margin, taskbar.rect_h(info.rect) + round(WIDGETS_BUTTON_GAP * dpr))
        prect = _left_rect(info, w_phys, h_phys, margin, vertical)
        # Known limitation: the number of taskbar buttons is unknown, so the pills could still reach the
        # centred icon group on a crowded bar. Conservative cap: at most a quarter of the bar length.
        cap = int(taskbar.rect_w(info.rect) / dpr * LEFT_MAX_SHARE - margin / dpr)
        limit = max(LEFT_MIN_LIMIT, min(limit, cap))
    else:
        prect = taskbar.anchor_widget_rect(info, w_phys, h_phys, gap=round(4 * dpr))
        if not vertical:    # centre on the notification area's icon row, not just the bar
            ty = (info.tray_rect[1] + info.tray_rect[3]) // 2
            if info.rect[1] <= ty <= info.rect[3]:
                prect = center_rect_y(prect, ty)
    rect = taskbar.physical_to_logical_rect(prect, info.monitor_rect[:2], env.screen_origin, dpr)
    return PlacementResult(rect, env.screen_name, vertical, limit, True, reason, ph)
