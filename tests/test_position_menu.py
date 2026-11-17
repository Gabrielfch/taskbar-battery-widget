"""Settings > Personalization > Position submenu: tree, persistence, sync, monitors, reset, warning."""
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController

SCREENS = [("aa11", "Monitor 2 \u2014 1080\u00d71920")]


import pytest

from taskbar_battery_widget import taskbar


@pytest.fixture(autouse=True)
def _centered(monkeypatch):
    set_language("en")
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: True)


def make(tmp_path, qapp):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_screen_lister(lambda: SCREENS)
    return cfg, t


def labels(page):
    from taskbar_battery_widget.settings_model import Separator
    return [n.title() if hasattr(n, "title") else n.label() for n in page.children if not isinstance(n, Separator)]


def opts(t, node_id):
    return {o.value: o for o in t._settings.find(node_id).options()}


def test_tree_en_and_es(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    pos = t._settings.find("position")
    assert labels(pos) == ["Position", "Floating position", "Monitor", "Visible in fullscreen", "In fullscreen",
                           "Reset position"]
    assert [o.label for o in t._settings.find("placement").options()] == [
        "Taskbar, right", "Taskbar, left", "Floating (draggable)"]
    assert opts(t, "placement")["left"].tip == "Needs centred Windows icons"
    assert opts(t, "placement")["floating"].tip == "Drag it to any corner or edge"
    set_language("es")
    t2 = TrayController(Config(tmp_path / "d.json"))
    assert labels(t2._settings.find("position")) == [
        "Posición", "Posición flotante", "Monitor", "Visible en pantalla completa", "En pantalla completa",
        "Restablecer posición"]
    assert [o.label for o in t2._settings.find("placement").options()] == [
        "Barra de tareas, derecha", "Barra de tareas, izquierda", "Flotante (arrastrable)"]
    assert [o.label for o in t2._settings.find("float_anchor").options()] == [
        "Arriba izquierda", "Arriba centro", "Arriba derecha", "Centro izquierda", "Centro derecha",
        "Abajo izquierda", "Abajo centro", "Abajo derecha"]
    assert opts(t2, "placement")["left"].tip == "Requiere los iconos de Windows centrados"


def test_options_persist_keys(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    placement = t._settings.find("placement")
    placement.set("left")
    assert (cfg.get("placement_mode"), cfg.get("taskbar_anchor")) == ("taskbar", "left")
    placement.set("floating")
    assert cfg.get("placement_mode") == "floating"
    placement.set("right")
    assert (cfg.get("placement_mode"), cfg.get("taskbar_anchor")) == ("taskbar", "right")
    t._settings.find("show_in_fullscreen").set(True)
    assert cfg.get("show_in_fullscreen") is True
    assert len(n) == 4


def test_values_reflect_external_changes(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("placement_mode", "floating")
    cfg.set("show_in_fullscreen", True)
    cfg.set("placement_monitor", "aa11")
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    sm = t._settings
    assert sm.find("placement").get() == "floating"
    assert sm.find("show_in_fullscreen").get() is True
    assert sm.find("monitor").get() == "aa11" and sm.find("monitor").current_label().startswith("Monitor 2")
    assert n == []                                  # reading does not persist or emit
    cfg.set("placement_mode", "taskbar")
    cfg.set("taskbar_anchor", "left")
    assert sm.find("placement").get() == "left"


def test_monitor_list_is_read_live(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    sm = t._settings
    mon = sm.find("monitor")
    assert [o.value for o in mon.options()] == ["primary", "aa11"]
    assert mon.options()[1].label.startswith("Monitor 2") and mon.options()[0].label == "Primary"
    t.set_screen_lister(lambda: [("bb22", "Monitor 3"), ("cc33", "Monitor 4")])
    cfg.set("placement_monitor", "cc33")
    assert [o.value for o in mon.options()] == ["primary", "bb22", "cc33"]
    assert mon.get() == "cc33"
    mon.set("bb22")
    assert cfg.get("placement_monitor") == "bb22"
    mon.set("primary")
    assert cfg.get("placement_monitor") == "primary"


def test_reset_only_current_monitor(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 5, "dy": 7},
                                   "aa11": {"anchor": "top-right", "dx": 9, "dy": 3}})
    cfg.set("placement_mode", "floating")
    cfg.set("placement_monitor", "aa11")
    t.set_current_screen_key_fn(lambda: "aa11")
    t._settings.find("reset_position").run()
    fp = cfg.get("floating_positions")
    assert "aa11" not in fp and "primary" not in fp         # both gone: default position applies
    assert cfg.get("placement_mode") == "floating"


def test_reset_keeps_other_monitors(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 1, "dy": 1},
                                   "aa11": {"anchor": "top-right", "dx": 2, "dy": 2},
                                   "bb22": {"anchor": "top-center", "dx": 3, "dy": 3}})
    t.set_current_screen_key_fn(lambda: "aa11")
    t._reset_position()
    assert list(cfg.get("floating_positions")) == ["bb22"]
    t.set_current_screen_key_fn(None)                       # unknown key: only "primary" goes
    t._reset_position()
    assert list(cfg.get("floating_positions")) == ["bb22"]


def test_floating_anchor_choice(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    got = []
    t.set_floating_anchor_fn(got.append)
    t.set_current_screen_key_fn(lambda: "aa11")
    anchor = t._settings.find("float_anchor")
    assert anchor.get() == "bottom-right"                   # default
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0}})
    assert anchor.get() == "top-left"                       # falls back to "primary"
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0},
                                   "aa11": {"anchor": "middle-left", "dx": 0, "dy": 0}})
    assert anchor.get() == "middle-left"
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    assert cfg.get("placement_mode") == "taskbar"
    anchor.set("top-center")
    assert got == ["top-center"] and cfg.get("placement_mode") == "floating" and n == [1]
    anchor.set("bottom-left")                               # already floating: no mode switch signal
    assert got == ["top-center", "bottom-left"] and n == [1]


def test_floating_hint_once(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    placement = t._settings.find("placement")
    placement.set("floating")
    placement.set("right")
    placement.set("floating")
    assert shown == ["Drag the widget to move it, or pick a corner in Floating position."]


def test_left_fallback_label_and_warning(qapp, tmp_path, monkeypatch):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    placement = t._settings.find("placement")
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: False)
    left = opts(t, "placement")["left"]
    assert left.label == "Taskbar, left (needs centred icons)" and left.enabled
    placement.set("left")
    placement.set("right")
    placement.set("left")
    assert len(shown) == 2 and "centred" in shown[0]        # every explicit pick, not once per session
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: True)
    assert opts(t, "placement")["left"].label == "Taskbar, left"
    placement.set("left")
    assert len(shown) == 2


def test_reset_enabled_only_when_useful(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    reset = t._settings.find("reset_position")
    assert not reset.enabled()                              # taskbar mode, monitor has a taskbar
    t.set_monitor_has_taskbar_fn(lambda: False)
    assert reset.enabled()
    t.set_monitor_has_taskbar_fn(lambda: True)
    cfg.set("placement_mode", "floating")
    assert reset.enabled()


def test_disconnected_monitor_entry(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("placement_monitor", "dead")
    o = opts(t, "monitor")["dead"]
    assert o.label == "Disconnected monitor (using the primary)" and not o.enabled
    assert t._settings.find("monitor").get() == "dead"
    cfg.set("placement_monitor", "aa11")
    assert "dead" not in opts(t, "monitor") and t._settings.find("monitor").get() == "aa11"


def test_placement_warning_once(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("other")
    assert shown == ["The left position needs centred Windows icons; the right one is used instead."]
