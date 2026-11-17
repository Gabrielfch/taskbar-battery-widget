"""Declarative Settings tree (no widgets): pages, toggles, exclusive choices and actions.

The tree reads and writes through `SettingsCallbacks` (supplied by the tray) and the config; the
settings view (settings_view.py) only renders it, so every rule lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import COLOR_PALETTES, FLOATING_ANCHORS, ICON_STYLES, LANGUAGES, PANEL_SIZES, UI_SCALES
from .i18n import LANGUAGES as I18N_LANGUAGES, tr

DEFAULT_FLOATING_ANCHOR = "bottom-right"

THRESHOLDS = (10, 15, 20, 25, 30)


@dataclass
class SettingsCallbacks:
    set_bool: Callable[[str, bool], None]         # (config key, value) - persists and notifies
    set_threshold: Callable[[int], None]
    set_palette: Callable[[str], None]            # one of config.COLOR_PALETTES
    set_startup: Callable[[bool], None]
    open_device_log: Callable[[], None]
    open_logs_folder: Callable[[], None]
    startup_enabled: Callable[[], bool]           # real state of the Windows startup entry
    open_detail_folder: Callable[[], None] = lambda: None
    set_icon_style: Callable[[str], None] = lambda style: None      # one of config.ICON_STYLES
    set_placement: Callable[[str], None] = lambda choice: None      # "right" | "left" | "floating"
    set_monitor: Callable[[str], None] = lambda key: None           # "primary" or a monitor key
    reset_position: Callable[[], None] = lambda: None
    set_fullscreen_placement: Callable[[str], None] = lambda choice: None   # config.FULLSCREEN_PLACEMENTS
    list_screens: Callable[[], list] = lambda: []                   # [(key, label)]
    set_floating_anchor: Callable[[str], None] = lambda anchor: None
    current_key: Callable[[], object] = lambda: None                # monitor key the widget is on
    icons_centered: Callable[[], object] = lambda: True             # True / False / None (unknown)
    monitor_has_taskbar: Callable[[], bool] = lambda: True          # does the widget's monitor have a taskbar
    open_custom_icons: Callable[[], None] = lambda: None            # custom icon images dialog
    set_panel_size: Callable[[str], None] = lambda size: None       # one of config.PANEL_SIZES
    set_ui_scale: Callable[[int], None] = lambda pct: None          # one of config.UI_SCALES
    set_language: Callable[[str], None] = lambda lang: None         # one of config.LANGUAGES
    open_tutorial: Callable[[], None] = lambda: None                # the six-step tour
    open_whats_new: Callable[[], None] = lambda: None               # notes of the running version


# ------------------------------------------------------------------ nodes
@dataclass(eq=False, kw_only=True)
class Node:
    id: str = ""


@dataclass(eq=False, kw_only=True)
class Separator(Node):
    pass


@dataclass
class Option:
    """One entry of a Choice. `label` is already translated; `tip` is an optional short hint."""
    value: str
    label: str
    enabled: bool = True
    tip: str = ""


@dataclass(eq=False, kw_only=True)
class Page(Node):
    title_key: str
    children: list = field(default_factory=list)
    icon: str = ""                  # glyph name (icons.glyph_icon) shown on the row that opens the page

    def title(self) -> str:
        return tr(self.title_key)


@dataclass(eq=False, kw_only=True)
class Toggle(Node):
    label_key: str
    get: Callable[[], bool]
    set: Callable[[bool], None]
    tip_key: str = ""               # optional short hint (tooltip / accessible description)

    def label(self) -> str:
        return tr(self.label_key)

    def tip(self) -> str:
        return tr(self.tip_key) if self.tip_key else ""


@dataclass(eq=False, kw_only=True)
class Choice(Node):
    label_key: str
    options: Callable[[], list]     # () -> [Option]; re-evaluated on every refresh
    get: Callable[[], object]
    set: Callable[[object], None]

    def label(self) -> str:
        return tr(self.label_key)

    def current_label(self) -> str:
        cur = self.get()
        for o in self.options():
            if o.value == cur:
                return o.label
        return ""


@dataclass(eq=False, kw_only=True)
class Action(Node):
    label_key: str
    run: Callable[[], None]
    enabled: Callable[[], bool] = lambda: True

    def label(self) -> str:
        return tr(self.label_key)


@dataclass(eq=False)
class SettingsModel:
    root: Page
    callbacks: SettingsCallbacks

    def walk(self, page: Page | None = None):
        """Yield every node below `page` (default: the root), depth first, pages before their children."""
        for node in (page or self.root).children:
            yield node
            if isinstance(node, Page):
                yield from self.walk(node)

    def find(self, node_id: str):
        for node in self.walk():
            if node.id == node_id:
                return node
        return self.root if node_id == self.root.id else None

    def parent_of(self, node_id: str) -> Page | None:
        stack = [self.root]
        while stack:
            page = stack.pop()
            for child in page.children:
                if child.id == node_id:
                    return page
                if isinstance(child, Page):
                    stack.append(child)
        return None

    def path_to(self, page_id: str) -> list[Page]:
        """Pages from the root down to `page_id` (inclusive); [root] when unknown."""
        chain = [self.find(page_id)]
        if not isinstance(chain[0], Page):
            return [self.root]
        while chain[0] is not self.root:
            parent = self.parent_of(chain[0].id)
            if parent is None:
                return [self.root]
            chain.insert(0, parent)
        return chain


# ------------------------------------------------------------------ helpers shared with the tray
def effective_palette(cfg) -> str:
    """Palette in force: the legacy color_mode == "mono" wins over a stale color_palette."""
    if cfg.get("color_mode") == "mono":
        return "mono"
    pal = cfg.get("color_palette")
    return "classic" if pal == "mono" else pal


def placement_choice(cfg) -> str:
    if cfg.get("placement_mode") == "floating":
        return "floating"
    return "left" if cfg.get("taskbar_anchor") == "left" else "right"


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a broken callback must not break the page
        return default


# ------------------------------------------------------------------ builder
def build_settings_model(cfg, cb: SettingsCallbacks) -> SettingsModel:
    """The whole Settings tree. Every value is read lazily, so the view only has to call the getters again."""

    def cfg_toggle(key: str, label_key: str, tip_key: str = "") -> Toggle:
        return Toggle(id=key, label_key=label_key, get=lambda: bool(cfg.get(key)),
                      set=lambda v: cb.set_bool(key, bool(v)), tip_key=tip_key)

    def static_options(values, label_of) -> Callable[[], list]:
        return lambda: [Option(v, label_of(v)) for v in values]

    sources = Page(id="sources", title_key="menu_sources", icon="sources", children=[
        cfg_toggle("wireless_enabled", "wireless_devices"),
        cfg_toggle("bluetooth_enabled", "bluetooth_source"),
    ])

    alerts = Page(id="alerts", title_key="menu_alerts", icon="alerts", children=[
        cfg_toggle("notifications_enabled", "notifications"),
        cfg_toggle("full_charge_alert", "full_charge_alert"),
        Choice(id="low_threshold", label_key="low_threshold",
               options=static_options(THRESHOLDS, lambda v: f"{v} %"),
               get=lambda: int(cfg.get("low_threshold")),
               set=lambda v: cb.set_threshold(int(v))),
    ])

    logs = Page(id="logs", title_key="menu_logs", icon="logs", children=[
        Action(id="open_device_log", label_key="open_device_log", run=lambda: cb.open_device_log()),
        Action(id="open_logs_folder", label_key="open_logs_folder", run=lambda: cb.open_logs_folder()),
        cfg_toggle("detail_log_enabled", "detail_log"),
        Action(id="open_detail_folder", label_key="open_detail_folder", run=lambda: cb.open_detail_folder()),
    ])

    def startup_state() -> bool:
        try:
            return bool(cb.startup_enabled())
        except Exception:  # noqa: BLE001
            return bool(cfg.get("start_with_windows"))

    # ---- position
    def placement_options() -> list:
        centered = _safe(cb.icons_centered, True) is True
        return [Option("right", tr("pos_taskbar_right")),
                Option("left", tr("pos_taskbar_left") if centered else tr("pos_left_fallback"),
                       tip=tr("pos_left_tip")),
                Option("floating", tr("pos_floating"), tip=tr("pos_floating_tip"))]

    def current_anchor() -> str:
        positions = cfg.get("floating_positions")
        positions = positions if isinstance(positions, dict) else {}
        key = _safe(cb.current_key, None)
        entry = positions.get(str(key)) if key is not None else None
        if not isinstance(entry, dict):
            entry = positions.get("primary")
        cur = entry.get("anchor") if isinstance(entry, dict) else None
        return cur if cur in FLOATING_ANCHORS else DEFAULT_FLOATING_ANCHOR

    def monitor_options() -> list:
        try:
            screens = [(str(k), str(lbl)) for k, lbl in cb.list_screens()]
        except Exception:  # noqa: BLE001 - a broken lister must not break the page
            screens = []
        cur = str(cfg.get("placement_monitor"))
        entries = [("primary", tr("monitor_primary"))] + [s for s in screens if s[0] != "primary"]
        opts = [Option(k, lbl) for k, lbl in entries]
        if cur != "primary" and cur not in {o.value for o in opts}:
            opts.append(Option(cur, tr("monitor_disconnected"), enabled=False))
        return opts

    def reset_enabled() -> bool:
        return (placement_choice(cfg) == "floating" or not _safe(cb.monitor_has_taskbar, True)
                or _safe(lambda: cfg.get("fullscreen_placement"), "same") == "floating")

    def fs_options() -> list:
        # Needs "Visible in fullscreen": without it every option is disabled and says why.
        on = bool(_safe(lambda: cfg.get("show_in_fullscreen"), False))
        tip = "" if on else tr("fs_pos_needs_visible")
        values = [("same", tr("fs_pos_same")), ("floating", tr("pos_floating"))]
        values += [(a, tr(f"anchor_{a}")) for a in FLOATING_ANCHORS]
        return [Option(v, lbl, enabled=on, tip=tip) for v, lbl in values]

    position = Page(id="position", title_key="menu_position", icon="position", children=[
        Choice(id="placement", label_key="menu_position", options=placement_options,
               get=lambda: placement_choice(cfg), set=lambda v: cb.set_placement(str(v))),
        Choice(id="float_anchor", label_key="menu_float_anchor",
               options=static_options(FLOATING_ANCHORS, lambda a: tr(f"anchor_{a}")),
               get=current_anchor, set=lambda v: cb.set_floating_anchor(str(v))),
        Choice(id="monitor", label_key="menu_monitor", options=monitor_options,
               get=lambda: str(cfg.get("placement_monitor")), set=lambda v: cb.set_monitor(str(v))),
        cfg_toggle("show_in_fullscreen", "show_in_fullscreen"),
        Choice(id="fullscreen_placement", label_key="menu_fs_position", options=fs_options,
               get=lambda: _safe(lambda: cfg.get("fullscreen_placement"), "same"),
               set=lambda v: cb.set_fullscreen_placement(str(v))),
        Separator(id="position_sep"),
        Action(id="reset_position", label_key="reset_position", run=lambda: cb.reset_position(),
               enabled=reset_enabled),
    ])

    # Language names are shown in their own language, whatever the current UI language is.
    def language_options() -> list:
        return [Option(v, tr("lang_auto") if v == "auto" else I18N_LANGUAGES[v]) for v in LANGUAGES]

    personalization = Page(id="personalization", title_key="menu_personalization", icon="personalization",
                           children=[
        Choice(id="palette", label_key="menu_colors",
               options=static_options(COLOR_PALETTES, lambda n: tr("palette_" + n)),
               get=lambda: effective_palette(cfg), set=lambda v: cb.set_palette(str(v))),
        Choice(id="icon_style", label_key="menu_icon_style",
               options=static_options(ICON_STYLES, lambda n: tr("icon_" + n)),
               get=lambda: cfg.get("icon_style"), set=lambda v: cb.set_icon_style(str(v))),
        Action(id="custom_icons", label_key="custom_icons_menu", run=lambda: cb.open_custom_icons()),
        cfg_toggle("animations_enabled", "animations", "animations_tip"),
        Choice(id="panel_size", label_key="menu_panel_size",
               options=static_options(PANEL_SIZES, lambda n: tr("panel_size_" + n)),
               get=lambda: cfg.get("panel_size"), set=lambda v: cb.set_panel_size(str(v))),
        Choice(id="ui_scale", label_key="menu_ui_scale",
               options=static_options(UI_SCALES, lambda n: f"{n} %"),
               get=lambda: int(cfg.get("ui_scale")), set=lambda v: cb.set_ui_scale(int(v))),
        Choice(id="language", label_key="menu_language", options=language_options,
               get=lambda: cfg.get("language"), set=lambda v: cb.set_language(str(v))),
        position,
    ])

    root = Page(id="root", title_key="menu_settings", children=[
        sources,
        alerts,
        logs,
        Toggle(id="start_with_windows", label_key="start_with_windows", get=startup_state,
               set=lambda v: cb.set_startup(bool(v))),
        personalization,
        Action(id="tutorial", label_key="tutorial_menu", run=lambda: cb.open_tutorial()),
        Action(id="whats_new", label_key="whatsnew_menu", run=lambda: cb.open_whats_new()),
    ])
    return SettingsModel(root, cb)
