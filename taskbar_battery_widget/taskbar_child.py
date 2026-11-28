"""Experimental: show the pills in a layered *child* window of the taskbar (ctypes).

A child of ``Shell_TrayWnd`` is composed as part of the taskbar, so it slides with an auto-hide taskbar,
hides with it over fullscreen apps and always sits above it - all done by Windows, frame-perfect. Our
normal top-level window can only chase the taskbar after it moved.

The child only shows pixels: it is click-through (``WS_EX_TRANSPARENT``) and the regular widget window
stays on top of it, invisible, for the mouse (hover, clicks, drag, menu). The child lives on its own
thread with a GetMessage loop and the stock ``DefWindowProcW``, so no Python code runs when explorer
talks to it (a cross-process child shares input with the taskbar: a busy GIL must not stall explorer).

Needs Windows 8+ in the exe manifest (layered child windows); python.exe and PyInstaller builds have it.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes as wt

WS_CHILD, WS_CLIPSIBLINGS = 0x40000000, 0x04000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x00080000, 0x20, 0x08000000
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0x0, 0x1
SW_HIDE, SW_SHOWNA = 0, 8
HWND_TOP = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
WM_APP_DESTROY = 0x8000 + 0x51
DIB_RGB_COLORS = 0
_CLASS_NAME = "TaskbarBatteryWidgetChild"
log = logging.getLogger(__name__)


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


if sys.platform == "win32":
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _u32.RegisterClassW.restype = wt.ATOM
    _u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    _u32.CreateWindowExW.restype = wt.HWND
    _u32.DestroyWindow.argtypes = [wt.HWND]
    _u32.DestroyWindow.restype = wt.BOOL
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    _u32.GetMessageW.restype = wt.BOOL
    _u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    _u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    _u32.DispatchMessageW.restype = ctypes.c_ssize_t
    _u32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u32.PostThreadMessageW.restype = wt.BOOL
    _u32.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
    _u32.ShowWindowAsync.restype = wt.BOOL
    _u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wt.UINT]
    _u32.SetWindowPos.restype = wt.BOOL
    _u32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    _u32.ScreenToClient.restype = wt.BOOL
    _u32.IsWindow.argtypes = [wt.HWND]
    _u32.IsWindow.restype = wt.BOOL
    _u32.GetDC.argtypes = [wt.HWND]
    _u32.GetDC.restype = wt.HDC
    _u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    _u32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
                                         wt.HDC, ctypes.POINTER(wt.POINT), wt.COLORREF,
                                         ctypes.POINTER(_BLENDFUNCTION), wt.DWORD]
    _u32.UpdateLayeredWindow.restype = wt.BOOL
    _u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u32.DefWindowProcW.restype = ctypes.c_ssize_t
    _gdi.CreateCompatibleDC.argtypes = [wt.HDC]
    _gdi.CreateCompatibleDC.restype = wt.HDC
    _gdi.DeleteDC.argtypes = [wt.HDC]
    _gdi.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(_BITMAPINFOHEADER), wt.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
    _gdi.CreateDIBSection.restype = wt.HBITMAP
    _gdi.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    _gdi.SelectObject.restype = wt.HGDIOBJ
    _gdi.DeleteObject.argtypes = [wt.HGDIOBJ]
    _k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    _k32.GetModuleHandleW.restype = wt.HMODULE
    _k32.GetCurrentThreadId.restype = wt.DWORD

_class_atom = 0


def _register_class() -> bool:
    global _class_atom
    if _class_atom:
        return True
    wc = _WNDCLASSW()
    # The stock window procedure: nothing of ours runs when explorer sends this window a message.
    wc.lpfnWndProc = ctypes.cast(_u32.DefWindowProcW, ctypes.c_void_p).value
    wc.hInstance = _k32.GetModuleHandleW(None)
    wc.lpszClassName = _CLASS_NAME
    _class_atom = _u32.RegisterClassW(ctypes.byref(wc))
    return bool(_class_atom)


class _Surface:
    """A memory DC with a top-down 32-bit DIB of a fixed size (reused while the size stays)."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.dc = _gdi.CreateCompatibleDC(None)
        bih = _BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bih.biWidth, bih.biHeight = w, -h       # negative: top-down rows, like QImage
        bih.biPlanes, bih.biBitCount = 1, 32
        self.bits = ctypes.c_void_p()
        self.bmp = _gdi.CreateDIBSection(self.dc, ctypes.byref(bih), DIB_RGB_COLORS, ctypes.byref(self.bits),
                                         None, 0)
        self.old = _gdi.SelectObject(self.dc, self.bmp) if self.bmp else None

    @property
    def ok(self) -> bool:
        return bool(self.dc and self.bmp and self.bits.value)

    def close(self) -> None:
        if self.old:
            _gdi.SelectObject(self.dc, self.old)
        if self.bmp:
            _gdi.DeleteObject(self.bmp)
        if self.dc:
            _gdi.DeleteDC(self.dc)
        self.dc = self.bmp = self.old = None


