"""Settings model: declarative tree (no widgets) and the ids each page exposes."""
from taskbar_battery_widget.config import COLOR_PALETTES, FLOATING_ANCHORS, ICON_STYLES
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.settings_model import (Action, Choice, Page, Separator, SettingsCallbacks,
                                                   Toggle, build_settings_model)


class Cfg:
    def __init__(self, **kw):
        self.d = {"wireless_enabled": True, "bluetooth_enabled": True, "notifications_enabled": True,
                  "full_charge_alert": True, "detail_log_enabled": False, "start_with_windows": False,
                  "show_in_fullscreen": False, "low_threshold": 20, "color_palette": "classic",
                  "color_mode": "levels", "icon_style": "outline", "placement_mode": "taskbar",
                  "taskbar_anchor": "right", "placement_monitor": "primary", "floating_positions": {},
                  "panel_size": "normal", "ui_scale": 100, "language": "auto"}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


def make(**kw):
    set_language("en")
    cfg = Cfg(**kw)
    calls = []

    def rec(name):
        return lambda *a: calls.append((name, *a))
    cb = SettingsCallbacks(
        set_bool=rec("set_bool"), set_threshold=rec("set_threshold"), set_palette=rec("set_palette"),
        set_startup=rec("set_startup"), open_device_log=rec("open_device_log"),
        open_logs_folder=rec("open_logs_folder"), startup_enabled=lambda: False,
        open_detail_folder=rec("open_detail_folder"), set_icon_style=rec("set_icon_style"),
        set_placement=rec("set_placement"), set_monitor=rec("set_monitor"), reset_position=rec("reset_position"),
        list_screens=lambda: [("aa11", "Monitor 2")], set_floating_anchor=rec("set_floating_anchor"),
        open_custom_icons=rec("open_custom_icons"))
    return cfg, calls, build_settings_model(cfg, cb)


# Every entry of each settings page, by id (config key for toggles).
SETTINGS_TREE = {
    "sources": {"wireless_enabled", "bluetooth_enabled", "razer_dongle_wake"},
    "alerts": {"notifications_enabled", "full_charge_alert", "low_threshold"},
    "logs": {"open_device_log", "open_logs_folder", "detail_log_enabled", "open_detail_folder"},
    "personalization": {"app_theme", "pill_style", "palette", "icon_style", "custom_icons", "animations", "position",
                        "panel_size", "ui_scale", "language", "hdr_pills", "hdr_app", "pill_font_weight",
                        "pill_percent_sign", "pill_show_names", "pill_show_bar", "pill_color_text", "pill_separators"},
    "position": {"placement", "float_anchor", "monitor", "stack_devices", "attach_to_taskbar", "show_in_fullscreen",
                 "fullscreen_placement",
                 "reset_position"},
    "root": {"sources", "alerts", "logs", "start_with_windows", "personalization", "tutorial", "whats_new"},
}


def test_settings_tree_structure():
    _cfg, _calls, m = make()
    for page_id, expected in SETTINGS_TREE.items():
        page = m.find(page_id)
        assert isinstance(page, Page)
        assert {c.id for c in page.children if not isinstance(c, Separator)} == expected, page_id
    kinds = {Toggle: {"wireless_enabled", "bluetooth_enabled", "razer_dongle_wake", "notifications_enabled", "full_charge_alert",
                      "detail_log_enabled", "start_with_windows", "show_in_fullscreen", "stack_devices",
                      "attach_to_taskbar", "pill_percent_sign", "pill_show_names", "pill_show_bar",
                      "pill_color_text", "pill_separators"},
             Choice: {"low_threshold", "app_theme", "pill_style", "palette", "icon_style", "animations", "placement", "float_anchor", "monitor", "fullscreen_placement",
                       "panel_size", "ui_scale", "language", "pill_font_weight"},
             Action: {"open_device_log", "open_logs_folder", "open_detail_folder", "custom_icons",
                      "reset_position", "tutorial", "whats_new"}}
    for kind, ids in kinds.items():
        assert {n.id for n in m.walk() if isinstance(n, kind)} == ids
    ids = [n.id for n in m.walk()]
    assert len(ids) == len(set(ids))                    # unique ids


def test_choice_values_match_config_constants():
    _cfg, _calls, m = make()
    assert [o.value for o in m.find("palette").options()] == list(COLOR_PALETTES)
    assert [o.value for o in m.find("icon_style").options()] == list(ICON_STYLES)
    assert [o.value for o in m.find("float_anchor").options()] == list(FLOATING_ANCHORS)
    assert [o.value for o in m.find("low_threshold").options()] == [10, 15, 20, 25, 30]
    assert [o.value for o in m.find("placement").options()] == ["right", "left", "floating"]
    assert m.find("palette").options()[-1].value == "custom"


def test_toggles_and_choices_route_to_callbacks():
    _cfg, calls, m = make()
    m.find("wireless_enabled").set(False)
    m.find("detail_log_enabled").set(True)
    m.find("show_in_fullscreen").set(True)
    m.find("start_with_windows").set(True)
    m.find("low_threshold").set(25)
    m.find("palette").set("pastel")
    m.find("icon_style").set("retro")
    m.find("placement").set("left")
    m.find("float_anchor").set("top-left")
    m.find("monitor").set("aa11")
    for aid in ("open_device_log", "open_logs_folder", "open_detail_folder", "custom_icons", "reset_position"):
        m.find(aid).run()
    assert calls == [
        ("set_bool", "wireless_enabled", False), ("set_bool", "detail_log_enabled", True),
        ("set_bool", "show_in_fullscreen", True), ("set_startup", True), ("set_threshold", 25),
        ("set_palette", "pastel"), ("set_icon_style", "retro"), ("set_placement", "left"),
        ("set_floating_anchor", "top-left"), ("set_monitor", "aa11"),
        ("open_device_log",), ("open_logs_folder",), ("open_detail_folder",), ("open_custom_icons",),
        ("reset_position",)]


def test_getters_follow_config_without_widgets():
    cfg, _calls, m = make()
    cfg.d.update(color_mode="mono", low_threshold=30, placement_mode="floating", placement_monitor="zz")
    assert m.find("palette").get() == "mono" and m.find("low_threshold").get() == 30
    assert m.find("placement").get() == "floating"
    assert m.find("monitor").current_label() == "Disconnected monitor (using the primary)"
    assert m.find("reset_position").enabled()
    cfg.d["color_mode"] = "levels"
    cfg.d["color_palette"] = "mono"                      # stale palette without mono mode -> classic
    assert m.find("palette").get() == "classic"


def test_navigation_helpers():
    _cfg, _calls, m = make()
    assert [p.id for p in m.path_to("position")] == ["root", "personalization", "position"]
    assert [p.id for p in m.path_to("nope")] == ["root"]
    assert m.parent_of("monitor").id == "position" and m.parent_of("root") is None


def test_titles_translate():
    _cfg, _calls, m = make()
    assert m.root.title() == "Settings" and m.find("sources").title() == "Sources"
    set_language("es")
    assert m.root.title() == "Configuración" and m.find("personalization").title() == "Personalización"
    set_language("en")
