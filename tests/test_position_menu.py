"""Settings > Personalization > Position submenu: tree, persistence, sync, monitors, reset, warning."""
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.tray import TrayController

SCREENS = [("aa11", "Monitor 2 \u2014 1080\u00d71920")]


def setup_function(_):
    set_language("en")


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
    assert texts(pos) == ["Taskbar, right", "Taskbar, left", "Floating", "Monitor",
                          "Visible in fullscreen", "Reset position"]
    assert t._settings.pos_actions["left"].toolTip() == "Needs centred Windows icons"
    assert t._settings.pos_actions["floating"].toolTip() == "Drag it to any corner or edge"
    set_language("es")
    t2 = TrayController(Config(tmp_path / "d.json"))
    assert texts(t2._settings.position_menu) == [
        "Barra de tareas, derecha", "Barra de tareas, izquierda", "Flotante", "Monitor",
        "Visible en pantalla completa", "Restablecer posición"]
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
    t._settings.position_menu.actions()[-1].trigger()
    fp = cfg.get("floating_positions")
    assert fp["aa11"] == {"anchor": "bottom-right", "dx": 0, "dy": 0}
    assert fp["primary"] == {"anchor": "top-left", "dx": 5, "dy": 7}
    assert cfg.get("placement_mode") == "floating"


def test_placement_warning_once(qapp, tmp_path):
    cfg, t = make(tmp_path, qapp)
    shown = []
    t.notify = lambda title, body, info=False: shown.append(body)
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("left_needs_centered_icons")
    t.show_placement_warning("other")
    assert shown == ["The left position needs centred Windows icons; the right one is used instead."]
