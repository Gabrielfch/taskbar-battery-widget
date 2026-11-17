"""Shared animation clock: when it runs, how fast, and the widget / panel behaviour around it (offscreen Qt)."""
import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QGuiApplication

from taskbar_battery_widget import animation as an
from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget import icons
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.colors import (TASKBAR_BASE, PALETTES, band_color, composite, contrast_ratio,
                                           pill_backgrounds, set_palette)
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.placement import ScreenInfo

from tests._anim_data import make_gif

_REAL_PROBE = an.system_animations_enabled      # captured before the autouse fixture replaces it
D1 = "\\\\.\\DISPLAY1"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)


class Owner(QObject):
    """A clock client whose demand the test controls."""

    def __init__(self, value=None):
        super().__init__()
        self.value = value

    def demand(self):
        return self.value


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ the clock alone
def test_clock_idle_without_demand(qapp):
    c = an.AnimationClock()
    o = Owner(None)
    c.register(o, o.demand)
    c.refresh()
    assert not c.running


def test_clock_runs_with_demand_and_stops_when_it_goes(qapp):
    c = an.AnimationClock()
    o = Owner(500)
    c.register(o, o.demand)
    c.refresh()
    assert c.running and c.interval() == 500
    o.value = None
    c.refresh()
    assert not c.running


@pytest.mark.parametrize("asked,expected", [(1, 66), (10, 66), (65, 66), (66, 66), (100, 100), (500, 500),
                                            (2000, 2000), (60000, 2000)])
def test_interval_is_clamped(qapp, asked, expected):
    c = an.AnimationClock()
    o = Owner(asked)
    c.register(o, o.demand)
    c.refresh()
    assert c.interval() == expected and c.interval() >= an.MIN_INTERVAL_MS


def test_interval_is_the_nearest_demand(qapp):
    c = an.AnimationClock()
    a, b = Owner(400), Owner(150)
    c.register(a, a.demand)
    c.register(b, b.demand)
    c.refresh()
    assert c.interval() == 150
    b.value = None
    c.refresh()
    assert c.interval() == 400


def test_disabled_clock_stays_stopped_and_resumes(qapp):
    c = an.AnimationClock()
    o = Owner(100)
    c.register(o, o.demand)
    c.refresh()
    seen = []
    c.state_changed.connect(lambda: seen.append(c.running))
    c.set_enabled(False)
    assert not c.running and not c.allowed() and seen == [False]
    c.set_enabled(True)
    assert c.running and seen == [False, True]


def test_windows_animations_off_stops_and_is_reread_after_a_minute(qapp):
    t, answers = FakeTime(), {"v": False}
    c = an.AnimationClock(time_fn=t, system_fn=lambda: answers["v"])
    o = Owner(100)
    c.register(o, o.demand)
    c.refresh()
    assert not c.running and not c.allowed()
    answers["v"] = True
    c.refresh()
    assert not c.running                                    # the answer is cached ...
    t.t += an.SYSTEM_RECHECK_S + 1
    c.refresh()
    assert c.running                                        # ... and re-read after about a minute
    answers["v"] = False
    c.set_enabled(True)                                     # a settings change re-reads at once
    assert not c.running


def test_system_probe_failure_means_animations_stay_on(qapp):
    def boom():
        raise OSError

    c = an.AnimationClock(system_fn=boom)
    assert c.allowed()


def test_tick_emits_time_and_follows_the_source(qapp):
    t = FakeTime()
    c = an.AnimationClock(time_fn=t)
    assert c.now_ms() == 0
    t.t += 1.5
    assert c.now_ms() == 1500
    got = []
    c.tick.connect(got.append)
    c._on_timeout()
    assert got == [1500]


def test_dead_or_broken_clients_do_not_matter(qapp):
    c = an.AnimationClock()
    good, bad = Owner(300), Owner(1)

    def boom():
        raise ValueError

    c.register(good, good.demand)
    c.register(bad, boom)
    c.refresh()
    assert c.running and c.interval() == 300
    del bad
    good.value = None
    c.refresh()
    assert not c.running


