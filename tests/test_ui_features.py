import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from taskbar_battery_widget import widget as w
from taskbar_battery_widget.colors import battery_color, contrast_ratio, level_band
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.popup import (HoverPopup, build_popup_content, format_updated, place_popup,
                             status_text)
from taskbar_battery_widget.tray import TrayController


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _en():
    set_language("en")
    yield


class Cfg:
    def __init__(self, **kw):
        self.d = {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False,
                  "hidden_devices": [], "color_mode": "levels"}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


# ---------------------------------------------------------------- B: colours
@pytest.mark.parametrize("level,charging,expected", [
    (100, False, "green"), (60, False, "green"), (59, False, "amber"), (30, False, "amber"),
    (29, False, "orange"), (21, False, "orange"), (20, False, "red"), (5, False, "red"),
    (5, True, "green"), (None, False, "grey"), (None, True, "grey"),
])
def test_level_bands(level, charging, expected):
    assert level_band(level, charging, 20) == expected


def test_threshold_moves_red_band():
    assert level_band(25, False, 25) == "red"
    assert level_band(26, False, 25) == "orange"
    assert level_band(30, False, 30) == "red"       # threshold above the orange band


def test_colors_are_distinct_and_readable():
    for dark, bg in ((True, QColor(32, 32, 32)), (False, QColor(243, 243, 243))):
        cols = {b: battery_color(lv, False, 20, dark) for b, lv in
                (("green", 95), ("amber", 45), ("orange", 25), ("red", 8))}
        assert len({c.name() for c in cols.values()}) == 4
        for c in cols.values():
            assert contrast_ratio(c, bg) >= 3.0, (dark, c.name())
        assert contrast_ratio(battery_color(None, False, 20, dark), bg) >= 3.0


def _render(wd) -> QImage:
    pm = wd.grab()
    img = QImage(pm.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(32, 32, 32))
    p = QPainter(img)
    p.drawPixmap(0, 0, pm)
    p.end()
    return img


def _bar_pixel(wd, i, expected=None):
    """Colour of pill ``i``'s level bar. Scans the whole bar row instead of a fixed offset so the
    test does not depend on font metrics (pill width differs between machines / CI runners)."""
    x, y, pw, h = wd._rects[i]
    img = _render(wd)
    row = int(y + h - 5 + 1)
    colours = [QColor(img.pixel(px, row)) for px in range(int(x + w.PAD), int(x + pw - w.PAD))]
    if expected is not None and colours:
        # Short bars on narrow pills are mostly anti-aliased rounded ends: take the closest pixel and
        # accept it when it is near the expected colour.
        want = QColor(expected)

        def dist(c):
            return max(abs(c.red() - want.red()), abs(c.green() - want.green()), abs(c.blue() - want.blue()))

        best = min(colours, key=dist)
        return want if dist(best) <= 48 else best
    return colours[min(2, len(colours) - 1)] if colours else QColor()


def test_pills_render_distinct_colors(qapp, monkeypatch):
    # Without this the widget measures the machine's real taskbar (CI runners have one) and may
    # collapse pills into a "+N" overflow pill, so pill i would no longer be device i.
    from taskbar_battery_widget import taskbar
    monkeypatch.setattr(taskbar, "get_taskbar_info", lambda: None)
    wd = w.TaskbarWidget(Cfg())
    wd._max_extent = None
    wd._light = False
    wd.set_devices([Device("a", "A", "keyboard", 95, True), Device("b", "B", "mouse", 45, True),
                    Device("c", "C", "headset", 25, True), Device("d", "D", "mouse", 8, True)])
    seen = set()
    for i, lvl in enumerate((95, 45, 25, 8)):
        want = battery_color(lvl, False, 20, True).name()
        px = _bar_pixel(wd, i, want)
        assert px.name() == want, f"pill {i} rect={wd._rects[i]} closest={px.name()}"
        seen.add(px.name())
    assert len(seen) == 4
    wd._cfg = Cfg(color_mode="mono")
    wd._rebuild()
    assert wd.pill_color(wd._pills[0]) is None


# ---------------------------------------------------------------- C: visible devices
def test_config_hidden_devices_validation(tmp_path):
    c = Config(tmp_path / "c.json")
    assert c.get("hidden_devices") == [] and c.get("color_mode") == "levels"
    ka, kb = visibility_key("a"), visibility_key("b")
    c.set("hidden_devices", [ka, kb])
    assert Config(tmp_path / "c.json").get("hidden_devices") == [ka, kb]
    for bad in ("x", [1], [ka] * 201, [""], None, {"a": 1}):
        with pytest.raises(ValueError):
            c.set("hidden_devices", bad)
    with pytest.raises(ValueError):
        c.set("color_mode", "rainbow")
    c.get("hidden_devices").append("zzz")          # get() hands out a copy
    assert c.get("hidden_devices") == [ka, kb]
    assert Config(tmp_path / "c.json")._data is not Config(tmp_path / "c.json")._data