BACKOFF_FIRST_S = 2.0     # wait this long after the first failure before trying again ...
BACKOFF_MAX_S = 120.0     # ... doubling up to this


class _Worker:
    """One attempt: a thread that creates the child in `parent` and pumps its messages until told to stop.

    Creation never blocks the GUI thread. If the attempt is cancelled before the window exists, the thread
    destroys the window itself as soon as CreateWindowExW returns, so a slow explorer cannot leave an
    orphan behind. `on_done(worker)` is called on the worker thread once the window exists or failed.
    """

    def __init__(self, parent: int, on_done):
        self.parent = parent
        self.hwnd = 0
        self.tid = 0
        self.state = "creating"         # "creating" | "ready" | "failed" | "closed"
        self._cancel = False
        self._lock = threading.Lock()
        self._on_done = on_done
        self.exited = threading.Event()
        self.thread = threading.Thread(target=self._run, name="taskbar-child", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        try:
            hwnd = int(_u32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE, _CLASS_NAME,
                                            None, WS_CHILD | WS_CLIPSIBLINGS, 0, 0, 1, 1, wt.HWND(self.parent),
                                            None, _k32.GetModuleHandleW(None), None) or 0)
            with self._lock:
                if hwnd and self._cancel:
                    _u32.DestroyWindow(wt.HWND(hwnd))       # cancelled while explorer was slow: no orphan
                    hwnd = 0
                    self.state = "closed"
                elif hwnd:
                    self.hwnd, self.tid, self.state = hwnd, int(_k32.GetCurrentThreadId()), "ready"
                else:
                    self.state = "closed" if self._cancel else "failed"
                notify = not self._cancel
            if notify:
                self._notify()
            if not hwnd:
                return
            msg = wt.MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if not msg.hWnd and msg.message == WM_APP_DESTROY:
                    break
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
            _u32.DestroyWindow(wt.HWND(hwnd))
        except Exception:  # noqa: BLE001 - log; the window (if any) dies with this thread
            log.debug("taskbar child thread failed", exc_info=True)
            with self._lock:
                if self.state == "creating":
                    self.state = "failed"
        finally:
            with self._lock:
                if self.state == "ready":
                    self.state = "closed"
            self.exited.set()

    def _notify(self) -> None:
        try:
            if self._on_done is not None:
                self._on_done(self)
        except Exception:  # noqa: BLE001
            log.debug("taskbar child callback failed", exc_info=True)

    def close(self) -> bool:
        """Ask the thread to destroy its window and end. True when that is on its way (or done);
        False when the request could not be delivered (the caller retries later)."""
        with self._lock:
            self._cancel = True
            tid, hwnd = self.tid, self.hwnd
        if self.exited.is_set() or not tid:
            return True                 # gone, or still creating: the thread cleans up by itself
        if _u32.PostThreadMessageW(tid, WM_APP_DESTROY, 0, 0):
            return True
        if self.exited.is_set():
            return True
        log.debug("taskbar child: PostThreadMessageW failed (%s)", ctypes.get_last_error())
        _u32.ShowWindowAsync(wt.HWND(hwnd), SW_HIDE)     # at least nothing stays visible meanwhile
        return False


