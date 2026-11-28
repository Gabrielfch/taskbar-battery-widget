"""Experimental "attach to taskbar": the pills go to a taskbar child window, this window is the hit area.

Two layers are tested: the widget's attach decisions in `update_position` (with a fake child, the platform
check patched so offscreen runs take the attach path) and the TaskbarChild lifecycle itself (with fake
user32 / kernel32: background creation, cancel while creating, undelivered close, back-off).
"""
import threading
import time

import pytest
from PySide6.QtGui import QGuiApplication, QImage

from taskbar_battery_widget import taskbar as tb
from taskbar_battery_widget import taskbar_child as tc
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import ScreenInfo

D1 = "\\\\.\\DISPLAY1"
SCREEN = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
TASKBAR_HWND = 0x1234


class FakeChild:
    """Stands in for TaskbarChild: `ready` says whether the window exists, `ok` whether frames work."""

    def __init__(self, ok=True, ready=True):
        self.ok = ok
        self.ready = ready
        self.frames = []
        self.requests = []
        self.hidden = 0
        self.raised = 0
        self.failures = 0
        self.destroyed = 0

    def ready_for(self, parent):
        return self.ready

    def request(self, parent):
        self.requests.append(parent)

    def show_frame(self, parent, pos, image, client_pos=None):
        self.frames.append((parent, pos, image.size().width(), image.size().height(), client_pos))
        return self.ok

    def note_failure(self):
        self.failures += 1
        self.ready = False

    def raise_top(self):
        self.raised += 1

    def hide(self):
        self.hidden += 1

    def reap(self):
        return True

    def destroy(self, wait=0.0):
        self.destroyed += 1
        self.ready = False
        return True


def _bar(autohide=False, tucked=False):
    rect = (0, 1078, 1920, 1126) if tucked else (0, 1032, 1920, 1080)
    tray = (1602, rect[1], 1920, rect[3])
    return tb.TaskbarInfo(rect=rect, tray_rect=tray, edge="bottom", autohide=autohide,
                          monitor_rect=(0, 0, 1920, 1080), device_name=D1, hwnd=TASKBAR_HWND)


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    """A widget on one taskbar with attach supported; `state` drives the taskbar and fullscreen."""
    state = {"bar": _bar(), "fullscreen": False}
    monkeypatch.setattr(w, "_attach_supported", lambda: True)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [state["bar"]])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: state["fullscreen"])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda *a, **k: state["fullscreen"])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: True)
    monkeypatch.setattr(w.taskbar, "_win_rect", lambda hwnd: (1500, 1040, 1590, 1072))
    made = []

    def make(attach=True, child=None, **cfg_values):
        cfg = Config(tmp_path / f"config{len(made)}.json")
        cfg.set("attach_to_taskbar", attach)
        for k, v in cfg_values.items():
            cfg.set(k, v)
        wd = w.TaskbarWidget(cfg)
        wd._child = child or FakeChild()
        real = QGuiApplication.primaryScreen()
        wd._screens = lambda: [(SCREEN, real)]
        wd.set_devices([Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 50, True)])
        made.append(wd)
        return wd, cfg

    yield make, state
    for wd in made:
        wd.stop()
        wd.deleteLater()


# ------------------------------------------------------------------ widget: attach decisions
def test_default_off(qapp, tmp_path):
    assert Config(tmp_path / "c.json").get("attach_to_taskbar") is False


def test_offscreen_platform_is_not_supported(qapp):
    assert not w._attach_supported()      # only the real Windows platform plugin attaches


def test_attaches_to_the_taskbar_and_moves_the_child(env):
    make, _state = env
    wd, _ = make()
    assert wd._attach_parent == TASKBAR_HWND
    parent, pos, *_ = wd._child.frames[-1]
    assert parent == TASKBAR_HWND and pos == (1500, 1040)
    assert wd._child.raised


def test_child_not_ready_yet_draws_itself_and_asks_for_it(env):
    make, _state = env
    wd, _ = make(child=FakeChild(ready=False))
    assert wd._attach_parent == 0 and wd._child.frames == []
    assert TASKBAR_HWND in wd._child.requests and wd.isVisible()


def test_ready_signal_attaches(env, qapp):
    make, _state = env
    child = FakeChild(ready=False)
    wd, _ = make(child=child)
    child.ready = True
    wd._child_ready.emit()          # what the child's thread emits once the window exists
    qapp.processEvents()
    assert wd._attach_parent == TASKBAR_HWND


def test_failed_frame_backs_off_and_draws_itself(env):
    make, _state = env
    wd, _ = make(child=FakeChild(ok=False))
    assert wd._attach_parent == 0 and wd._child.failures == 1
    wd.update_position()             # child backing off: not ready, no new frame attempt
    assert wd._child.failures == 1 and wd._attach_parent == 0