def test_config_invalid_hidden_in_file_falls_back(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"hidden_devices": [1, 2], "color_mode": 5}', encoding="utf-8")
    c = Config(p)
    assert c.get("hidden_devices") == [] and c.get("color_mode") == "levels"


def test_widget_filters_hidden(qapp):
    devs = [Device("a", "A", "keyboard", 80, True), Device("b", "B", "mouse", 50, True)]
    wd = w.TaskbarWidget(Cfg(hidden_devices=[visibility_key("a")]))
    wd.set_devices(devs)
    assert wd.pill_texts() == ["50%"]
    wd._cfg = Cfg()
    wd._rebuild()
    assert wd.pill_texts() == ["80%", "50%"]


def test_tray_has_no_show_submenu(qapp, tmp_path):
    set_language("en")
    tray = TrayController(Config(tmp_path / "c.json"))
    assert not hasattr(tray, "_visible_menu")
    assert all(a.text() != "Show" for a in tray.menu.actions())
    assert tray.menu.actions()[0].text() == "Refresh"


def test_tray_panel_eye_persists_and_show_all(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    changes, real = [], []
    tray.ui_settings_changed.connect(lambda: changes.append(1))
    tray.settings_changed.connect(lambda: real.append(1))
    tray.set_devices([Device("a", "Mouse & Co <b>", "mouse", 80, True),
                      Device("b", "Gone", "keyboard", None, False)])
    tray.open_panel()
    p = tray._panel
    assert p.isVisible()
    assert len(p._model.connected) == 1 and len(p._model.known) == 1
    p.visibility_toggled.emit(visibility_key("b"), False)
    assert cfg.get("hidden_devices") == [visibility_key("b")] and changes and not real
    assert [r.visible for r in p._model.known] == [False]          # panel refreshed
    p.show_all_requested.emit()
    assert cfg.get("hidden_devices") == []
    assert [r.visible for r in p._model.known] == [True]
    p.show_disconnected_toggled.emit(True)
    assert cfg.get("show_disconnected") is True and p._model.show_disconnected
    p.close()


def test_tray_left_and_right_click_toggle_panel(qapp, tmp_path):
    from PySide6.QtWidgets import QSystemTrayIcon
    R = QSystemTrayIcon.ActivationReason
    tray = TrayController(Config(tmp_path / "c.json"))
    tray.set_devices([Device("a", "A", "mouse", 80, True)])
    assert not hasattr(tray, "_act_devices")
    tray._on_activated(R.DoubleClick)
    assert tray._panel is None or not tray._panel.isVisible()
    tray._on_activated(R.Context)           # right click opens the panel
    assert tray._panel.isVisible()
    tray._on_activated(R.Context)           # a second one closes it
    assert not tray._panel.isVisible()
    tray._panel.hidden_at = None
    tray._on_activated(R.Trigger)
    assert tray._panel.isVisible()
    tray._panel.close()
    assert not tray._panel.isVisible()


def test_tray_panel_follows_device_changes(qapp, tmp_path):
    tray = TrayController(Config(tmp_path / "c.json"))
    tray.set_devices([Device("a", "A", "mouse", 80, True)])
    tray.open_panel()
    assert [r.battery for r in tray._panel._model.connected] == [80]
    tray.set_devices([Device("a", "A", "mouse", 55, True)])
    assert [r.battery for r in tray._panel._model.connected] == [55]
    tray._panel.close()


def test_tray_toggles_persist(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    tray._act_wireless.setChecked(False)
    tray._act_colors.setChecked(False)
    assert cfg.get("wireless_enabled") is False and cfg.get("color_mode") == "mono"
    tray._act_colors.setChecked(True)
    assert cfg.get("color_mode") == "levels"


# ---------------------------------------------------------------- D: hover popup
def test_popup_content_is_plain_and_complete():
    d = Device("razer:1390", "Razer <b>Shark</b> & Co", "headset", 97, True, link="2.4ghz",
               updated=1_700_000_000.0)
    c = build_popup_content(d, 20, now=1_700_000_100.0)
    assert "<" not in c.title and "&" not in c.title
    assert c.lines[0] == "Headset - 2.4 GHz"
    assert c.lines[1] == "97% - OK"
    assert c.lines[2].startswith("Updated ")


def test_popup_statuses():
    mk = lambda **kw: Device("x", "N", "mouse", kw.pop("battery", 50), kw.pop("connected", True), **kw)
    assert status_text(mk(charging=True), 20) == "Charging"
    assert status_text(mk(battery=100), 20) == "Full"
    assert status_text(mk(battery=15), 20) == "Low"
    assert status_text(mk(battery=None), 20) == "Battery unknown"
    assert status_text(mk(connected=False), 20) == "Disconnected"
    c = build_popup_content(mk(battery=None, connected=False), 20)
    assert c.lines == ["Mouse - Bluetooth", "Disconnected"]
    assert format_updated(None) == ""


def test_place_popup_edges_and_clamp():
    screen = (0, 0, 1920, 1080)
    anchor = (900, 1040, 60, 28)
    x, y = place_popup(anchor, (200, 70), screen, "bottom", gap=8)
    assert (x, y) == (830, 1040 - 70 - 8)
    assert place_popup((900, 0, 60, 28), (200, 70), screen, "top", gap=8)[1] == 36
    assert place_popup((0, 500, 60, 28), (200, 70), screen, "left", gap=8)[0] == 68
    assert place_popup((1860, 500, 60, 28), (200, 70), screen, "right", gap=8)[0] == 1860 - 208
    # clamped inside the screen
    assert place_popup((1900, 1040, 60, 28), (200, 70), screen, "bottom")[0] == 1720
    assert place_popup((0, 0, 20, 20), (200, 70), screen, "bottom")[1] == 0
    # secondary monitor with negative origin
    sx, sy = place_popup((-1900, 1040, 60, 28), (200, 70), (-1920, 0, 1920, 1080), "bottom")[0], 0
    assert sx >= -1920


def test_widget_hover_shows_and_hides_popup(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "Alpha", "keyboard", 80, True)])
    assert wd.hasMouseTracking()
    wd.show()
    wd._hover = 0
    wd._show_popup()
    assert wd._popup is not None and wd._popup.isVisible()
    assert wd._popup_hwnd() != 0
    wd._hide_popup()
    assert not wd._popup.isVisible()
    wd._hover = 0
    wd._show_popup()
    wd.hide()
    assert not wd._popup.isVisible()


