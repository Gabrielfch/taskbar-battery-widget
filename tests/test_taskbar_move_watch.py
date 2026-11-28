"""_TaskbarMoveWatch: hooks explorer's process once, re-hooks when explorer restarts, filters events."""
import pytest

from taskbar_battery_widget import win_window as ww


class FakeUser32:
    def __init__(self):
        self.hooks = []          # (pid, proc) per successful SetWinEventHook call
        self.unhooked = []
        self.classes = {}
        self.class_lookups = 0
        self.fail = False

    def SetWinEventHook(self, ev_min, ev_max, _mod, proc, pid, tid, flags):
        assert ev_min == ev_max == ww.EVENT_OBJECT_LOCATIONCHANGE and tid == 0
        assert flags == ww.WINEVENT_OUTOFCONTEXT
        if self.fail:
            return 0
        self.hooks.append((pid, proc))
        return 1000 + len(self.hooks)

    def UnhookWinEvent(self, hook):
        self.unhooked.append(hook)
        return True

    def GetClassNameW(self, hwnd, buf, size):
        self.class_lookups += 1
        name = self.classes.get(int(hwnd.value or 0), "")
        buf.value = name
        return len(name)


@pytest.fixture
def env(monkeypatch):
    u32 = FakeUser32()
    pid = {"v": 42}
    monkeypatch.setattr(ww.sys, "platform", "win32")
    monkeypatch.setattr(ww, "_u32", u32, raising=False)
    monkeypatch.setattr(ww, "_taskbar_pid", lambda: pid["v"])
    monkeypatch.setattr(ww, "_win_event_proto", lambda: (lambda fn: fn))
    moves = []
    watch = ww._TaskbarMoveWatch(lambda: moves.append(1))
    return watch, u32, pid, moves


def _fire(u32, hwnd, idobj=None):
    u32.hooks[-1][1](None, ww.EVENT_OBJECT_LOCATIONCHANGE, hwnd,
                     ww.OBJID_WINDOW if idobj is None else idobj, 0, 0, 0)


def test_refresh_hooks_explorer_once(env):
    watch, u32, _pid, _moves = env
    for _ in range(3):
        watch.refresh()
    assert [p for p, _ in u32.hooks] == [42]
    assert u32.unhooked == []


def test_refresh_rehooks_when_explorer_restarts(env):
    watch, u32, pid, _moves = env
    watch.refresh()
    pid["v"] = 77                    # explorer restarted with a new pid
    watch.refresh()
    assert [p for p, _ in u32.hooks] == [42, 77]
    assert u32.unhooked == [1001]


def test_no_explorer_means_no_hook_until_it_appears(env):
    watch, u32, pid, _moves = env
    pid["v"] = 0
    watch.refresh()
    assert u32.hooks == []
    pid["v"] = 9
    watch.refresh()
    assert [p for p, _ in u32.hooks] == [9]


def test_failed_hook_retries_on_next_refresh(env):
    watch, u32, _pid, _moves = env
    u32.fail = True
    watch.refresh()
    u32.fail = False
    watch.refresh()
    assert [p for p, _ in u32.hooks] == [42]


def test_stop_unhooks_once(env):
    watch, u32, _pid, _moves = env
    watch.refresh()
    watch.stop()
    watch.stop()
    assert u32.unhooked == [1001]


def test_events_only_for_taskbar_windows(env):
    watch, u32, _pid, moves = env
    u32.classes = {1: "Shell_TrayWnd", 2: "Shell_SecondaryTrayWnd", 3: "CabinetWClass"}
    watch.refresh()
    _fire(u32, 1)
    _fire(u32, 2)
    _fire(u32, 3)                    # an Explorer folder window
    _fire(u32, 1, idobj=-4)          # a child object of the taskbar, not the window
    _fire(u32, 0)
    assert len(moves) == 2


def test_taskbar_hits_cached_misses_not(env):
    watch, u32, _pid, moves = env
    u32.classes = {1: "Shell_TrayWnd"}
    watch.refresh()
    for _ in range(3):
        _fire(u32, 1)
    assert u32.class_lookups == 1                    # the hit is cached
    _fire(u32, 7)                                    # unknown window: a miss
    u32.classes[7] = "Shell_SecondaryTrayWnd"        # handle reused by a new taskbar (monitor plugged in)
    _fire(u32, 7)
    assert len(moves) == 4


def test_callback_errors_are_swallowed(env):
    watch, u32, _pid, _moves = env
    u32.classes = {1: "Shell_TrayWnd"}
    watch._on_move = lambda: 1 / 0
    watch.refresh()
    _fire(u32, 1)                    # must not raise into the WinEvent callback
