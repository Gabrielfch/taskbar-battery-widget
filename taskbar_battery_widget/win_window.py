"""Win32 window helpers (ctypes): extended styles, topmost raising, Z-order guard, popup menu focus."""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes as wt

# --------------------------------------------------------------------------- win32
if sys.platform == "win32":
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.UINT]
    _u32.SetWindowPos.restype = wt.BOOL
    _u32.SetForegroundWindow.argtypes = [wt.HWND]
    _u32.SetForegroundWindow.restype = wt.BOOL
    _u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u32.PostMessageW.restype = wt.BOOL
    _u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u32.GetAsyncKeyState.restype = ctypes.c_short
    _u32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    _u32.FindWindowW.restype = wt.HWND
    _u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u32.GetClassNameW.restype = ctypes.c_int
    _u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wt.DWORD

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10


def _apply_ex_styles(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    h = wt.HWND(hwnd)
    cur = _u32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    _u32.SetWindowLongPtrW(h, GWL_EXSTYLE, cur | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x20
VK_SHIFT = 0x10


def set_click_through(hwnd: int, on: bool) -> bool:
    """Toggle WS_EX_TRANSPARENT (mouse falls through to the window below); keeps the other ex-styles.

    Returns True when the style was applied; a safe no-op (False) off Windows or on any failure.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        h = wt.HWND(hwnd)
        cur = _u32.GetWindowLongPtrW(h, GWL_EXSTYLE)
        new = (cur | WS_EX_LAYERED | WS_EX_TRANSPARENT) if on else (cur & ~WS_EX_TRANSPARENT)
        if new != cur:
            _u32.SetWindowLongPtrW(h, GWL_EXSTYLE, new)
        return True
    except Exception:
        return False


def shift_down() -> bool:
    """True while a Shift key is physically held (system-wide, works while another app has focus)."""
    if sys.platform != "win32":
        return False
    try:
        return bool(_u32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
    except Exception:
        return False


WM_NULL = 0x0000


def _foreground(hwnd: int) -> None:
    """SetForegroundWindow on one of our own windows (needed so a popup menu closes on outside clicks)."""
    if sys.platform != "win32" or not hwnd:
        return
    _u32.SetForegroundWindow(wt.HWND(hwnd))


def _post_null(hwnd: int) -> None:
    """Classic tray-menu companion: WM_NULL after the menu ends so the menu loop does not linger."""
    if sys.platform != "win32" or not hwnd:
        return
    _u32.PostMessageW(wt.HWND(hwnd), WM_NULL, 0, 0)


def popup_menu_foreground(menu, pos, owner_hwnd: int = 0) -> None:
    """Show `menu` at `pos` from a non-activating window so it dismisses on any outside click.

    The menu's window is made foreground right after it appears; when it hides, WM_NULL is posted to the
    owner window (or the menu's own window when no owner is given).
    """
    menu.popup(pos)
    try:
        mh = int(menu.winId())
        _foreground(mh)
        target = owner_hwnd or mh

        def _closed() -> None:
            try:
                menu.aboutToHide.disconnect(_closed)
            except (RuntimeError, TypeError):
                pass
            _post_null(target)
        menu.aboutToHide.connect(_closed)
    except Exception:
        pass


def _assert_topmost(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    _u32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


HWND_NOTOPMOST = -2


def _drop_topmost(hwnd: int) -> None:
    """Remove the topmost z-order band from `hwnd` without moving, resizing or activating it."""
    if sys.platform != "win32" or not hwnd:
        return
    _u32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


EVENT_OBJECT_REORDER = 0x8004
WINEVENT_OUTOFCONTEXT = 0x0000
_ZGUARD_MIN_INTERVAL = 0.015  # seconds; avoids a raise storm if something else keeps reordering


_WINEVENTPROC = None


def _win_event_proto():
    """The WINEVENTPROC type, created once so every hook shares SetWinEventHook's argtypes."""
    global _WINEVENTPROC
    if _WINEVENTPROC is None:
        _WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                           wt.DWORD, wt.DWORD)
        _u32.UnhookWinEvent.argtypes = [wt.HANDLE]
        _u32.UnhookWinEvent.restype = wt.BOOL
        _u32.SetWinEventHook.restype = wt.HANDLE
        _u32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, _WINEVENTPROC, wt.DWORD,
                                         wt.DWORD, wt.DWORD]
    return _WINEVENTPROC


class _ZGuard:
    """Re-raises our window the instant the shell reorders windows.

    On Windows 11 explorer pushes the taskbar back above ordinary topmost windows within ~50 ms,
    so a periodic SetWindowPos alone loses. A WinEvent hook on EVENT_OBJECT_REORDER (delivered in
    this thread's message loop) lets us win the race without a busy timer.
    """

    def __init__(self, hwnd_getter, extra_getter=None):
        self._get = hwnd_getter
        self._extra = extra_getter      # hwnd of a companion window kept just above ours
        self._hook = None
        self._last = 0.0
        self._proc = None

    def start(self) -> None:
        if sys.platform != "win32" or self._hook:
            return
        proto = _win_event_proto()
        self._proc = proto(self._on_event)
        self._hook = _u32.SetWinEventHook(EVENT_OBJECT_REORDER, EVENT_OBJECT_REORDER, None,
                                          self._proc, 0, 0, WINEVENT_OUTOFCONTEXT)

    def stop(self) -> None:
        if self._hook:
            try:
                _u32.UnhookWinEvent(self._hook)
            finally:
                self._hook = None
                self._proc = None

    def _on_event(self, _hook, _event, hwnd, _idobj, _idchild, _thread, _time) -> None:
        try:
            mine = self._get()
            if not mine or (hwnd or 0) == mine:
                return       # our own reorder: re-raising would feed back into itself
            now = time.monotonic()
            if now - self._last < _ZGUARD_MIN_INTERVAL:
                return
            self._last = now
            _assert_topmost(mine)
            extra = self._extra() if self._extra else 0
            if extra:
                _assert_topmost(extra)
        except Exception:
            pass


EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")


class _TaskbarMoveWatch:
    """Calls `on_move` the moment a taskbar window moves or resizes (auto-hide slide, edge change).

    Without it the widget only noticed an auto-hide taskbar on its periodic tick, so it appeared
    and vanished up to a tick late and did not follow the slide. The hook is limited to explorer's
    process (location changes are frequent system-wide); `refresh()` re-hooks after explorer restarts.
    """

    def __init__(self, on_move):
        self._on_move = on_move
        self._hook = None
        self._proc = None
        self._pid = 0
        self._hwnds: set[int] = set()         # hwnds known to be taskbars (only hits are cached)

    def refresh(self) -> None:
        """(Re)hook explorer's process; cheap, meant to run on the periodic tick."""
        if sys.platform != "win32":
            return
        pid = _taskbar_pid()
        if pid == self._pid and (self._hook or not pid):
            return
        self.stop()
        if not pid:
            return
        proto = _win_event_proto()
        self._proc = proto(self._on_event)
        self._hook = _u32.SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE, None,
                                          self._proc, pid, 0, WINEVENT_OUTOFCONTEXT)
        self._pid = pid if self._hook else 0
        if not self._hook:
            self._proc = None

    def stop(self) -> None:
        if self._hook:
            try:
                _u32.UnhookWinEvent(self._hook)
            except Exception:
                pass
        self._hook = None
        self._proc = None
        self._pid = 0
        self._hwnds.clear()

    def _is_taskbar(self, hwnd: int) -> bool:
        """Misses are never cached: a handle explorer reuses for a new taskbar (a monitor plugged
        in later) must not stay ignored. The few taskbar hits are cached."""
        if hwnd in self._hwnds:
            return True
        buf = ctypes.create_unicode_buffer(64)
        n = _u32.GetClassNameW(wt.HWND(hwnd), buf, 64)
        if n > 0 and buf.value in _TASKBAR_CLASSES:
            self._hwnds.add(hwnd)
            return True
        return False

    def _on_event(self, _hook, _event, hwnd, idobj, _idchild, _thread, _time) -> None:
        try:
            if idobj != OBJID_WINDOW or not hwnd or not self._is_taskbar(int(hwnd)):
                return
            self._on_move()
        except Exception:
            pass


def _taskbar_pid() -> int:
    """Process id of the window owning the primary taskbar (explorer), 0 if there is none."""
    try:
        hwnd = _u32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return 0
        pid = wt.DWORD(0)
        _u32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0