def test_popup_renders(qapp):
    p = HoverPopup()
    p.set_content(build_popup_content(Device("a", "Alpha " * 20, "mouse", 5, True), 20))
    assert p.width() <= 320 and p.height() > 30
    assert not p.grab().isNull()


# ---------------------------------------------------------------- idle (sleeping 2.4 GHz devices)
def test_config_idle_grace_min_validation(tmp_path):
    c = Config(tmp_path / "c.json")
    assert c.get("idle_grace_min") == 30
    c.set("idle_grace_min", 1)
    c.set("idle_grace_min", 1440)
    for bad in (0, 1441, -5, "30", 2.5, True, None):
        with pytest.raises(ValueError):
            c.set("idle_grace_min", bad)
    assert Config(tmp_path / "c.json").get("idle_grace_min") == 1440


def test_idle_pill_renders_with_reduced_opacity(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd._light = False
    awake = Device("a", "A", "mouse", 95, True)
    asleep = Device("b", "B", "mouse", 95, True, idle=True)
    wd.set_devices([awake, asleep])
    assert wd._pills[1].idle and not wd._pills[0].idle and wd.pill_texts()[1] == "95%"
    full, dim = _bar_pixel(wd, 0), _bar_pixel(wd, 1)
    assert full.name() == battery_color(95, False, 20, True).name()
    assert dim.name() != full.name() and dim.green() < full.green()      # blended toward background


def test_idle_popup_text():
    d = Device("b", "G903", "mouse", 64, True, link="2.4ghz", idle=True, updated=1000.0)
    c = build_popup_content(d, 20, now=1000.0 + 12 * 60)
    assert c.lines[1] == "64% - Asleep \u00b7 last value 12 min ago"
    assert not any("Updated" in x for x in c.lines)
    set_language("es")
    assert build_popup_content(d, 20, now=1000.0 + 12 * 60).lines[1] == \
        "64% - En reposo \u00b7 \u00faltimo valor hace 12 min"
    assert status_text(d, 20) == "En reposo"


def test_alerts_do_not_renotify_idle_device():
    from taskbar_battery_widget.alerts import AlertManager

    class C:
        def get(self, k):
            return {"notifications_enabled": True, "low_threshold": 20,
                    "notification_cooldown_min": 1}[k]
    am = AlertManager(C())
    assert len(am.check([Device("x", "M", "mouse", 10, True)], 0.0)) == 1
    assert am.check([Device("x", "M", "mouse", 10, True, idle=True)], 1000.0) == []
