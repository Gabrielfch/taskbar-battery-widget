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


EVENT_OBJECT_REORDER = 0x8004
WINEVENT_OUTOFCONTEXT = 0x0000
_ZGUARD_MIN_INTERVAL = 0.015  # seconds; avoids a raise storm if something else keeps reordering


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
        proto = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                   wt.DWORD, wt.DWORD)
        self._proc = proto(self._on_event)
        _u32.UnhookWinEvent.argtypes = [wt.HANDLE]
        _u32.UnhookWinEvent.restype = wt.BOOL
        _u32.SetWinEventHook.restype = wt.HANDLE
        _u32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, proto, wt.DWORD,
                                         wt.DWORD, wt.DWORD]
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
