"""Builds the Settings submenu of the tray menu from the config and a set of callbacks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu

from .config import FLOATING_ANCHORS
from .i18n import tr

DEFAULT_FLOATING_ANCHOR = "bottom-right"

THRESHOLDS = (10, 15, 20, 25, 30)


@dataclass
class SettingsCallbacks:
    set_bool: Callable[[str, bool], None]         # (config key, value) - persists and notifies
    set_threshold: Callable[[int], None]
    set_color_mode: Callable[[bool], None]        # True = colour by level
    set_startup: Callable[[bool], None]
    open_device_log: Callable[[], None]
    open_logs_folder: Callable[[], None]
    startup_enabled: Callable[[], bool]           # real state of the Windows startup entry
    open_detail_folder: Callable[[], None] = lambda: None
    set_placement: Callable[[str], None] = lambda choice: None      # "right" | "left" | "floating"
    set_monitor: Callable[[str], None] = lambda key: None           # "primary" or a monitor key
    reset_position: Callable[[], None] = lambda: None
    list_screens: Callable[[], list] = lambda: []                   # [(key, label)]
    set_floating_anchor: Callable[[str], None] = lambda anchor: None
    current_key: Callable[[], object] = lambda: None                # monitor key the widget is on
    icons_centered: Callable[[], object] = lambda: True             # True / False / None (unknown)
    monitor_has_taskbar: Callable[[], bool] = lambda: True          # does the widget's monitor have a taskbar


@dataclass
class SettingsMenu:
    menu: QMenu
    actions: dict[str, QAction] = field(default_factory=dict)
    thr_actions: list = field(default_factory=list)
    syncs: list = field(default_factory=list)
    pos_actions: dict = field(default_factory=dict)         # "right" | "left" | "floating" -> action
    monitor_actions: dict = field(default_factory=dict)     # monitor key -> action
    anchor_actions: dict = field(default_factory=dict)      # floating anchor -> action
    reset_action: object = None
    anchor_menu: object = None
    position_menu: object = None
    monitor_menu: object = None

    def sync(self) -> None:
        """Refresh every checkmark from the config / system state (signals blocked)."""
        for fn in self.syncs:
            fn()

    def set_startup_checked(self, value: bool) -> None:
        a = self.actions["start_with_windows"]
        was = a.blockSignals(True)
        a.setChecked(bool(value))
        a.blockSignals(was)


def _toggle(sm: SettingsMenu, menu: QMenu, label: str, name: str, getter, setter) -> QAction:
    a = menu.addAction(label)
    a.setCheckable(True)
    a.setChecked(bool(getter()))
    a.toggled.connect(setter)
    sm.actions[name] = a

    def sync(act=a, get=getter):
        was = act.blockSignals(True)
        act.setChecked(bool(get()))
        act.blockSignals(was)
    sm.syncs.append(sync)
    return a


def build_settings_menu(parent: QMenu, cfg, cb: SettingsCallbacks) -> SettingsMenu:
    """Add 'Settings >' to `parent` and return the object whose sync() refreshes its checkmarks."""
    root = parent.addMenu(tr("menu_settings"))
    sm = SettingsMenu(root)

    def cfg_bool(key):
        return lambda: bool(cfg.get(key))

    def cfg_setter(key):
        return lambda checked: cb.set_bool(key, checked)

    sources = root.addMenu(tr("menu_sources"))
    _toggle(sm, sources, tr("wireless_devices"), "wireless_enabled",
            cfg_bool("wireless_enabled"), cfg_setter("wireless_enabled"))
    _toggle(sm, sources, tr("bluetooth_source"), "bluetooth_enabled",
            cfg_bool("bluetooth_enabled"), cfg_setter("bluetooth_enabled"))

    alerts = root.addMenu(tr("menu_alerts"))
    _toggle(sm, alerts, tr("notifications"), "notifications_enabled",
            cfg_bool("notifications_enabled"), cfg_setter("notifications_enabled"))
    sub = alerts.addMenu(tr("low_threshold"))
    group = QActionGroup(sub)
    group.setExclusive(True)
    for v in THRESHOLDS:
        a = sub.addAction(f"{v} %")
        a.setCheckable(True)
        group.addAction(a)
        a.triggered.connect(lambda _c=False, val=v: cb.set_threshold(val))
        sm.thr_actions.append((v, a))

    def sync_threshold():
        cur = int(cfg.get("low_threshold"))
        for v, act in sm.thr_actions:
            act.setChecked(v == cur)
    sync_threshold()
    sm.syncs.append(sync_threshold)

    logs = root.addMenu(tr("menu_logs"))
    logs.addAction(tr("open_device_log")).triggered.connect(lambda _c=False: cb.open_device_log())
    logs.addAction(tr("open_logs_folder")).triggered.connect(lambda _c=False: cb.open_logs_folder())
    _toggle(sm, logs, tr("detail_log"), "detail_log_enabled",
            cfg_bool("detail_log_enabled"), cfg_setter("detail_log_enabled"))
    logs.addAction(tr("open_detail_folder")).triggered.connect(lambda _c=False: cb.open_detail_folder())

    def startup_state():
        try:
            return bool(cb.startup_enabled())
        except Exception:
            return bool(cfg.get("start_with_windows"))
    _toggle(sm, root, tr("start_with_windows"), "start_with_windows", startup_state, cb.set_startup)

    pers = root.addMenu(tr("menu_personalization"))
    _toggle(sm, pers, tr("color_levels"), "color_mode",
            lambda: cfg.get("color_mode") != "mono", cb.set_color_mode)
    _build_position_menu(sm, pers, cfg, cb)
    return sm


def _placement_choice(cfg) -> str:
    if cfg.get("placement_mode") == "floating":
        return "floating"
    return "left" if cfg.get("taskbar_anchor") == "left" else "right"


def _build_position_menu(sm: SettingsMenu, pers: QMenu, cfg, cb: SettingsCallbacks) -> None:
    pos = pers.addMenu(tr("menu_position"))
    pos.setToolTipsVisible(True)
    sm.position_menu = pos
    group = QActionGroup(pos)
    group.setExclusive(True)
    for choice, label_key, tip_key in (("right", "pos_taskbar_right", None),
                                       ("left", "pos_taskbar_left", "pos_left_tip"),
                                       ("floating", "pos_floating", "pos_floating_tip")):
        a = pos.addAction(tr(label_key))
        a.setCheckable(True)
        group.addAction(a)
        if tip_key:
            a.setToolTip(tr(tip_key))
            a.setStatusTip(tr(tip_key))
        a.triggered.connect(lambda _c=False, ch=choice: cb.set_placement(ch))
        sm.pos_actions[choice] = a

    def safe(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a broken callback must not break the menu
            return default

    def sync_position():
        cur = _placement_choice(cfg)
        for ch, act in sm.pos_actions.items():
            was = act.blockSignals(True)
            act.setChecked(ch == cur)
            act.blockSignals(was)
        left = sm.pos_actions["left"]
        left.setText(tr("pos_taskbar_left") if safe(cb.icons_centered, True) is True
                     else tr("pos_left_fallback"))
        if sm.reset_action is not None:
            sm.reset_action.setEnabled(cur == "floating" or not safe(cb.monitor_has_taskbar, True))
    sm.syncs.append(sync_position)

    anchors = pos.addMenu(tr("menu_float_anchor"))
    sm.anchor_menu = anchors
    agroup = QActionGroup(anchors)
    agroup.setExclusive(True)
    for anchor in FLOATING_ANCHORS:
        a = anchors.addAction(tr(f"anchor_{anchor}"))
        a.setCheckable(True)
        agroup.addAction(a)
        a.triggered.connect(lambda _c=False, an=anchor: cb.set_floating_anchor(an))
        sm.anchor_actions[anchor] = a

    def sync_anchor():
        positions = cfg.get("floating_positions")
        positions = positions if isinstance(positions, dict) else {}
        key = safe(cb.current_key, None)
        entry = positions.get(str(key)) if key is not None else None
        if not isinstance(entry, dict):
            entry = positions.get("primary")
        cur = entry.get("anchor") if isinstance(entry, dict) else None
        if cur not in sm.anchor_actions:
            cur = DEFAULT_FLOATING_ANCHOR
        for an, act in sm.anchor_actions.items():
            was = act.blockSignals(True)
            act.setChecked(an == cur)
            act.blockSignals(was)
    sync_anchor()
    sm.syncs.append(sync_anchor)

    mon = pos.addMenu(tr("menu_monitor"))
    sm.monitor_menu = mon
    mgroup = QActionGroup(mon)
    mgroup.setExclusive(True)

    def rebuild_monitors():
        for act in list(sm.monitor_actions.values()):
            mon.removeAction(act)
            mgroup.removeAction(act)
            act.deleteLater()
        sm.monitor_actions.clear()
        try:
            screens = [(str(k), str(lbl)) for k, lbl in cb.list_screens()]
        except Exception:  # noqa: BLE001 - a broken lister must not break the menu
            screens = []
        cur = str(cfg.get("placement_monitor"))
        entries = [("primary", tr("monitor_primary"))] + [s for s in screens if s[0] != "primary"]
        for key, label in entries:
            a = mon.addAction(label)
            a.setCheckable(True)
            mgroup.addAction(a)
            was = a.blockSignals(True)
            a.setChecked(key == cur)
            a.blockSignals(was)
            a.triggered.connect(lambda _c=False, k=key: cb.set_monitor(k))
            sm.monitor_actions[key] = a
        if cur != "primary" and cur not in sm.monitor_actions:
            a = mon.addAction(tr("monitor_disconnected"))
            a.setCheckable(True)
            mgroup.addAction(a)
            a.setChecked(True)
            a.setEnabled(False)
            sm.monitor_actions[cur] = a
    rebuild_monitors()
    mon.aboutToShow.connect(rebuild_monitors)
    sm.syncs.append(rebuild_monitors)

    _toggle(sm, pos, tr("show_in_fullscreen"), "show_in_fullscreen",
            lambda: bool(cfg.get("show_in_fullscreen")), lambda v: cb.set_bool("show_in_fullscreen", v))
    pos.addSeparator()
    sm.reset_action = pos.addAction(tr("reset_position"))
    sm.reset_action.triggered.connect(lambda _c=False: cb.reset_position())
    sync_position()