def test_turning_the_option_off_destroys_the_child(env):
    make, _state = env
    wd, cfg = make()
    assert wd._attach_parent == TASKBAR_HWND
    cfg.set("attach_to_taskbar", False)
    wd.update_position()
    assert wd._attach_parent == 0 and wd._child.destroyed >= 1


def test_stop_and_close_destroy_the_child(env):
    make, _state = env
    wd, _ = make()
    wd.stop()
    assert wd._child.destroyed == 1 and wd._attach_parent == 0
    wd2, _ = make()
    wd2.close()
    assert wd2._child.destroyed >= 1


def test_tucked_taskbar_keeps_the_child(env):
    make, state = env
    wd, _ = make()
    state["bar"] = _bar(autohide=True, tucked=True)
    wd.update_position()
    assert wd._attach_parent == TASKBAR_HWND and wd._child.hidden == 0
    assert not wd.isVisible()        # this window hides; the child slides away inside the taskbar


def test_tucked_taskbar_over_fullscreen_hides_the_child(env):
    make, state = env
    wd, _ = make()
    state["bar"] = _bar(autohide=True, tucked=True)
    state["fullscreen"] = True
    wd.update_position()
    assert wd._attach_parent == 0 and wd._child.hidden == 1


def test_fullscreen_with_visible_in_fullscreen_does_not_attach(env):
    make, state = env
    state["fullscreen"] = True
    wd, _ = make(show_in_fullscreen=True)
    assert wd.isVisible() and wd._attach_parent == 0


def test_preattach_while_tucked_places_the_child_in_the_shown_taskbar(env):
    make, state = env
    state["bar"] = _bar(autohide=True, tucked=True)
    wd, _ = make()
    assert wd._attach_parent == TASKBAR_HWND and not wd.isVisible()
    _parent, pos, _w, _h, client = wd._child.frames[-1]
    assert pos is None and client is not None
    assert 0 <= client[1] < 48       # inside the (shown) taskbar's client area


def test_preattach_not_over_fullscreen(env):
    make, state = env
    state["bar"] = _bar(autohide=True, tucked=True)
    state["fullscreen"] = True
    wd, _ = make()
    assert wd._attach_parent == 0 and wd._child.frames == []


def test_render_image_matches_widget_size(env):
    make, _state = env
    wd, _ = make(attach=False)
    img = wd._render_image()
    dpr = wd.devicePixelRatioF() or 1.0
    assert (img.width(), img.height()) == (round(wd.width() * dpr), round(wd.height() * dpr))
    assert img.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))


def test_paint_while_attached_sends_frame_without_moving(env):
    make, _state = env
    wd, _ = make()
    n = len(wd._child.frames)
    wd.paintEvent(None)
    assert len(wd._child.frames) == n + 1 and wd._child.frames[-1][1] is None


def test_failed_repaint_falls_back(env, qapp):
    make, _state = env
    wd, _ = make()
    wd._child.ok = False
    wd.paintEvent(None)
    qapp.processEvents()
    assert wd._attach_parent == 0 and wd._child.failures == 1


def test_untuck_offset_puts_the_bar_flush_with_its_edge():
    mon = (0, 0, 1920, 1080)
    assert tb.untuck_offset((0, 1078, 1920, 1126), mon, tb.EDGE_BOTTOM) == (0, -46)
    assert tb.untuck_offset((0, -46, 1920, 2), mon, tb.EDGE_TOP) == (0, 46)
    assert tb.untuck_offset((-46, 0, 2, 1080), mon, tb.EDGE_LEFT) == (46, 0)
    assert tb.untuck_offset((1918, 0, 1966, 1080), mon, tb.EDGE_RIGHT) == (-46, 0)
    assert tb.shift_rect((1, 2, 3, 4), 10, -1) == (11, 1, 13, 3)


