"""Builds the Settings submenu of the tray menu from the config and a set of callbacks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu

from .i18n import tr

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


@dataclass
class SettingsMenu:
    menu: QMenu
    actions: dict[str, QAction] = field(default_factory=dict)
    thr_actions: list = field(default_factory=list)
    syncs: list = field(default_factory=list)

    def sync(self) -> None:
        """Refresh every checkmark from the config / system state (signals blocked)."""
        for fn in self.syncs:
            fn()

    def set_startup_checked(self, value: bool) -> None:
        a = self.actions["start_with_windows"]
        a.blockSignals(True)
        a.setChecked(bool(value))
        a.blockSignals(False)


def _toggle(sm: SettingsMenu, menu: QMenu, label: str, name: str, getter, setter) -> QAction:
    a = menu.addAction(label)
    a.setCheckable(True)
    a.setChecked(bool(getter()))
    a.toggled.connect(setter)
    sm.actions[name] = a

    def sync(act=a, get=getter):
        act.blockSignals(True)
        act.setChecked(bool(get()))
        act.blockSignals(False)
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

    def startup_state():
        try:
            return bool(cb.startup_enabled())
        except Exception:
            return bool(cfg.get("start_with_windows"))
    _toggle(sm, root, tr("start_with_windows"), "start_with_windows", startup_state, cb.set_startup)

    pers = root.addMenu(tr("menu_personalization"))
    _toggle(sm, pers, tr("color_levels"), "color_mode",
            lambda: cfg.get("color_mode") != "mono", cb.set_color_mode)
    return sm
