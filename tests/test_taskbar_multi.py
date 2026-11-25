"""T31: multi-taskbar enumeration, per-monitor fullscreen, monitor_key, TaskbarAl (all mocked)."""
import types

import pytest

from taskbar_battery_widget import taskbar as tb
from tests._winreg_helpers import fake_winreg

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


def test_fullscreen_when_global_check_says_no_needs_borderless_window(fs):
    fs["qun"] = 5  # QUNS_ACCEPTS_NOTIFICATIONS: browser video / F11 fullscreen reports this (#9)
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is True
    fs["zoomed"] = True  # maximized borderless window over an autohide taskbar: not fullscreen
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False
    fs["zoomed"], fs["style"] = False, 0x00C00000  # titled window covering the monitor
    assert tb.is_fullscreen_on_monitor("\\\\.\\DISPLAY2") is False
    fs["style"], fs["rect"] = 0, (1920, 0, 3000, 900)  # borderless but not covering
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
    fake_winreg(monkeypatch, value=value, missing=missing, open_fails=open_fails, name="TaskbarAl")


@pytest.mark.parametrize("value,expected", [(0, False), (1, True), (2, True)])
def test_taskbar_icons_centered_values(monkeypatch, win11, value, expected):
    _fake_winreg(monkeypatch, value=value)
    assert tb.taskbar_icons_centered() is expected


@pytest.fixture(autouse=False)
def win11(monkeypatch):
    monkeypatch.setattr(tb, "_is_windows_11", lambda: True)


def test_taskbar_icons_never_centred_on_windows_10(monkeypatch):
    monkeypatch.setattr(tb, "_is_windows_11", lambda: False)
    _fake_winreg(monkeypatch, value=1)
    assert tb.taskbar_icons_centered() is False


def test_taskbar_icons_centered_missing_value_is_centred(monkeypatch, win11):
    _fake_winreg(monkeypatch, missing=True)
    assert tb.taskbar_icons_centered() is True


def test_taskbar_icons_centered_failure_is_none(monkeypatch):
    _fake_winreg(monkeypatch, open_fails=True)
    assert tb.taskbar_icons_centered() is None


# ------------------------------------------------------------------ fullscreen behind a click on another monitor
@pytest.fixture
def zorder(monkeypatch):
    """Foreground on DISPLAY1 (the user clicked there); z-order top-first in state["z"]."""
    FS, TOOL, MAXED, OTHER, CHROME_FS, FRAMELESS_MAX = 11, 12, 13, 14, 15, 16
    m = MON[SEC1][1]
    wins = {  # hwnd -> (monitor hwnd in MON, rect, style, exstyle, zoomed, cloaked)
        PRIMARY: (PRIMARY, (100, 100, 900, 700), 0x00C00000, 0, False, False),
        FS: (SEC1, MON[SEC1][1], 0x14000000, 0, False, False),                 # F11 browser on DISPLAY2
        TOOL: (SEC1, (3600, 1000, 3800, 1030), 0, 0x80, False, False),         # a tool window on top
        MAXED: (SEC1, MON[SEC1][1], 0x00C00000, 0, True, False),               # maximized titled window
        OTHER: (SEC1, (2000, 100, 2500, 500), 0x00C00000, 0, False, False),
        CHROME_FS: (SEC1, m, 0x16000000, 0, True, False),                       # maximized, exact rect
        FRAMELESS_MAX: (SEC1, (m[0] - 8, m[1] - 8, m[2] + 8, m[3] + 8), 0, 0, True, False),
    }
    state = {"z": [PRIMARY, TOOL, FS], "own": set(), "cloaked": set()}

    def nxt(h, _cmd):
        z = state["z"]
        return z[z.index(h) + 1] if h in z and z.index(h) + 1 < len(z) else None

    def pid_of(h, out):
        out._obj.value = 4242 if h in state["own"] else 1
        return 1

    user32 = types.SimpleNamespace(
        GetForegroundWindow=lambda: PRIMARY,
        IsWindowVisible=lambda h: True,
        IsIconic=lambda h: False,
        IsZoomed=lambda h: wins[h][4],
        GetWindowLongW=lambda h, i: wins[h][3] if i == -20 else wins[h][2],
        GetTopWindow=lambda _p: state["z"][0] if state["z"] else None,
        GetWindow=nxt,
        GetWindowThreadProcessId=pid_of,
    )
    monkeypatch.setattr(tb, "_user32", user32, raising=False)
    monkeypatch.setattr(tb.sys, "platform", "win32")
    monkeypatch.setattr(tb.os, "getpid", lambda: 4242)
    monkeypatch.setattr(tb, "query_user_notification_state", lambda: 5)
    monkeypatch.setattr(tb, "_class_name", lambda h: "Chrome_WidgetWin_1")
    monkeypatch.setattr(tb, "_monitor_details", lambda h: MON.get(wins[h][0]))
    monkeypatch.setattr(tb, "_win_rect", lambda h: wins[h][1])
    monkeypatch.setattr(tb, "_is_cloaked", lambda h: h in state["cloaked"])
    state.update(FS=FS, TOOL=TOOL, MAXED=MAXED, OTHER=OTHER, CHROME_FS=CHROME_FS,
                 FRAMELESS_MAX=FRAMELESS_MAX)
    return state


def test_fullscreen_stays_hidden_after_clicking_another_monitor(zorder):
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is True     # tool window on top is skipped
    assert tb.is_fullscreen_on_monitor(MON[SEC2][0]) is False    # nothing there


def test_window_on_top_of_that_monitor_decides(zorder):
    zorder["z"] = [PRIMARY, zorder["OTHER"], zorder["FS"]]            # a normal window covers part of it
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is False
    zorder["z"] = [PRIMARY, zorder["MAXED"]]                           # maximized titled: not fullscreen
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is False


def test_own_and_cloaked_windows_are_skipped(zorder):
    zorder["z"] = [PRIMARY, zorder["OTHER"], zorder["FS"]]
    zorder["own"] = {zorder["OTHER"]}                                  # e.g. the widget's own panel
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is True
    zorder["own"], zorder["cloaked"] = set(), {zorder["OTHER"]}
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is True


def test_chrome_fullscreen_stays_maximized_at_the_exact_monitor_rect(zorder):
    zorder["z"] = [PRIMARY, zorder["CHROME_FS"]]       # Prime Video in F11, seen on real hardware
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is True
    zorder["z"] = [PRIMARY, zorder["FRAMELESS_MAX"]]   # a plain maximized frameless window overhangs
    assert tb.is_fullscreen_on_monitor(MON[SEC1][0]) is False
