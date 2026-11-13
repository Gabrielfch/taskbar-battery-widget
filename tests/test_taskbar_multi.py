"""T31: multi-taskbar enumeration, per-monitor fullscreen, monitor_key, TaskbarAl (all mocked)."""
import sys
import types

import pytest

from taskbar_battery_widget import taskbar as tb

# exercises get_taskbar_infos() itself (Win32 is mocked inside each test), so skip the global stub
pytestmark = pytest.mark.live_taskbar

PRIMARY, SEC1, SEC2 = 100, 200, 300
RECTS = {
    PRIMARY: (0, 1032, 1920, 1080),
    SEC1: (1920, 1032, 3840, 1080),
    SEC2: (-1920, 1032, 0, 1080),
}
TRAY_PRIMARY = (1602, 1032, 1920, 1080)
MON = {  # hwnd -> (device, monitor, work)
    PRIMARY: ("\\\\.\\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032)),
    SEC1: ("\\\\.\\DISPLAY2", (1920, 0, 3840, 1080), (1920, 0, 3840, 1032)),
    SEC2: ("\\\\.\\DISPLAY3", (-1920, 0, 0, 1080), (-1920, 0, 0, 1032)),
}
CLOCKS = {SEC1: (3700, 1032, 3840, 1080)}  # SEC2 has no clock child -> fallback


@pytest.fixture
def fake_win(monkeypatch):
    secondaries = [SEC1, SEC2]

    def find_window(cls, title):
        return PRIMARY if cls == "Shell_TrayWnd" else None

    def find_ex(parent, prev, cls, title):
        if cls == "Shell_SecondaryTrayWnd" and parent is None:
            if prev is None:
                return secondaries[0] if secondaries else None
            i = secondaries.index(prev) + 1
            return secondaries[i] if i < len(secondaries) else None
        if cls == "TrayNotifyWnd" and parent == PRIMARY:
            return 1
        if cls == "ClockButton" and parent in CLOCKS:
            return parent + 1
        return None

    user32 = types.SimpleNamespace(FindWindowW=find_window, FindWindowExW=find_ex)
    monkeypatch.setattr(tb, "_user32", user32, raising=False)

    def win_rect(h):
        if h == 1:
            return TRAY_PRIMARY
        if h in (SEC1 + 1,):
            return CLOCKS[SEC1]
        return RECTS.get(h)

    monkeypatch.setattr(tb, "_win_rect", win_rect)
    monkeypatch.setattr(tb, "_monitor_details", lambda h: MON.get(h))
    monkeypatch.setattr(tb, "_appbar", lambda msg: types.SimpleNamespace(uEdge=3))
    monkeypatch.setattr(tb, "_autohide_state", lambda: False)
    monkeypatch.setattr(tb.sys, "platform", "win32")
    return secondaries


def test_infos_order_and_fields(fake_win):
    infos = tb.get_taskbar_infos()
    assert [i.kind for i in infos] == ["primary", "secondary", "secondary"]
    assert [i.hwnd for i in infos] == [PRIMARY, SEC1, SEC2]
    assert [i.has_tray for i in infos] == [True, False, False]
    p, s1, s2 = infos
    assert p.tray_rect == TRAY_PRIMARY and p.edge == "bottom" and not p.autohide
    assert p.rect == RECTS[PRIMARY] and p.monitor_rect == MON[PRIMARY][1]
    assert p.monitor_work == MON[PRIMARY][2]


def test_device_name_mapping(fake_win):
    infos = tb.get_taskbar_infos()
    assert [i.device_name for i in infos] == ["\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2", "\\\\.\\DISPLAY3"]
    assert infos[1].monitor_rect == (1920, 0, 3840, 1080)
    assert infos[2].monitor_work == (-1920, 0, 0, 1032)


def test_secondary_tray_uses_clock_child_else_right_end(fake_win):
    _, s1, s2 = tb.get_taskbar_infos()
    assert s1.tray_rect == CLOCKS[SEC1]
    w = tb.FALLBACK_SECONDARY_TRAY_WIDTH
    assert s2.tray_rect == (0 - w, 1032, 0, 1080)


def test_secondary_edge_derived_not_from_primary_appbar(fake_win, monkeypatch):
    monkeypatch.setattr(tb, "_appbar", lambda msg: types.SimpleNamespace(uEdge=1))  # primary on top
    infos = tb.get_taskbar_infos()
    assert infos[0].edge == "top"
    assert infos[1].edge == "bottom"  # derived from its own rect


def test_no_primary_still_lists_secondaries(fake_win, monkeypatch):
    monkeypatch.setattr(tb._user32, "FindWindowW", lambda c, t: None)
    assert [i.kind for i in tb.get_taskbar_infos()] == ["secondary", "secondary"]


@pytest.mark.live_taskbar
def test_wrapper_returns_primary_unchanged(fake_win):
    one = tb.get_taskbar_info()
    assert one is not None and one.kind == "primary"
    assert (one.rect, one.tray_rect, one.edge, one.autohide, one.monitor_rect) == (
        RECTS[PRIMARY], TRAY_PRIMARY, "bottom", False, MON[PRIMARY][1])


@pytest.mark.live_taskbar
def test_wrapper_none_when_empty(fake_win, monkeypatch):
    monkeypatch.setattr(tb, "get_taskbar_infos", lambda: [])
    assert tb.get_taskbar_info() is None


def test_win32_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("x")
    monkeypatch.setattr(tb.sys, "platform", "win32")
    monkeypatch.setattr(tb, "_user32", types.SimpleNamespace(FindWindowW=boom, FindWindowExW=boom),
                        raising=False)
    assert tb.get_taskbar_infos() == []


def test_infos_empty_off_windows(monkeypatch):
    monkeypatch.setattr(tb.sys, "platform", "linux")
    assert tb.get_taskbar_infos() == []


def test_taskbar_info_positional_compat():
    i = tb.TaskbarInfo((0, 0, 1, 1), (0, 0, 1, 1), "bottom", False, (0, 0, 2, 2))
    assert i.kind == "primary" and i.has_tray and i.device_name == "" and i.hwnd == 0


# ------------------------------------------------------------------ per-monitor fullscreen
@pytest.fixture
def fs(monkeypatch):
    state = {"qun": 3, "fg": SEC1, "rect": MON[SEC1][1], "cls": "Chrome_WidgetWin_1",
             "visible": True, "iconic": False, "zoomed": False, "style": 0}
    user32 = types.SimpleNamespace(
        GetForegroundWindow=lambda: state["fg"],
        IsWindowVisible=lambda h: state["visible"],
        IsIconic=lambda h: state["iconic"],
        IsZoomed=lambda h: state["zoomed"],
        GetWindowLongW=lambda h, i: state["style"],
    )
    monkeypatch.setattr(tb, "_user32", user32, raising=False)
    monkeypatch.setattr(tb.sys, "platform", "win32")
    monkeypatch.setattr(tb, "query_user_notification_state", lambda: state["qun"])
    monkeypatch.setattr(tb, "_class_name", lambda h: state["cls"])
    monkeypatch.setattr(tb, "_monitor_details", lambda h: MON.get(h))
    monkeypatch.setattr(tb, "_win_rect", lambda h: state["rect"])
    return state


def test_fullscreen_true_on_foreground_monitor(fs):
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is True


def test_fullscreen_false_on_other_monitor(fs):
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY1") is False


def test_fullscreen_false_when_global_check_says_no(fs):
    fs["qun"] = 5  # QUNS_ACCEPTS_NOTIFICATIONS
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False


def test_fullscreen_false_when_window_does_not_cover(fs):
    fs["rect"] = (1920, 0, 3000, 900)
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False


def test_fullscreen_false_for_shell_windows_and_hidden(fs):
    fs["cls"] = "Progman"
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False
    fs["cls"], fs["iconic"] = "x", True
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False


def test_fullscreen_unknown_state_falls_back_to_cover_check(fs):
    fs["qun"] = None
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is True
    fs["zoomed"], fs["style"] = True, 0x00C00000  # titled maximized window
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False


def test_fullscreen_safe_defaults(fs, monkeypatch):
    assert tb.is_fullscreen_on_monitor("") is False
    monkeypatch.setattr(tb, "_class_name", lambda h: (_ for _ in ()).throw(OSError()))
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False


def test_is_fullscreen_foreground_still_exported():
    assert callable(tb.is_fullscreen_foreground) and callable(tb._covers_fallback)


# ------------------------------------------------------------------ monitor_key
def test_monitor_key_stable_and_shape():
    a = tb.monitor_key("DEL", "U2720Q", "SER123", "DISPLAY1", set())
    b = tb.monitor_key("DEL", "U2720Q", "SER123", "OTHER-NAME", set())
    assert a == b and len(a) == 12 and int(a, 16) >= 0


def test_monitor_key_case_and_space_insensitive():
    assert (tb.monitor_key(" del", "u2720q ", "ser123", "", set())
            == tb.monitor_key("DEL", "U2720Q", "SER123", "", set()))


def test_monitor_key_no_raw_serial():
    key = tb.monitor_key("DEL", "U2720Q", "ABCDEF123456", "n", set())
    assert "ABCDEF" not in key and "abcdef123456" not in key.lower()


def test_monitor_key_duplicates_get_suffix():
    k1 = tb.monitor_key("DEL", "M", "S", "n", set())
    k2 = tb.monitor_key("DEL", "M", "S", "n", {k1})
    k3 = tb.monitor_key("DEL", "M", "S", "n", {k1, k2})
    assert k2 == k1 + "#2" and k3 == k1 + "#3"


def test_monitor_key_falls_back_to_name_hash():
    a = tb.monitor_key("", "", "", "\\\\.\\DISPLAY1", set())
    b = tb.monitor_key("", "", "", "\\\\.\\DISPLAY2", set())
    assert a != b and len(a) == 12
    assert a == tb.monitor_key("", "", "", "\\\\.\\DISPLAY1", set())
    assert a != tb.monitor_key("DEL", "", "", "\\\\.\\DISPLAY1", set())


# ------------------------------------------------------------------ TaskbarAl
def _fake_winreg(monkeypatch, value=None, missing=False, open_fails=False):
    class _K:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def open_key(root, path):
        if open_fails:
            raise OSError("denied")
        return _K()

    def query(k, name):
        assert name == "TaskbarAl"
        if missing:
            raise FileNotFoundError
        return (value, 4)

    fake = types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=open_key, QueryValueEx=query)
    monkeypatch.setitem(sys.modules, "winreg", fake)


@pytest.mark.parametrize("value,expected", [(0, False), (1, True), (2, True)])
def test_taskbar_icons_centered_values(monkeypatch, value, expected):
    _fake_winreg(monkeypatch, value=value)
    assert tb.taskbar_icons_centered() is expected


def test_taskbar_icons_centered_missing_value_is_centred(monkeypatch):
    _fake_winreg(monkeypatch, missing=True)
    assert tb.taskbar_icons_centered() is True


def test_taskbar_icons_centered_failure_is_none(monkeypatch):
    _fake_winreg(monkeypatch, open_fails=True)
    assert tb.taskbar_icons_centered() is None
