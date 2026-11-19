"""Stack devices: one device per row when floating at a corner (offscreen Qt)."""
import pytest
from PySide6.QtGui import QGuiApplication

from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import DEFAULTS, Config
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.placement import ScreenInfo
from taskbar_battery_widget.settings_model import SettingsCallbacks, Toggle, build_settings_model
from taskbar_battery_widget.tray import TrayController

P = ScreenInfo("aaa", "\\\\.\\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)
DEVS = [Device("a", "Mouse", "mouse", 80, True), Device("b", "Pad", "gamepad", 12, True),
        Device("c", "Keyboard long", "keyboard", 100, True), Device("d", "Pen", "mouse", 5, True)]


def make(tmp_path, monkeypatch, mode="floating", anchor="bottom-right", stack=True, style="transparent", devs=DEVS):
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: False)
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: False)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [])
    cfg = Config(tmp_path / "c.json")
    cfg.set("animations", "off")
    cfg.set("placement_mode", mode)
    cfg.set("stack_devices", stack)
    cfg.set("pill_style", style)
    cfg.set("floating_positions", {"aaa": {"anchor": anchor, "dx": 0, "dy": 0}})
    wd = w.TaskbarWidget(cfg)
    wd._screens = lambda: [(P, QGuiApplication.primaryScreen())]
    wd.set_devices(list(devs))
    wd.update_position()
    return wd, cfg


def test_config_default_and_validation(tmp_path):
    assert DEFAULTS["stack_devices"] is False
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("stack_devices") is False
    cfg.set("stack_devices", True)
    assert cfg.get("stack_devices") is True
    for bad in ("yes", 1, None):
        with pytest.raises(ValueError):
            cfg.set("stack_devices", bad)


def test_toggle_in_position_page_applies_without_rescan(tmp_path):
    cfg = Config(tmp_path / "c.json")
    calls = []
    cb = SettingsCallbacks(set_bool=lambda k, v: calls.append((k, v)), set_threshold=lambda v: None,
                           set_palette=lambda v: None, set_startup=lambda v: None, open_device_log=lambda: None,
                           open_logs_folder=lambda: None, startup_enabled=lambda: False)
    t = build_settings_model(cfg, cb).find("stack_devices")
    assert isinstance(t, Toggle)
    t.set(True)
    assert calls == [("stack_devices", True)]
    tray = TrayController.__new__(TrayController)       # only _set_bool is exercised
    tray._cfg = cfg
    seen = []

    class Sig:
        def __init__(self, n):
            self.n = n

        def emit(self):
            seen.append(self.n)

    tray.ui_settings_changed, tray.settings_changed = Sig("ui"), Sig("rescan")
    TrayController._set_bool(tray, "stack_devices", True)
    assert seen == ["ui"] and cfg.get("stack_devices") is True


def test_not_stacked_on_the_taskbar(qapp, tmp_path, monkeypatch):
    wd, _ = make(tmp_path, monkeypatch, "taskbar", "bottom-right")
    assert not wd._vertical
    assert wd.width() > wd.height()


@pytest.mark.parametrize("anchor", ["top-center", "middle-left", "middle-right", "bottom-center"])
def test_stacked_at_any_floating_edge_position(qapp, tmp_path, monkeypatch, anchor):
    wd, _ = make(tmp_path, monkeypatch, anchor=anchor)
    assert wd._vertical and len({r[2] for r in wd._rects}) == 1


@pytest.mark.parametrize("anchor", ["top-left", "top-right", "bottom-left", "bottom-right"])
def test_stacked_at_corners_equal_widths_and_anchored(qapp, tmp_path, monkeypatch, anchor):
    wd, _ = make(tmp_path, monkeypatch, anchor=anchor)
    assert wd._vertical and len(wd._rects) == len(DEVS)
    assert len({r[2] for r in wd._rects}) == 1                  # every row as wide as the widest
    assert [r[0] for r in wd._rects] == [wd._rects[0][0]] * len(DEVS)
    assert wd.height() > wd.width() or len(DEVS) > 2
    x, y = wd.pos().x(), wd.pos().y()
    avail = P.available
    assert x == (avail[0] if anchor.endswith("left") else avail[0] + avail[2] - wd.width())
    # a bottom corner grows upward (bottom edge fixed), a top corner downward (top edge fixed)
    assert (y == avail[1]) if anchor.startswith("top") else (y + wd.height() == avail[1] + avail[3])


def test_toggle_live_and_back_to_row(qapp, tmp_path, monkeypatch):
    wd, cfg = make(tmp_path, monkeypatch, stack=False)
    assert not wd._vertical
    cfg.set("stack_devices", True)
    wd.refresh_style()
    assert wd._vertical
    cfg.set("stack_devices", False)
    wd.refresh_style()
    assert not wd._vertical


def test_overflow_plus_n_when_column_too_tall(qapp, tmp_path, monkeypatch):
    devs = [Device(str(i), f"D{i}", "mouse", 50, True) for i in range(60)]
    wd, _ = make(tmp_path, monkeypatch, devs=devs)
    assert wd._pills[-1].kind == "overflow"
    assert wd.height() <= P.available[3]
    assert wd._pills[-1].text == f"+{60 - (len(wd._pills) - 1)}"


def test_separators_are_horizontal_lines_in_transparent(qapp, tmp_path, monkeypatch):
    wd, _ = make(tmp_path, monkeypatch, style="transparent")
    assert wd.pill_style() == "transparent" and wd._vertical       # _draw_separators draws between rows
    rects = wd._rects
    assert rects[1][1] > rects[0][1] and rects[1][0] == rects[0][0]
    wd2, _ = make(tmp_path, monkeypatch, style="compact")
    assert wd2._vertical and wd2.pill_style() == "compact"