def test_unregister(qapp):
    c = an.AnimationClock()
    o = Owner(100)
    c.register(o, o.demand)
    c.unregister(o)
    c.refresh()
    assert not c.running


def test_shared_clock_is_a_singleton_and_reset_drops_it(qapp):
    a = an.clock()
    assert an.clock() is a
    an.reset()
    assert an.clock() is not a


def test_real_windows_probe_returns_a_bool():
    assert isinstance(_REAL_PROBE(), bool)       # the autouse fixture replaces the module function; this is the original


# ------------------------------------------------------------------ pulses
def test_charge_pulse_range_and_period():
    vals = [an.charge_pulse(t) for t in range(0, 4000, 50)]
    assert min(vals) == pytest.approx(0.6, abs=0.01) and max(vals) == pytest.approx(1.0, abs=0.001)
    assert an.charge_pulse(0) == pytest.approx(1.0) and an.charge_pulse(1000) == pytest.approx(0.6)
    assert an.charge_pulse(300) == pytest.approx(an.charge_pulse(2300))        # 2 s period
    assert an.charge_pulse(None) == 1.0


def test_alert_pulse_is_slow_and_gentle():
    assert an.alert_pulse(None) == 0.0 and an.alert_pulse(0) == pytest.approx(0.0)
    assert an.alert_pulse(750) == pytest.approx(1.0) and an.alert_pulse(300) == pytest.approx(an.alert_pulse(1800))
    # smooth: no step between two 100 ms samples is large, and one full swing takes 1.5 s (< 3 flashes / s)
    vals = [an.alert_pulse(t) for t in range(0, 1600, 100)]
    assert max(abs(a - b) for a, b in zip(vals, vals[1:])) < 0.5
    assert an.ALERT_PERIOD_MS >= 1000 / 3 * 2


@pytest.mark.parametrize("palette", PALETTES)
@pytest.mark.parametrize("dark", [True, False])
def test_critical_pulse_keeps_the_contrast(palette, dark):
    set_palette(palette, {"high": "#6EDC78", "medium": "#F5C842", "low": "#FF9999"})
    base = QColor(*TASKBAR_BASE[dark])
    text = band_color("red", dark)
    floor = min(contrast_ratio(text, bg) for bg in pill_backgrounds(dark).values())
    for step in range(0, 21):
        pulse = step / 20
        bg = composite(an.alert_fill(dark, pulse), base)
        assert contrast_ratio(text, bg) >= max(4.5, floor - 1e-9), (palette, dark, pulse)
        edge = QColor(text)
        edge.setAlpha(an.alert_border_alpha(pulse))
        assert contrast_ratio(composite(edge, bg), bg) >= 3.0, (palette, dark, pulse)
    assert an.alert_border_alpha(0) == 255 and an.alert_border_alpha(1) == an.ALERT_BORDER_MIN_ALPHA
    assert an.alert_fill(dark, 0).alpha() < an.alert_fill(dark, 1).alpha()


# ------------------------------------------------------------------ widget
@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    state = {"fs": False, "shift": False, "tmp": tmp_path}
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: state["fs"])
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: state["fs"])
    monkeypatch.setattr(w.taskbar, "taskbar_icons_centered", lambda: True)
    monkeypatch.setattr(w, "set_click_through", lambda hwnd, on: True)
    monkeypatch.setattr(w, "shift_down", lambda: state["shift"])
    cfg = Config(tmp_path / "config.json")
    cfg.set("placement_mode", "floating")
    cfg.set("show_in_fullscreen", True)
    real = QGuiApplication.primaryScreen()
    clock = an.clock()
    state["time"] = FakeTime()
    clock.set_time_source(state["time"])
    wd = w.TaskbarWidget(cfg)
    wd._screens = lambda: [(P, real)]
    state.update(wd=wd, cfg=cfg, clock=clock)
    yield state
    wd.stop()
    wd.deleteLater()


