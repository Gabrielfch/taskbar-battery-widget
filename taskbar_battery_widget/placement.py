"""Pure placement maths: where the pills go for a given taskbar / screen / placement choice.

No Qt widgets and no Win32 calls: everything the decision needs arrives in `Env`, so it is unit-testable.
Only the current behaviour (``mode="taskbar"``, ``anchor="right"``, ``monitor="primary"``) is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import taskbar

MIN_PILL_H, MAX_PILL_H = 20, 32
FREE_SPACE_SHARE = 0.4   # share of the taskbar run left of the tray the pills may use
MIN_LIMIT = 120


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


@dataclass
class Placement:
    mode: str = "taskbar"        # "taskbar" (floating comes later)
    anchor: str = "right"        # "right" (left comes later)
    monitor: str = "primary"


@dataclass
class PlacementResult:
    rect: tuple | None         # logical (x, y, w, h); None when not visible
    screen_name: str
    vertical: bool
    max_extent: int | None
    visible: bool
    reason: str                # "ok", "no_taskbar", "no_pills", "tucked", "fullscreen"
    pill_h: int = 0


def _hidden(env: Env, reason: str) -> PlacementResult:
    return PlacementResult(None, env.screen_name, False, None, False, reason)


def compute_placement(env: Env, placement: Placement) -> PlacementResult:
    info = env.info
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
    prect = taskbar.anchor_widget_rect(info, w_phys, h_phys, gap=round(4 * dpr))
    if not vertical:    # centre on the notification area's icon row, not just the bar
        ty = (info.tray_rect[1] + info.tray_rect[3]) // 2
        if info.rect[1] <= ty <= info.rect[3]:
            prect = center_rect_y(prect, ty)
    rect = taskbar.physical_to_logical_rect(prect, info.monitor_rect[:2], env.screen_origin, dpr)
    return PlacementResult(rect, env.screen_name, vertical, limit, True, "ok", ph)
