"""Declarative Settings tree (no widgets): pages, toggles, exclusive choices and actions.

The tree reads and writes through `SettingsCallbacks` (supplied by the tray) and the config; the
settings view (settings_view.py) only renders it, so every rule lives here.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from .config import (ANIMATION_MODES, APP_THEMES, COLOR_PALETTES, FLOATING_ANCHORS, ICON_STYLES, LANGUAGES, PANEL_SIZES,
                     PILL_FONT_WEIGHTS, PILL_STYLES, UI_SCALES)
from .colors import palette_swatch
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
    set_animations: Callable[[str], None] = lambda mode: None       # one of config.ANIMATION_MODES
    set_app_theme: Callable[[str], None] = lambda theme: None       # one of config.APP_THEMES
    set_pill_style: Callable[[str], None] = lambda style: None      # one of config.PILL_STYLES
    set_ui_scale: Callable[[int], None] = lambda pct: None          # one of config.UI_SCALES
    set_language: Callable[[str], None] = lambda lang: None         # one of config.LANGUAGES
    open_tutorial: Callable[[], None] = lambda: None                # the six-step tour
    open_whats_new: Callable[[], None] = lambda: None               # notes of the running version
    set_pill_option: Callable[[str, object], None] = lambda key, value: None    # pill look (no device rescan)


# ------------------------------------------------------------------ nodes
@dataclass(eq=False, kw_only=True)
class Node:
    id: str = ""
    # Re-evaluated on every rebuild: a hidden node gets no row (e.g. options that only apply to a choice).
    visible: Callable[[], bool] = lambda: True
    # Extra words the settings search matches (English synonyms; the translated label always matches).
    keywords: tuple = ()


@dataclass(eq=False, kw_only=True)
class Header(Node):
    """A small section title that groups the rows below it (not focusable, no action)."""
    label_key: str

    def label(self) -> str:
        return tr(self.label_key)


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
    # Optional sample drawn at the end of the option row: ("swatch", [QColor, ...]) colour dots or
    # ("icon", icon_style) a sample device icon in that style. Plain data: the view does the drawing.
    preview: tuple | None = None


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

    def search(self, query: str, limit: int = 30) -> list["SearchHit"]:
        """Rows matching every word of `query` (label, hint, option labels, keywords; case-insensitive).

        Matching ignores accents ("posicion" finds "Posición"). A match on the row's own label ranks
        first, then one on an option or hint. Rows that are hidden right now (their own `visible()` or
        that of a page above them is False) are left out: the hit would open a page without them.
        """
        words = [w for w in _fold(query).split() if w]
        if not words:
            return []
        hits: list[SearchHit] = []
        for node in self.walk():
            if isinstance(node, (Separator, Header)) or not self._shown(node):
                continue
            label = _label_of(node)
            own = _fold(label)
            extra = [_safe_text(lambda: node.tip())] if isinstance(node, Toggle) else []
            if isinstance(node, Choice):
                extra += [o.label for o in _safe(node.options, [])]
            extra += list(node.keywords)
            hay = " ".join([own] + [_fold(e) for e in extra])
            if not all(w in hay for w in words):
                continue
            rank = 0 if all(w in own for w in words) else 1
            page = self.parent_of(node.id)
            trail = [pg.title() for pg in self.path_to(page.id)[1:]] if page is not None else []
            hits.append(SearchHit(node, label, trail, rank))
        hits.sort(key=lambda h: h.rank)     # stable: tree order inside a rank
        return hits[:limit]

    def _shown(self, node) -> bool:
        """The node and every page above it are visible right now."""
        if not _safe(node.visible, True):
            return False
        page = self.parent_of(node.id)
        while page is not None and page is not self.root:
            if not _safe(page.visible, True):
                return False
            page = self.parent_of(page.id)
        return True

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


@dataclass
class SearchHit:
    node: Node
    label: str              # translated label of the row
    trail: list             # titles of the pages that hold it, below the root ([] = on the root page)
    rank: int = 0           # 0: the label matched, 1: an option / hint / keyword matched


def _fold(text: str) -> str:
    """Case- and accent-insensitive form of `text` (NFD, combining marks stripped, casefolded)."""
    decomposed = unicodedata.normalize("NFD", str(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _label_of(node) -> str:
    if isinstance(node, Page):
        return node.title()
    return _safe_text(node.label)


def _safe_text(fn) -> str:
    try:
        return str(fn() or "")
    except Exception:  # noqa: BLE001
        return ""


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

    def cfg_toggle(key: str, label_key: str, tip_key: str = "", keywords: tuple = (), **kw) -> Toggle:
        return Toggle(id=key, label_key=label_key, get=lambda: bool(cfg.get(key)),
                      set=lambda v: cb.set_bool(key, bool(v)), tip_key=tip_key, keywords=keywords, **kw)

    def pill_toggle(key: str, label_key: str, keywords: tuple = (), **kw) -> Toggle:
        return Toggle(id=key, label_key=label_key, get=lambda: cfg.get(key) is not False,
                      set=lambda v: cb.set_pill_option(key, bool(v)), keywords=keywords, **kw)

    def palette_options() -> list:
        custom = _safe(lambda: cfg.get("custom_colors"), None)
        return [Option(n, tr("palette_" + n), preview=("swatch", _safe(lambda n=n: palette_swatch(n, custom), [])))
                for n in COLOR_PALETTES]

    def icon_style_options() -> list:
        return [Option(n, tr("icon_" + n), preview=("icon", n)) for n in ICON_STYLES]

    def static_options(values, label_of) -> Callable[[], list]:
        return lambda: [Option(v, label_of(v)) for v in values]

    def animation_options() -> list:
        return [Option(m, tr("animations_" + m), tip=tr("animations_tip") if m == "system" else "")
                for m in ANIMATION_MODES]

    sources = Page(id="sources", title_key="menu_sources", icon="sources",
                   keywords=("devices", "bluetooth", "wireless", "dongle", "razer", "logitech"), children=[
        cfg_toggle("wireless_enabled", "wireless_devices"),
        cfg_toggle("bluetooth_enabled", "bluetooth_source"),
        cfg_toggle("razer_dongle_wake", "razer_dongle_wake", "razer_dongle_wake_tip"),
    ])

    alerts = Page(id="alerts", title_key="menu_alerts", icon="alerts",
                  keywords=("notifications", "warning", "low battery"), children=[
        cfg_toggle("notifications_enabled", "notifications", keywords=("toast", "popup", "warning")),
        cfg_toggle("full_charge_alert", "full_charge_alert", keywords=("charged", "100", "notification")),
        Choice(id="low_threshold", label_key="low_threshold", keywords=("warning", "percent", "low battery"),
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
        values = [("same", tr("fs_pos_same")), ("floating", tr("pos_floating"))]
        values += [(a, tr(f"anchor_{a}")) for a in FLOATING_ANCHORS]
        return [Option(v, lbl) for v, lbl in values]

    # Rows that only apply to one choice are shown only while that choice is made.
    def is_floating() -> bool:
        return placement_choice(cfg) == "floating"

    def fs_visible() -> bool:
        return bool(_safe(lambda: cfg.get("show_in_fullscreen"), False))

    position = Page(id="position", title_key="menu_position", icon="position",
                    keywords=("place", "location", "corner", "left", "right", "monitor", "screen", "move"), children=[
        Choice(id="placement", label_key="menu_position", options=placement_options,
               get=lambda: placement_choice(cfg), set=lambda v: cb.set_placement(str(v))),
        Choice(id="float_anchor", label_key="menu_float_anchor",
               options=static_options(FLOATING_ANCHORS, lambda a: tr(f"anchor_{a}")),
               get=current_anchor, set=lambda v: cb.set_floating_anchor(str(v)), visible=is_floating),
        Choice(id="monitor", label_key="menu_monitor", options=monitor_options,
               get=lambda: str(cfg.get("placement_monitor")), set=lambda v: cb.set_monitor(str(v))),
        cfg_toggle("stack_devices", "stack_devices", keywords=("column", "vertical", "rows")),
        Toggle(id="attach_to_taskbar", keywords=("autohide", "auto-hide", "auto hide", "smooth", "embed", "slide"), label_key="attach_to_taskbar", tip_key="attach_to_taskbar_tip",
               get=lambda: bool(_safe(lambda: cfg.get("attach_to_taskbar"), False)),
               set=lambda v: cb.set_bool("attach_to_taskbar", bool(v)), visible=lambda: not is_floating()),
        cfg_toggle("show_in_fullscreen", "show_in_fullscreen", keywords=("games", "video", "overlay")),
        Choice(id="fullscreen_placement", label_key="menu_fs_position", options=fs_options,
               get=lambda: _safe(lambda: cfg.get("fullscreen_placement"), "same"),
               set=lambda v: cb.set_fullscreen_placement(str(v)), visible=fs_visible),
        Separator(id="position_sep"),
        Action(id="reset_position", label_key="reset_position", run=lambda: cb.reset_position(),
               enabled=reset_enabled),
    ])

    # Language names are shown in their own language, whatever the current UI language is.
    def language_options() -> list:
        return [Option(v, tr("lang_auto") if v == "auto" else I18N_LANGUAGES[v]) for v in LANGUAGES]

    def is_transparent() -> bool:
        return _safe(lambda: cfg.get("pill_style"), "transparent") == "transparent"

    personalization = Page(id="personalization", title_key="menu_personalization", icon="personalization",
                           keywords=("customize", "customise", "look", "appearance", "style"), children=[
        Header(id="hdr_pills", label_key="section_pills"),
        Choice(id="pill_style", label_key="menu_pill_style",
               options=static_options(PILL_STYLES, lambda n: tr("pill_style_" + n)),
               get=lambda: cfg.get("pill_style"), set=lambda v: cb.set_pill_style(str(v)),
               keywords=("background", "transparent", "compact", "classic", "soft", "shape")),
        Choice(id="palette", label_key="menu_colors", options=palette_options,
               get=lambda: effective_palette(cfg), set=lambda v: cb.set_palette(str(v)),
               keywords=("colour", "colors", "palette", "colorblind", "contrast")),
        Choice(id="icon_style", label_key="menu_icon_style", options=icon_style_options,
               get=lambda: cfg.get("icon_style"), set=lambda v: cb.set_icon_style(str(v)),
               keywords=("symbol", "glyph", "outline", "filled", "retro")),
        Action(id="custom_icons", label_key="custom_icons_menu", run=lambda: cb.open_custom_icons(),
               keywords=("image", "picture", "png", "svg", "gif")),
        Choice(id="pill_font_weight", label_key="pill_font_weight",
               options=static_options(PILL_FONT_WEIGHTS, lambda n: tr("font_weight_" + n)),
               get=lambda: _safe(lambda: cfg.get("pill_font_weight"), "semibold"),
               set=lambda v: cb.set_pill_option("pill_font_weight", str(v)),
               keywords=("font", "text", "bold", "thin", "weight")),
        pill_toggle("pill_percent_sign", "pill_percent_sign", keywords=("%", "percent", "number", "text")),
        pill_toggle("pill_show_names", "pill_show_names", keywords=("name", "label", "text", "device")),
        pill_toggle("pill_show_bar", "pill_show_bar", keywords=("bar", "line", "progress", "gauge")),
        pill_toggle("pill_color_text", "pill_color_text", keywords=("colour", "color", "text", "white", "neutral")),
        pill_toggle("pill_separators", "pill_separators", keywords=("divider", "line", "separator"),
                    visible=is_transparent),
        Header(id="hdr_app", label_key="section_app"),
        Choice(id="app_theme", label_key="menu_theme",
               options=static_options(APP_THEMES, lambda n: tr("theme_" + n)),
               get=lambda: cfg.get("app_theme"), set=lambda v: cb.set_app_theme(str(v)),
               keywords=("dark", "light", "mode", "night")),
        Choice(id="panel_size", label_key="menu_panel_size",
               options=static_options(PANEL_SIZES, lambda n: tr("panel_size_" + n)),
               get=lambda: cfg.get("panel_size"), set=lambda v: cb.set_panel_size(str(v)),
               keywords=("height", "big", "small", "window")),
        Choice(id="ui_scale", label_key="menu_ui_scale",
               options=static_options(UI_SCALES, lambda n: f"{n} %"),
               get=lambda: int(cfg.get("ui_scale")), set=lambda v: cb.set_ui_scale(int(v)),
               keywords=("size", "zoom", "bigger", "smaller", "font", "text")),
        Choice(id="animations", label_key="animations", options=animation_options,
               get=lambda: cfg.get("animations"), set=lambda v: cb.set_animations(str(v)),
               keywords=("motion", "pulse", "blink", "gif", "animated")),
        Choice(id="language", label_key="menu_language", options=language_options,
               get=lambda: cfg.get("language"), set=lambda v: cb.set_language(str(v)),
               keywords=("translation", "english", "deutsch", "espanol", "francais")),
        Separator(id="personalization_sep"),
        position,
    ])

    root = Page(id="root", title_key="menu_settings", children=[
        sources,
        alerts,
        logs,
        Toggle(id="start_with_windows", label_key="start_with_windows", get=startup_state,
               set=lambda v: cb.set_startup(bool(v)), keywords=("autostart", "startup", "boot", "login")),
        personalization,
        Action(id="tutorial", label_key="tutorial_menu", run=lambda: cb.open_tutorial()),
        Action(id="whats_new", label_key="whatsnew_menu", run=lambda: cb.open_whats_new()),
    ])
    return SettingsModel(root, cb)
