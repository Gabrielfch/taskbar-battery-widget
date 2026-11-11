"""System tray icon and menu."""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import startup
from .colors import battery_color, color_dot_icon
from .i18n import tr
from .icons import make_tray_icon
from .models import visibility_key
from .naming import sanitize_name

log = logging.getLogger(__name__)

THRESHOLDS = (10, 15, 20, 25, 30)


class TrayController(QObject):
    refresh_requested = Signal()
    quit_requested = Signal()
    settings_changed = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._getters: dict = {}
        self._devices = []
        self._radio_on = True
        self._dark = True
        self._tray = QSystemTrayIcon(self)
        self._menu = QMenu()
        self._dev_actions: list[QAction] = []
        self._visible_actions: list[QAction] = []
        self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.setToolTip(tr("app_name"))
        self._update_icon()

    @property
    def menu(self) -> QMenu:
        return self._menu

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        m = self._menu
        # device lines are inserted before this separator every time the menu opens
        self._devices_sep = m.addSeparator()
        m.addAction(tr("refresh")).triggered.connect(self.refresh_requested.emit)
        m.addSeparator()
        show = m.addMenu(tr("menu_show"))
        self._act_disc = self._toggle(tr("show_disconnected"), "show_disconnected", menu=show)
        self._visible_menu = show.addMenu(tr("visible_devices"))
        self._act_colors = self._toggle(tr("color_levels"), "color_mode",
                                        getter=lambda: self._cfg.get("color_mode") != "mono",
                                        setter=self._set_color_mode, menu=show)
        sources = m.addMenu(tr("menu_sources"))
        self._act_wireless = self._toggle(tr("wireless_devices"), "wireless_enabled", menu=sources)
        self._act_exp = self._toggle(tr("experimental_readers"), "experimental_readers",
                                     menu=sources)
        self._act_ds_bt = self._toggle(tr("dualsense_bt_extended"), "dualsense_bt_extended",
                                       menu=sources)
        alerts = m.addMenu(tr("menu_alerts"))
        self._act_notif = self._toggle(tr("notifications"), "notifications_enabled", menu=alerts)
        sub = alerts.addMenu(tr("low_threshold"))
        group = QActionGroup(sub)
        group.setExclusive(True)
        current = int(self._cfg.get("low_threshold"))
        self._thr_actions = []
        for v in THRESHOLDS:
            a = sub.addAction(f"{v} %")
            a.setCheckable(True)
            a.setChecked(v == current)
            group.addAction(a)
            a.triggered.connect(lambda _c=False, val=v: self._set_threshold(val))
            self._thr_actions.append((v, a))
        m.addSeparator()
        m.addAction(tr("open_device_log")).triggered.connect(self.open_device_log)
        m.addAction(tr("open_logs_folder")).triggered.connect(self.open_logs_folder)
        m.addSeparator()
        self._act_start = m.addAction(tr("start_with_windows"))
        self._act_start.setCheckable(True)
        try:
            self._act_start.setChecked(bool(startup.is_enabled()))
        except Exception:
            self._act_start.setChecked(bool(self._cfg.get("start_with_windows")))
        self._act_start.toggled.connect(self._set_startup)
        m.addAction(tr("quit")).triggered.connect(self.quit_requested.emit)
        m.aboutToShow.connect(self._sync_menu)

    # ---- logs
    def _open_path(self, path) -> None:
        try:
            os.startfile(str(path))
        except (OSError, AttributeError):
            log.exception("could not open %s", path.name)
            self.notify(tr("app_name"), str(path))

    def open_device_log(self) -> None:
        from . import device_history
        path = device_history.device_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)       # so the default program has something to open
        except OSError:
            log.exception("could not create the device log")
            return
        self._open_path(path)

    def open_logs_folder(self) -> None:
        from . import device_history
        path = device_history.logs_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create the logs folder")
            return
        self._open_path(path)

    def _toggle(self, label: str, key: str, getter=None, setter=None, menu=None) -> QAction:
        a = (menu or self._menu).addAction(label)
        a.setCheckable(True)
        self._getters[key] = getter or (lambda k=key: bool(self._cfg.get(k)))
        a.setChecked(bool(self._getters[key]()))
        a.toggled.connect(setter or (lambda checked, k=key: self._set_bool(k, checked)))
        return a

    def _set_color_mode(self, levels: bool) -> None:
        self._cfg.set("color_mode", "levels" if levels else "mono")
        self._update_icon()
        self.settings_changed.emit()

    # ---- visible devices submenu
    @staticmethod
    def _menu_text(name: str) -> str:
        """Device names are untrusted: sanitize, then escape '&' so it is not a mnemonic."""
        return (sanitize_name(name) or "?").replace("&", "&&")

    def hidden_ids(self) -> list[str]:
        v = self._cfg.get("hidden_devices")
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    def set_device_visible(self, device_id: str, visible: bool) -> None:
        key = visibility_key(device_id)
        hidden = [x for x in self.hidden_ids() if x != key]
        if not visible:
            hidden.append(key)
        self._save_hidden(hidden)

    def show_all_devices(self) -> None:
        self._save_hidden([])

    def _save_hidden(self, hidden: list[str]) -> None:
        try:
            self._cfg.set("hidden_devices", hidden[-200:])
        except ValueError:
            log.exception("could not save hidden devices")
            return
        self._update_icon()
        self.settings_changed.emit()

    def _rebuild_visible_menu(self) -> None:
        sub = self._visible_menu
        for a in self._visible_actions:
            sub.removeAction(a)
            a.deleteLater()
        self._visible_actions = []
        sub.clear()
        hidden = set(self.hidden_ids())
        seen: set[str] = set()
        for d in self._devices:
            if d.id in seen:
                continue
            seen.add(d.id)
            a = sub.addAction(self._menu_text(d.name))
            a.setCheckable(True)
            a.setChecked(visibility_key(d.id) not in hidden)
            a.triggered.connect(lambda checked=False, i=d.id: self.set_device_visible(i, checked))
            self._visible_actions.append(a)
        if not self._visible_actions:
            a = sub.addAction(tr("no_devices"))
            a.setEnabled(False)
            self._visible_actions.append(a)
        sub.addSeparator()
        show_all = sub.addAction(tr("show_all"))
        show_all.setEnabled(bool(hidden))
        show_all.triggered.connect(lambda _c=False: self.show_all_devices())

    def _set_bool(self, key: str, value: bool) -> None:
        self._cfg.set(key, bool(value))
        self.settings_changed.emit()

    def _set_threshold(self, value: int) -> None:
        self._cfg.set("low_threshold", int(value))
        self.settings_changed.emit()

    def _set_startup(self, enabled: bool) -> None:
        try:
            startup.set_enabled(bool(enabled))
        except Exception:
            log.exception("could not change start-with-Windows")
            self._set_start_checked(not enabled)    # revert the check, do not persist
            return
        self._cfg.set("start_with_windows", bool(enabled))
        self.settings_changed.emit()

    def _set_start_checked(self, value: bool) -> None:
        self._act_start.blockSignals(True)
        self._act_start.setChecked(bool(value))
        self._act_start.blockSignals(False)

    def _sync_menu(self) -> None:
        """Refresh the read-only device list and checkmarks every time the menu opens."""
        for a in self._dev_actions:
            self._menu.removeAction(a)
            a.deleteLater()
        self._dev_actions = []
        hidden = set(self.hidden_ids())
        shown = [d for d in self._devices
                 if visibility_key(d.id) not in hidden
                 and (d.connected or self._cfg.get("show_disconnected"))
                 and (self._radio_on or d.link != "bluetooth")]
        lines = [(self._device_line(d), self._device_color(d)) for d in shown]
        if not self._radio_on:
            lines.insert(0, (tr("bluetooth_off"), None))
        elif not lines:
            lines = [(tr("no_devices"), None)]
        for text, color in lines:
            a = QAction(text.replace("&", "&&"), self._menu)
            if color is not None:
                a.setIcon(color_dot_icon(color))
            a.setEnabled(False)
            self._menu.insertAction(self._devices_sep, a)
            self._dev_actions.append(a)
        for key, act in (("show_disconnected", self._act_disc), ("notifications_enabled", self._act_notif),
                         ("wireless_enabled", self._act_wireless),
                         ("dualsense_bt_extended", self._act_ds_bt),
                         ("experimental_readers", self._act_exp),
                         ("color_mode", self._act_colors)):
            act.blockSignals(True)
            act.setChecked(bool(self._getters[key]()))
            act.blockSignals(False)
        self._rebuild_visible_menu()
        try:
            self._set_start_checked(startup.is_enabled())
        except Exception:
            pass
        cur = int(self._cfg.get("low_threshold"))
        for v, a in self._thr_actions:
            a.setChecked(v == cur)

    def _device_color(self, d):
        """Colour of the level dot in the menu (None when colours are off or no reading)."""
        if (self._cfg.get("color_mode") == "mono" or not d.connected or d.battery is None
                or getattr(d, "stale", False)):
            return None
        return battery_color(d.battery, bool(d.charging), int(self._cfg.get("low_threshold")), self._dark)

    @staticmethod
    def _device_line(d) -> str:
        """'Name: 97 % · Charging' - parts joined with a middle dot."""
        if getattr(d, "stale", False) and d.battery is not None:
            parts = [tr("disconnected") if not d.connected else tr("battery_unknown"),
                     f"{d.battery} %"]
        elif not d.connected:
            parts = [tr("disconnected")]
        elif d.battery is None:
            parts = [tr("battery_unknown")]
        else:
            parts = [f"{d.battery} %"]
            if d.charging:
                parts.append(tr("charging"))
            if getattr(d, "idle", False):
                parts.append(tr("idle"))
        return f"{sanitize_name(d.name) or '?'}: " + " \u00b7 ".join(parts)

    # ------------------------------------------------------------------ state
    def set_devices(self, devices) -> None:
        self._devices = list(devices)
        self._update_icon()

    def set_radio(self, on: bool) -> None:
        self._radio_on = bool(on)
        self._update_icon()

    def set_dark(self, dark: bool) -> None:
        if dark != self._dark:
            self._dark = dark
            self._update_icon()

    def _update_icon(self) -> None:
        hidden = set(self.hidden_ids())
        vis = [d for d in self._devices if visibility_key(d.id) not in hidden]
        levels = [d.battery for d in vis
                  if d.connected and d.battery is not None and not getattr(d, "stale", False)]
        lowest = min(levels) if levels else None
        color = None
        if lowest is not None and self._cfg.get("color_mode") != "mono":
            low_dev = next(d for d in vis if d.connected and d.battery == lowest
                           and not getattr(d, "stale", False))
            color = battery_color(lowest, bool(low_dev.charging), int(self._cfg.get("low_threshold")),
                                  self._dark)
        self._tray.setIcon(make_tray_icon(lowest, self._dark, color))
        tip = tr("app_name")
        if lowest is not None:
            tip += f" - {lowest}%"
        self._tray.setToolTip(tip)

    def notify(self, title: str, body: str) -> None:
        self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Warning, 8000)
