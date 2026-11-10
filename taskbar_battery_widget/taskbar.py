"""Win32 taskbar helpers (ctypes) plus pure geometry helpers.

All rectangles are tuples ``(left, top, right, bottom)`` in *physical* pixels unless the
function name says otherwise. The geometry helpers are pure so they can be unit tested.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt
from dataclasses import dataclass

Rect = tuple  # (left, top, right, bottom)

EDGE_LEFT, EDGE_TOP, EDGE_RIGHT, EDGE_BOTTOM = "left", "top", "right", "bottom"
_ABE_TO_EDGE = {0: EDGE_LEFT, 1: EDGE_TOP, 2: EDGE_RIGHT, 3: EDGE_BOTTOM}

# Taskbar thinner than this (visible part, px) while auto-hide is on counts as "tucked".
TUCKED_MAX_THICKNESS = 4
# Fallback width of the notification area when it cannot be found.
FALLBACK_TRAY_WIDTH = 300
FALLBACK_TRAY_HEIGHT = 300


@dataclass
class TaskbarInfo:
    rect: Rect
    tray_rect: Rect
    edge: str
    autohide: bool
    monitor_rect: Rect


# --------------------------------------------------------------------------- pure helpers
def rect_w(r: Rect) -> int:
    return r[2] - r[0]


def rect_h(r: Rect) -> int:
    return r[3] - r[1]


def visible_thickness(rect: Rect, monitor_rect: Rect, edge: str) -> int:
    """Thickness of the taskbar part that lies inside the monitor."""
    l = max(rect[0], monitor_rect[0])
    t = max(rect[1], monitor_rect[1])
    r = min(rect[2], monitor_rect[2])
    b = min(rect[3], monitor_rect[3])
    if r <= l or b <= t:
        return 0
    return (r - l) if edge in (EDGE_LEFT, EDGE_RIGHT) else (b - t)


def is_tucked(rect: Rect, monitor_rect: Rect, edge: str, autohide: bool) -> bool:
    return autohide and visible_thickness(rect, monitor_rect, edge) <= TUCKED_MAX_THICKNESS


def derive_edge(rect: Rect, monitor_rect: Rect) -> str:
    if rect_w(rect) >= rect_h(rect):
        mid = (rect[1] + rect[3]) / 2
        return EDGE_TOP if mid < (monitor_rect[1] + monitor_rect[3]) / 2 else EDGE_BOTTOM
    mid = (rect[0] + rect[2]) / 2
    return EDGE_LEFT if mid < (monitor_rect[0] + monitor_rect[2]) / 2 else EDGE_RIGHT


def fallback_tray_rect(rect: Rect, edge: str) -> Rect:
    """Notification area guess: far end of the taskbar."""
    if edge in (EDGE_TOP, EDGE_BOTTOM):
        return (max(rect[0], rect[2] - FALLBACK_TRAY_WIDTH), rect[1], rect[2], rect[3])
    return (rect[0], max(rect[1], rect[3] - FALLBACK_TRAY_HEIGHT), rect[2], rect[3])


def anchor_widget_rect(info: TaskbarInfo, w: int, h: int, gap: int = 4) -> Rect:
    """Physical rect for a widget of size (w, h) docked next to the notification area.

    Horizontal taskbars: right edge sits ``gap`` left of the tray, centred vertically.
    Vertical taskbars: bottom edge sits ``gap`` above the tray, centred horizontally.
    The size is clamped to the taskbar thickness.
    """
    tb = info.rect
    tray = info.tray_rect
    if info.edge in (EDGE_TOP, EDGE_BOTTOM):
        h = min(h, rect_h(tb))
        right = tray[0] - gap
        left = max(tb[0], right - w)
        cy = (tb[1] + tb[3]) // 2
        top = cy - h // 2
        return (left, top, left + w, top + h)
    w = min(w, rect_w(tb))
    bottom = tray[1] - gap
    top = max(tb[1], bottom - h)
    cx = (tb[0] + tb[2]) // 2
    left = cx - w // 2
    return (left, top, left + w, top + h)


def physical_to_logical_rect(rect: Rect, monitor_phys_origin: tuple, screen_logical_origin: tuple,
                             dpr: float) -> tuple:
    """Map a physical rect on a monitor to Qt logical coords (x, y, w, h)."""
    dpr = dpr or 1.0
    x = screen_logical_origin[0] + (rect[0] - monitor_phys_origin[0]) / dpr
    y = screen_logical_origin[1] + (rect[1] - monitor_phys_origin[1]) / dpr
    return (round(x), round(y), round(rect_w(rect) / dpr), round(rect_h(rect) / dpr))


def covers_monitor(win_rect: Rect, monitor_rect: Rect) -> bool:
    return (win_rect[0] <= monitor_rect[0] and win_rect[1] <= monitor_rect[1]
            and win_rect[2] >= monitor_rect[2] and win_rect[3] >= monitor_rect[3])


# --------------------------------------------------------------------------- Win32 plumbing
_IGNORED_FG_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    class _APPBARDATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                    ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]

    _user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    _user32.FindWindowW.restype = wt.HWND
    _user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
    _user32.FindWindowExW.restype = wt.HWND
    _user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _user32.GetForegroundWindow.restype = wt.HWND
    _user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _user32.IsWindowVisible.argtypes = [wt.HWND]
    _user32.IsIconic.argtypes = [wt.HWND]
    _user32.IsZoomed.argtypes = [wt.HWND]
    _user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    _user32.MonitorFromWindow.restype = wt.HANDLE
    _user32.MonitorFromRect.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD]
    _user32.MonitorFromRect.restype = wt.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONITORINFO)]
    _shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(_APPBARDATA)]
    _shell32.SHAppBarMessage.restype = ctypes.c_size_t
    _shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    _shell32.SHQueryUserNotificationState.restype = ctypes.c_long   # HRESULT

# SHQueryUserNotificationState values that mean "a full-screen / presenting app owns the screen".
QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE = 2, 3, 4
_FULLSCREEN_STATES = (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)


def query_user_notification_state() -> int | None:
    """Raw QUNS_* value, or None if the call is unavailable/fails."""
    if sys.platform != "win32":
        return None
    try:
        st = ctypes.c_int(0)
        if _shell32.SHQueryUserNotificationState(ctypes.byref(st)) != 0:
            return None
        return st.value
    except Exception:
        return None


def state_is_fullscreen(state: int | None) -> bool | None:
    """Map a QUNS value to bool; None (unknown) so the caller can fall back."""
    return None if state is None else state in _FULLSCREEN_STATES


def _win_rect(hwnd) -> Rect | None:
    r = wt.RECT()
    if not hwnd or not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _monitor_rect_for(hwnd=None, rect: Rect | None = None) -> Rect | None:
    MONITOR_DEFAULTTONEAREST = 2
    if hwnd:
        hmon = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    elif rect:
        rc = wt.RECT(*rect)
        hmon = _user32.MonitorFromRect(ctypes.byref(rc), MONITOR_DEFAULTTONEAREST)
    else:
        return None
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    if not hmon or not _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None
    m = mi.rcMonitor
    return (m.left, m.top, m.right, m.bottom)


def _appbar(msg: int) -> _APPBARDATA | None:
    abd = _APPBARDATA()
    abd.cbSize = ctypes.sizeof(_APPBARDATA)
    res = _shell32.SHAppBarMessage(msg, ctypes.byref(abd))
    return abd if res else None


def get_taskbar_info() -> TaskbarInfo | None:
    """Current primary taskbar geometry, or None when no taskbar is present."""
    if sys.platform != "win32":
        return None
    try:
        hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
        rect = _win_rect(hwnd)
        if not rect:
            return None
        monitor = _monitor_rect_for(hwnd=hwnd) or rect
        edge = None
        pos = _appbar(5)  # ABM_GETTASKBARPOS
        if pos is not None and pos.uEdge in _ABE_TO_EDGE:
            edge = _ABE_TO_EDGE[pos.uEdge]
        if edge is None:
            edge = derive_edge(rect, monitor)
        abd = _APPBARDATA()
        abd.cbSize = ctypes.sizeof(_APPBARDATA)
        flags = _shell32.SHAppBarMessage(4, ctypes.byref(abd))  # ABM_GETSTATE -> flags
        autohide = bool(flags & 1)  # ABS_AUTOHIDE
        tray = _win_rect(_user32.FindWindowExW(hwnd, None, "TrayNotifyWnd", None))
        if not tray or rect_w(tray) <= 0 or rect_h(tray) <= 0:
            tray = fallback_tray_rect(rect, edge)
        return TaskbarInfo(rect, tray, edge, autohide, monitor)
    except Exception:
        return None


def is_fullscreen_foreground(monitor_rect: Rect) -> bool:
    """True when a full-screen app/game/presentation is active.

    Uses SHQueryUserNotificationState; the "foreground window covers the monitor" heuristic is
    only a fallback when that call fails. A maximized window is never fullscreen by itself with
    an autohide taskbar, because its rect then equals the monitor rect.
    """
    if sys.platform != "win32":
        return False
    verdict = state_is_fullscreen(query_user_notification_state())
    if verdict is not None:
        return verdict
    return _covers_fallback(monitor_rect)


def _covers_fallback(monitor_rect: Rect) -> bool:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return False
        if _class_name(hwnd) in _IGNORED_FG_CLASSES:
            return False
        # A maximized window with a title bar is just maximized (e.g. autohide taskbar).
        if _user32.IsZoomed(hwnd) and (_user32.GetWindowLongW(hwnd, -16) & 0x00C00000):
            return False
        rect = _win_rect(hwnd)
        if not rect:
            return False
        fg_monitor = _monitor_rect_for(hwnd=hwnd)
        if fg_monitor is not None and fg_monitor != tuple(monitor_rect):
            return False
        return covers_monitor(rect, tuple(monitor_rect))
    except Exception:
        return False


def taskbar_uses_light_theme() -> bool:
    """HKCU Personalize SystemUsesLightTheme (taskbar/system theme); default dark."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except Exception:
        return False