def _plain(env, battery=80, charging=False):
    env["wd"].set_devices([Device("1", "KB", "keyboard", battery, True, charging=charging)])
    env["wd"].update_position()


def _animated_icon(env, dev_id="1", kind="keyboard"):
    src = env["tmp"] / "a.gif"
    src.write_bytes(make_gif(16, 16, [100, 200, 300]))
    name = ci.import_image(src)
    ci.set_maps({}, {visibility_key(dev_id): name})
    return name


def test_no_animation_no_clock(env):
    _plain(env)
    assert env["wd"].isVisible() and not env["clock"].running


def test_animated_icon_starts_the_clock(env):
    _animated_icon(env)
    _plain(env)
    c = env["clock"]
    assert c.running and c.interval() >= an.MIN_INTERVAL_MS


def test_clock_follows_the_animated_frame_delay(env):
    _animated_icon(env)
    _plain(env)
    c, t = env["clock"], env["time"]
    t.t += 0.0                                               # frame 0 lasts 100 ms: clamp keeps it above 66
    c.refresh()
    assert c.interval() >= an.MIN_INTERVAL_MS
    t.t += 0.35                                              # inside frame 2 (300-600 ms): 250 ms left
    c.refresh()
    assert 150 <= c.interval() <= 300


def test_switch_off_stops_the_clock_and_on_resumes(env):
    _animated_icon(env)
    _plain(env)
    assert env["clock"].running
    env["cfg"].set("animations", "off")
    env["wd"].set_config(env["cfg"])
    assert not env["clock"].running
    env["cfg"].set("animations", "system")
    env["wd"].set_config(env["cfg"])
    assert env["clock"].running


def test_fullscreen_passive_stops_the_clock(env):
    _animated_icon(env)
    _plain(env)
    assert env["clock"].running
    env["fs"] = True
    env["wd"].update_position()
    assert env["wd"]._passive and not env["clock"].running
    env["fs"] = False
    env["wd"].update_position()
    assert not env["wd"]._passive and env["clock"].running


def test_windows_animations_off_stops_the_clock(env, monkeypatch):
    _animated_icon(env)
    _plain(env)
    assert env["clock"].running
    monkeypatch.setattr(an, "system_animations_enabled", lambda: False)
    env["clock"].set_enabled(True)                           # a settings change re-reads the Windows value
    assert not env["clock"].running and not env["clock"].allowed()
    monkeypatch.setattr(an, "system_animations_enabled", lambda: True)
    env["time"].t += an.SYSTEM_RECHECK_S + 1
    env["wd"].update_position()                              # the periodic placement tick re-checks it
    assert env["clock"].running


def test_hidden_widget_stops_the_clock(env):
    _animated_icon(env)
    _plain(env)
    assert env["clock"].running
    env["wd"].hide()
    assert not env["clock"].running
    env["wd"].update_position()
    assert env["clock"].running


def test_icon_removed_stops_the_clock(env):
    _animated_icon(env)
    _plain(env)
    assert env["clock"].running
    ci.set_maps({}, {})
    env["wd"].set_config(env["cfg"])
    assert not env["clock"].running


def _frame_colour(env):
    img = env["wd"].grab().toImage()
    r = env["wd"]._rects[0]
    x, y = r[0] + env["wd"]._pad + env["wd"]._icon_size() // 2, r[1] + r[3] // 2
    return QColor.fromRgba(img.pixel(int(x), int(y))).getRgb()[:3]


def test_widget_draws_the_frame_of_the_clock_time_and_the_first_when_off(env):
    _animated_icon(env)
    _plain(env)
    t = env["time"]
    base = t.t
    seen = []
    for dt in (0.0, 0.15, 0.45):
        t.t = base + dt
        seen.append(_frame_colour(env))
    assert seen[0] != seen[1] != seen[2] and seen[0] != seen[2]
    env["cfg"].set("animations", "off")
    env["wd"].set_config(env["cfg"])
    shots = set()
    for dt in (0.0, 0.15, 0.45):
        t.t = base + dt
        shots.add(_frame_colour(env))
    assert len(shots) == 1 and shots.pop() == seen[0]


