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


def texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_tree_en_and_es(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    pos = t._settings.position_menu
    assert texts(pos) == ["Taskbar, right", "Taskbar, left", "Floating (draggable)", "Floating position",
                          "Monitor", "Visible in fullscreen", "Reset position"]
    assert pos.toolTipsVisible()
    assert t._settings.pos_actions["left"].toolTip() == "Needs centred Windows icons"
    assert t._settings.pos_actions["floating"].toolTip() == "Drag it to any corner or edge"
    set_language("es")
    t2 = TrayController(Config(tmp_path / "d.json"))
    assert texts(t2._settings.position_menu) == [
        "Barra de tareas, derecha", "Barra de tareas, izquierda", "Flotante (arrastrable)",
        "Posición flotante", "Monitor", "Visible en pantalla completa", "Restablecer posición"]
    assert texts(t2._settings.anchor_menu) == [
        "Arriba izquierda", "Arriba centro", "Arriba derecha", "Centro izquierda", "Centro derecha",
        "Abajo izquierda", "Abajo centro", "Abajo derecha"]
    assert t2._settings.pos_actions["left"].toolTip() == "Requiere los iconos de Windows centrados"


def test_options_persist_keys(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    acts = t._settings.pos_actions
    acts["left"].trigger()
    assert (cfg.get("placement_mode"), cfg.get("taskbar_anchor")) == ("taskbar", "left")
    acts["floating"].trigger()
    assert cfg.get("placement_mode") == "floating"
    acts["right"].trigger()
    assert (cfg.get("placement_mode"), cfg.get("taskbar_anchor")) == ("taskbar", "right")
    fs = t._settings.actions["show_in_fullscreen"]
    fs.trigger()
    assert cfg.get("show_in_fullscreen") is True
    assert len(n) == 4


def test_sync_reflects_external_changes(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("placement_mode", "floating")
    cfg.set("show_in_fullscreen", True)
    cfg.set("placement_monitor", "aa11")
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    t._settings.sync()
    sm = t._settings
    assert sm.pos_actions["floating"].isChecked() and not sm.pos_actions["right"].isChecked()
    assert sm.actions["show_in_fullscreen"].isChecked()
    assert sm.monitor_actions["aa11"].isChecked() and not sm.monitor_actions["primary"].isChecked()
    assert n == []                                  # sync does not persist or emit
    cfg.set("placement_mode", "taskbar")
    cfg.set("taskbar_anchor", "left")
    sm.sync()
    assert sm.pos_actions["left"].isChecked()


def test_monitor_list_rebuilt_on_about_to_show(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    sm = t._settings
    assert list(sm.monitor_actions) == ["primary"]      # lister set after the build
    sm.monitor_menu.aboutToShow.emit()
    assert list(sm.monitor_actions) == ["primary", "aa11"]
    assert sm.monitor_actions["aa11"].text().startswith("Monitor 2")
    assert sm.monitor_actions["primary"].text() == "Primary"
    t.set_screen_lister(lambda: [("bb22", "Monitor 3"), ("cc33", "Monitor 4")])
    cfg.set("placement_monitor", "cc33")
    sm.monitor_menu.aboutToShow.emit()
    assert list(sm.monitor_actions) == ["primary", "bb22", "cc33"]
    assert sm.monitor_actions["cc33"].isChecked()
    assert [a.text() for a in sm.monitor_menu.actions()] == ["Primary", "Monitor 3", "Monitor 4"]
    sm.monitor_actions["bb22"].trigger()
    assert cfg.get("placement_monitor") == "bb22"
    sm.monitor_actions["primary"].trigger()
    assert cfg.get("placement_monitor") == "primary"


def test_reset_only_current_monitor(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 5, "dy": 7},
                                   "aa11": {"anchor": "top-right", "dx": 9, "dy": 3}})
    cfg.set("placement_mode", "floating")
    cfg.set("placement_monitor", "aa11")
    t.set_current_screen_key_fn(lambda: "aa11")
    t._settings.sync()
    t._settings.reset_action.trigger()
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


def test_floating_anchor_submenu(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    got = []
    t.set_floating_anchor_fn(got.append)
    t.set_current_screen_key_fn(lambda: "aa11")
    sm = t._settings
    sm.sync()
    assert sm.anchor_actions["bottom-right"].isChecked()    # default
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0}})
    sm.sync()
    assert sm.anchor_actions["top-left"].isChecked()        # falls back to "primary"
    cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0},
                                   "aa11": {"anchor": "middle-left", "dx": 0, "dy": 0}})
    sm.sync()
    assert sm.anchor_actions["middle-left"].isChecked()
    assert sum(a.isChecked() for a in sm.anchor_actions.values()) == 1
    n = []
    t.settings_changed.connect(lambda: n.append(1))
    assert cfg.get("placement_mode") == "taskbar"
    sm.anchor_actions["top-center"].trigger()
    assert got == ["top-center"] and cfg.get("placement_mode") == "floating" and n == [1]
    sm.anchor_actions["bottom-left"].trigger()              # already floating: no mode switch signal
    assert got == ["top-center", "bottom-left"] and n == [1]


def test_floating_hint_once(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    t._settings.pos_actions["floating"].trigger()
    t._settings.pos_actions["right"].trigger()
    t._settings.pos_actions["floating"].trigger()
    assert shown == ["Drag the widget to move it, or pick a corner in Floating position."]


def test_left_fallback_label_and_warning(qapp, tmp_path, monkeypatch):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: False)
    t._settings.sync()
    left = t._settings.pos_actions["left"]
    assert left.text() == "Taskbar, left (needs centred icons)" and left.isEnabled()
    left.trigger()
    t._settings.pos_actions["right"].trigger()
    left.trigger()
    assert len(shown) == 2 and "centred" in shown[0]        # every explicit pick, not once per session
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: True)
    t._settings.sync()
    assert left.text() == "Taskbar, left"
    left.trigger()
    assert len(shown) == 2


def test_reset_enabled_only_when_useful(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    sm = t._settings
    sm.sync()
    assert not sm.reset_action.isEnabled()                  # taskbar mode, monitor has a taskbar
    t.set_monitor_has_taskbar_fn(lambda: False)
    sm.sync()
    assert sm.reset_action.isEnabled()
    t.set_monitor_has_taskbar_fn(lambda: True)
    cfg.set("placement_mode", "floating")
    sm.sync()
    assert sm.reset_action.isEnabled()


def test_disconnected_monitor_entry(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    sm = t._settings
    cfg.set("placement_monitor", "dead")
    sm.monitor_menu.aboutToShow.emit()
    a = sm.monitor_actions["dead"]
    assert a.text() == "Disconnected monitor (using the primary)"
    assert a.isChecked() and not a.isEnabled()
    cfg.set("placement_monitor", "aa11")
    sm.monitor_menu.aboutToShow.emit()
    assert "dead" not in sm.monitor_actions and sm.monitor_actions["aa11"].isChecked()


def test_placement_warning_once(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("other")
    assert shown == ["The left position needs centred Windows icons; the right one is used instead."]