class TaskbarChild:
    """One click-through layered child window inside a taskbar window; frames come from the GUI thread.

    `request(parent)` starts creating it in the background; `on_ready()` is then called (from that
    thread) once it can take frames, or once creating it failed. Failures back off before the next try.
    """

    def __init__(self, on_ready=None, clock=time.monotonic):
        self._on_ready = on_ready
        self._clock = clock
        self._worker: _Worker | None = None
        self._closing: list[_Worker] = []        # asked to close, thread not gone yet
        self._undelivered: set[int] = set()      # ids of closing workers whose close request failed
        self._surface: _Surface | None = None
        self._last: bytes | None = None
        self._pos: tuple | None = None
        self._shown = False
        self._failures = 0
        self._retry_at = 0.0

    @property
    def hwnd(self) -> int:
        w = self._worker
        return w.hwnd if w is not None and w.state == "ready" else 0

    @property
    def parent(self) -> int:
        return self._worker.parent if self._worker is not None else 0

    @property
    def pending(self) -> bool:
        return self._worker is not None and self._worker.state == "creating"

    @property
    def leftover(self) -> int:
        """Child threads asked to stop that have not finished yet."""
        return len(self._closing)

    @property
    def retry_in(self) -> float:
        """Seconds until another attempt is allowed (0: now)."""
        return max(0.0, self._retry_at - self._clock())

    # ------------------------------------------------------------------ lifecycle
    def ready_for(self, parent: int) -> bool:
        """The child exists inside `parent` and can take frames."""
        w = self._worker
        if w is None or w.parent != parent:
            return False
        if w.state == "failed":
            self.note_failure()
            return False
        if w.state != "ready":
            return False
        if not _u32.IsWindow(wt.HWND(w.hwnd)):       # explorer restarted / taskbar recreated
            self._drop_worker()
            return False
        return True

    def request(self, parent: int) -> None:
        """Start creating the child in `parent` (non-blocking) unless that is under way or backing off."""
        if sys.platform != "win32" or not parent:
            return
        if self.ready_for(parent) or (self._worker is not None and self._worker.parent == parent
                                      and self._worker.state == "creating"):
            return
        if self.retry_in > 0:
            return
        self._drop_worker()
        if not _register_class():
            self.note_failure()
            return
        self._worker = _Worker(parent, self._worker_done)
        try:
            self._worker.start()
        except Exception:  # noqa: BLE001 - e.g. no more threads
            log.debug("taskbar child: thread start failed", exc_info=True)
            self._worker = None
            self.note_failure()

    def _worker_done(self, worker: _Worker) -> None:
        if worker is self._worker and self._on_ready is not None:
            self._on_ready()

    def note_failure(self) -> None:
        """Something did not work (creation, a frame): drop the child and wait before the next try."""
        self._drop_worker()
        self._failures += 1
        delay = min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** (self._failures - 1))
        self._retry_at = self._clock() + delay
        log.debug("taskbar child failed %d time(s); next try in %.0f s", self._failures, delay)

    def _drop_worker(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            self._closing.append(w)
            if not w.close():
                self._undelivered.add(id(w))
        self._release_frames()
        self.reap()

    def _release_frames(self) -> None:
        if self._surface is not None:
            self._surface.close()
        self._surface = None
        self._last = self._pos = None
        self._shown = False

    def reap(self) -> bool:
        """Forget finished workers and retry undelivered close requests; True when none is left over."""
        left = []
        for w in self._closing:
            if w.exited.is_set():
                self._undelivered.discard(id(w))
                continue
            if id(w) in self._undelivered and w.close():
                self._undelivered.discard(id(w))
            left.append(w)
        self._closing = left
        return not left

    def destroy(self, wait: float = 0.0) -> bool:
        """Destroy the child (the option was turned off, the app quits). With `wait`, give the threads up to
        that many seconds in total to finish; True when every child window is gone."""
        self._drop_worker()
        deadline = self._clock() + wait
        for w in list(self._closing):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            w.exited.wait(remaining)
        done = self.reap()
        if not done and wait:
            log.warning("taskbar child: %d window thread(s) did not stop in time", len(self._closing))
        return done

    # ------------------------------------------------------------------ frames
    def show_frame(self, parent: int, screen_pos: tuple | None, image, client_pos: tuple | None = None) -> bool:
        """Show `image` (QImage, ARGB32 premultiplied, physical px) inside taskbar `parent`.

        `screen_pos` is the top-left in physical screen px; None keeps the current spot (the taskbar
        moved, e.g. auto-hide tucked it away: the child moves with it and needs no new position).
        `client_pos` gives the spot in the taskbar's client px instead (wins over `screen_pos`).
        """
        if not self.ready_for(parent):
            return False
        hwnd = self._worker.hwnd
        w, h = image.width(), image.height()
        if w <= 0 or h <= 0:
            return False
        pos = (int(client_pos[0]), int(client_pos[1])) if client_pos is not None else None
        if pos is None and screen_pos is not None:
            pt = wt.POINT(int(screen_pos[0]), int(screen_pos[1]))
            if not _u32.ScreenToClient(wt.HWND(parent), ctypes.byref(pt)):
                return False
            pos = (pt.x, pt.y)
        data = bytes(image.constBits())[: w * h * 4]
        if data == self._last and (pos is None or pos == self._pos) and self._shown:
            return True     # nothing changed: skip the GDI work
        if self._surface is None or (self._surface.w, self._surface.h) != (w, h):
            if self._surface is not None:
                self._surface.close()
            self._surface = _Surface(w, h)
        if not self._surface.ok:
            return False
        ctypes.memmove(self._surface.bits, data, len(data))
        blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        size = wt.SIZE(w, h)
        src = wt.POINT(0, 0)
        dst = wt.POINT(*pos) if pos is not None else None
        ok = _u32.UpdateLayeredWindow(wt.HWND(hwnd), None, ctypes.byref(dst) if dst else None,
                                      ctypes.byref(size), self._surface.dc, ctypes.byref(src), 0,
                                      ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            return False
        self._last = data
        if pos is not None:
            self._pos = pos
        if not self._shown:
            _u32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOP), 0, 0, 0, 0,
                              SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self._shown = True
        self._failures = 0
        return True

    def raise_top(self) -> None:
        """Keep the child above the taskbar's own children (explorer may add or reorder some)."""
        if self.hwnd and self._shown:
            _u32.SetWindowPos(wt.HWND(self.hwnd), wt.HWND(HWND_TOP), 0, 0, 0, 0,
                              SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def hide(self) -> None:
        if self.hwnd and self._shown:
            _u32.ShowWindowAsync(wt.HWND(self.hwnd), SW_HIDE)
        self._shown = False

    @property
    def shown(self) -> bool:
        return self._shown