def test_tick_repaints_only_the_animated_pill(env):
    _animated_icon(env, "1")
    env["wd"].set_devices([Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 70, True)])
    env["wd"].update_position()
    assert [i for i, _ms in env["wd"]._anim_plan(0)] == [0]


def _bolt_pixels(env):
    img = env["wd"].grab().toImage()
    x, y, wd, h = env["wd"]._rects[0]
    return [QColor.fromRgba(img.pixel(int(x + wd - env["wd"]._pad - 2), int(y + h // 2))).getRgb()]


def test_charging_pulse_changes_between_ticks_and_is_still_when_off(env):
    icons.set_icon_style("outline")
    _plain(env, 80, charging=True)
    t = env["time"]
    base = t.t
    assert env["clock"].running and env["clock"].interval() == an.PULSE_INTERVAL_MS
    t.t = base
    full = env["wd"].grab().toImage()
    t.t = base + 1.0
    dim = env["wd"].grab().toImage()
    assert full != dim
    env["cfg"].set("animations", "off")
    env["wd"].set_config(env["cfg"])
    assert not env["clock"].running
    t.t = base
    a = env["wd"].grab().toImage()
    t.t = base + 1.0
    b = env["wd"].grab().toImage()
    assert a == b and a == full                            # still = the full-opacity look


def test_battery_style_gauge_pulses_while_charging(env):
    icons.set_icon_style("battery")
    _plain(env, 50, charging=True)
    assert env["clock"].running
    t = env["time"]
    base = t.t
    t.t = base
    a = env["wd"].grab().toImage()
    t.t = base + 1.0
    b = env["wd"].grab().toImage()
    assert a != b


def test_critical_level_pulses_but_not_while_charging(env):
    _plain(env, 5)
    assert env["wd"]._pills[0].alert and env["clock"].running
    t = env["time"]
    base = t.t
    t.t = base
    a = env["wd"].grab().toImage()
    t.t = base + 0.75
    b = env["wd"].grab().toImage()
    assert a != b
    env["cfg"].set("animations", "off")
    env["wd"].set_config(env["cfg"])
    assert not env["clock"].running
    t.t = base
    c = env["wd"].grab().toImage()
    t.t = base + 0.75
    d = env["wd"].grab().toImage()
    assert c == d == a                                      # still look == pulse trough
    _plain(env, 5, charging=True)
    env["cfg"].set("animations", "system")
    env["wd"].set_config(env["cfg"])
    assert not env["wd"]._pills[0].alert                    # charging is not critical


def test_normal_level_without_charging_has_no_pulse(env):
    _plain(env, 80)
    assert env["wd"]._anim_plan(0) == [] and not env["clock"].running


def test_one_off_blink_is_not_combined_with_the_pulse(env):
    _plain(env, 5)
    env["wd"]._flash_left = 3
    assert env["wd"]._anim_plan(0) == []
    env["wd"]._flash_left = 0
    assert [i for i, _ in env["wd"]._anim_plan(0)] == [0]


def test_closing_the_widget_unregisters_it(env):
    _plain(env, 5)
    assert env["clock"].running
    env["wd"].close()
    assert not env["clock"].running


# ------------------------------------------------------------------ panel
def _panel_model(dev):
    from taskbar_battery_widget.panel_model import build_panel_model
    return build_panel_model([dev], set(), False, 20, True, True)


def test_panel_animates_only_while_open_on_the_device_list(env, qapp):
    from taskbar_battery_widget.device_panel import DevicePanel
    _animated_icon(env)
    p = DevicePanel(dark=True)
    try:
        dev = Device("1", "KB", "keyboard", 80, True)
        p.set_model(_panel_model(dev))
        assert not env["clock"].running                     # closed
        p.show()
        qapp.processEvents()
        env["clock"].refresh()
        assert env["clock"].running and env["clock"].interval() >= an.MIN_INTERVAL_MS
        t = env["time"]
        t.t += 0.15
        env["clock"]._on_timeout()
        icon = p._row_ctx[visibility_key("1")][1]
        shown = QColor.fromRgba(icon.pixmap().toImage().pixel(10, 10)).getRgb()[:3]
        assert shown[1] > 200 and shown[0] < 60                  # frame 1 (green) at 150 ms
        env["cfg"].set("animations", "off")
        env["clock"].set_enabled(False)
        assert not env["clock"].running
        first = QColor.fromRgba(icon.pixmap().toImage().pixel(10, 10)).getRgb()[:3]
        assert first[0] > first[1]                                # paused: first frame (red)
        env["clock"].set_enabled(True)
        assert env["clock"].running
        p.hide()
        assert not env["clock"].running
    finally:
        p.hide()
        p.deleteLater()


def test_panel_settings_page_pauses_the_icons(env, qapp):
    from taskbar_battery_widget.device_panel import DevicePanel
    from taskbar_battery_widget.settings_model import SettingsCallbacks, build_settings_model
    _animated_icon(env)
    p = DevicePanel(dark=True)
    try:
        cb = SettingsCallbacks(set_bool=lambda k, v: None, set_threshold=lambda v: None,
                               set_palette=lambda v: None, set_startup=lambda v: None,
                               open_device_log=lambda: None, open_logs_folder=lambda: None,
                               startup_enabled=lambda: False)
        p.set_settings_model(build_settings_model(env["cfg"], cb))
        p.set_model(_panel_model(Device("1", "KB", "keyboard", 80, True)))
        p.show()
        qapp.processEvents()
        env["clock"].refresh()
        assert env["clock"].running
        p.show_settings()
        assert not env["clock"].running
        p.show_devices()
        assert env["clock"].running
    finally:
        p.hide()
        p.deleteLater()


# ------------------------------------------------------------------ settings wiring
def test_setting_defaults_and_choice(tmp_path):
    from taskbar_battery_widget.settings_model import Choice, SettingsCallbacks, build_settings_model
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("animations") == "system"
    calls = []
    cb = SettingsCallbacks(set_bool=lambda k, v: None, set_threshold=lambda v: None,
                           set_palette=lambda v: None, set_startup=lambda v: None, open_device_log=lambda: None,
                           open_logs_folder=lambda: None, startup_enabled=lambda: False,
                           set_animations=calls.append)
    m = build_settings_model(cfg, cb)
    node = m.find("animations")
    assert isinstance(node, Choice) and m.parent_of("animations").id == "personalization"
    assert [o.value for o in node.options()] == ["always", "system", "off"]
    assert node.get() == "system" and any(o.tip for o in node.options())
    node.set("always")
    assert calls == ["always"]
    cfg.set("animations", "off")
    assert node.get() == "off"
    with pytest.raises(ValueError):
        cfg.set("animations", "yes")


def test_legacy_animations_enabled_is_migrated(tmp_path):
    import json
    for old, new in ((False, "off"), (True, "system")):
        p = tmp_path / f"c{old}.json"
        p.write_text(json.dumps({"animations_enabled": old}), encoding="utf-8")
        cfg = Config(p)
        assert cfg.get("animations") == new
        assert "animations_enabled" not in json.loads(p.read_text(encoding="utf-8"))


def test_always_mode_ignores_the_windows_setting():
    c = an.AnimationClock(system_fn=lambda: False)
    c.set_mode("system")
    assert not c.allowed()
    c.set_mode("always")
    assert c.allowed()
    c.set_mode("off")
    assert not c.allowed()


def test_tray_toggle_switches_the_clock_live(qapp, tmp_path):
    from taskbar_battery_widget.tray import TrayController, apply_appearance
    cfg = Config(tmp_path / "c.json")
    apply_appearance(cfg)
    assert an.clock().enabled
    t = TrayController(cfg)
    t._set_animations("off")
    assert not an.clock().enabled and cfg.get("animations") == "off"
    cfg.set("animations", "system")
    apply_appearance(cfg)
    assert an.clock().enabled