# ------------------------------------------------------------------ TaskbarChild lifecycle (fake Win32)
class FakeWin32:
    """CreateWindowExW / message loop / PostThreadMessageW, enough for the child's worker threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.windows = set()
        self.created = []
        self.destroyed = []
        self.queues = {}                   # tid -> list of posted messages
        self.create_gate = threading.Event()
        self.create_gate.set()
        self.create_ok = True
        self.post_ok = True
        self._next = 100

    # user32
    def RegisterClassW(self, _wc):
        return 1

    def CreateWindowExW(self, *_a):
        self.create_gate.wait(5)
        with self.lock:
            if not self.create_ok:
                return 0
            self._next += 1
            self.windows.add(self._next)
            self.created.append(self._next)
            self.queues.setdefault(threading.get_ident(), [])
            return self._next

    def DestroyWindow(self, hwnd):
        with self.lock:
            h = int(hwnd.value or 0)
            self.windows.discard(h)
            self.destroyed.append(h)
        return True

    def IsWindow(self, hwnd):
        return int(hwnd.value or 0) in self.windows

    def GetMessageW(self, msg_p, *_a):
        tid = threading.get_ident()
        while True:
            with self.lock:
                q = self.queues.get(tid)
                if q:
                    m = q.pop(0)
                    msg_p._obj.hWnd = None
                    msg_p._obj.message = m
                    return 1
            time.sleep(0.002)

    def TranslateMessage(self, _m):
        return 0

    def DispatchMessageW(self, _m):
        return 0

    def PostThreadMessageW(self, tid, msg, _w, _l):
        with self.lock:
            if not self.post_ok or tid not in self.queues:
                return 0
            self.queues[tid].append(msg)
            return 1

    def ShowWindowAsync(self, *_a):
        return True

    # kernel32
    def GetModuleHandleW(self, _n):
        return 0

    def GetCurrentThreadId(self):
        return threading.get_ident()


@pytest.fixture
def win(monkeypatch):
    fake = FakeWin32()
    monkeypatch.setattr(tc.sys, "platform", "win32")
    monkeypatch.setattr(tc, "_u32", fake, raising=False)
    monkeypatch.setattr(tc, "_k32", fake, raising=False)
    monkeypatch.setattr(tc, "_class_atom", 1)
    return fake


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_request_creates_in_the_background_and_signals_ready(win):
    ready = threading.Event()
    child = tc.TaskbarChild(on_ready=ready.set)
    win.create_gate.clear()                  # explorer is slow
    t0 = time.monotonic()
    child.request(TASKBAR_HWND)
    assert time.monotonic() - t0 < 0.5      # did not wait for the window
    assert child.pending and not child.ready_for(TASKBAR_HWND)
    win.create_gate.set()
    assert ready.wait(3) and child.ready_for(TASKBAR_HWND) and child.hwnd in win.windows
    assert child.destroy(wait=2.0) and win.windows == set()


def test_destroy_while_creating_leaves_no_orphan(win):
    child = tc.TaskbarChild()
    win.create_gate.clear()
    child.request(TASKBAR_HWND)
    child.destroy()                          # before CreateWindowExW returned
    win.create_gate.set()
    assert _wait(lambda: win.created and win.windows == set())
    assert _wait(child.reap)


def test_new_taskbar_replaces_the_old_child(win):
    child = tc.TaskbarChild()
    child.request(TASKBAR_HWND)
    assert _wait(lambda: child.ready_for(TASKBAR_HWND))
    first = child.hwnd
    child.request(0x9999)                    # moved to another monitor's taskbar
    assert _wait(lambda: child.ready_for(0x9999))
    assert _wait(lambda: first not in win.windows)
    child.destroy(wait=2.0)


def test_undelivered_close_is_retried(win):
    child = tc.TaskbarChild()
    child.request(TASKBAR_HWND)
    assert _wait(lambda: child.ready_for(TASKBAR_HWND))
    hwnd = child.hwnd
    win.post_ok = False
    assert child.destroy() is False and child.leftover == 1     # reported, not forgotten
    assert hwnd in win.windows
    win.post_ok = True
    assert _wait(child.reap) and hwnd not in win.windows


def test_destroy_with_wait_reports_a_stuck_thread(win):
    child = tc.TaskbarChild()
    child.request(TASKBAR_HWND)
    assert _wait(lambda: child.ready_for(TASKBAR_HWND))
    win.post_ok = False
    t0 = time.monotonic()
    assert child.destroy(wait=0.2) is False
    assert time.monotonic() - t0 < 1.0
    win.post_ok = True
    assert _wait(child.reap)


def test_failed_creation_backs_off_exponentially(win):
    now = [1000.0]
    calls = []
    child = tc.TaskbarChild(on_ready=lambda: calls.append(1), clock=lambda: now[0])
    win.create_ok = False
    child.request(TASKBAR_HWND)
    assert _wait(lambda: calls)                         # told that the attempt finished (failed)
    assert not child.ready_for(TASKBAR_HWND)            # notices the failure
    assert child.retry_in == pytest.approx(tc.BACKOFF_FIRST_S)
    child.request(TASKBAR_HWND)                         # still backing off: no new attempt
    assert len(win.created) == 0 and child.parent == 0
    now[0] += tc.BACKOFF_FIRST_S
    child.request(TASKBAR_HWND)
    assert _wait(lambda: len(calls) == 2)
    assert not child.ready_for(TASKBAR_HWND)
    assert child.retry_in == pytest.approx(2 * tc.BACKOFF_FIRST_S)
    for _ in range(12):
        child.note_failure()
    assert child.retry_in == pytest.approx(tc.BACKOFF_MAX_S)


def test_window_gone_with_explorer_is_dropped(win):
    child = tc.TaskbarChild()
    child.request(TASKBAR_HWND)
    assert _wait(lambda: child.ready_for(TASKBAR_HWND))
    win.windows.clear()                                 # explorer restarted: the taskbar and child are gone
    assert not child.ready_for(TASKBAR_HWND) and child.parent == 0
    assert child.retry_in == 0                          # not a failure: a new attempt may start at once
    child.destroy(wait=2.0)
