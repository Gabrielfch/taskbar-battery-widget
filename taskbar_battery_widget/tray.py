"""System tray icon and menu."""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import startup
from . import visibility
from .colors import battery_color
from .i18n import tr
from .icons import make_tray_icon
from .panel_model import build_panel_model
from .settings_menu import SettingsCallbacks, build_settings_menu

log = logging.getLogger(__name__)


class TrayController(QObject):
    refresh_requested = Signal()
    quit_requested = Signal()
    settings_changed = Signal()
    open_detail_log_requested = Signal(str)     # visibility key of the device whose log to open
    open_detail_folder_requested = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._devices = []
        self._radio_on = True
        self._dark = True
        self._tray = QSystemTrayIcon(self)
        self._menu = QMenu()
        self._panel = None
        self._about = None
        self._release = None            # newer release found by the automatic check, if any
        self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.setToolTip(tr("app_name"))
        self._update_icon()

    @property
    def menu(self) -> QMenu:
        return self._menu

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        if self._about is not None:
            self._about.shutdown()
        self._tray.hide()

    # ---- about / updates
    def show_about(self) -> None:
        from .about import AboutDialog
        if self._about is None:
            self._about = AboutDialog(self._cfg, release=self._release)
            self._about.quit_requested.connect(self.quit_requested.emit)
        self._about.show()
        self._about.raise_()
        self._about.activateWindow()

    def set_available_release(self, rel) -> None:
        self._release = rel
        if self._about is not None:
            self._about.set_release(rel)

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        m = self._menu
        self._act_devices = m.addAction(tr("menu_devices"))
        self._act_devices.triggered.connect(lambda _c=False: self.open_panel())
        m.addAction(tr("refresh")).triggered.connect(self.refresh_requested.emit)
        self._settings = build_settings_menu(m, self._cfg, SettingsCallbacks(
            set_bool=self._set_bool, set_threshold=self._set_threshold,
            set_color_mode=self._set_color_mode, set_startup=self._set_startup,
            open_device_log=self.open_device_log, open_logs_folder=self.open_logs_folder,
            open_detail_folder=self.open_detail_folder_requested.emit,
            startup_enabled=lambda: startup.is_enabled()))
        acts = self._settings.actions
        self._act_wireless = acts["wireless_enabled"]
        self._act_bluetooth = acts["bluetooth_enabled"]
        self._act_notif = acts["notifications_enabled"]
        self._act_colors = acts["color_mode"]
        self._act_start = acts["start_with_windows"]
        self._thr_actions = self._settings.thr_actions
        m.addSeparator()
        self._act_about = m.addAction(tr("menu_about"))
        self._act_about.triggered.connect(self.show_about)
        m.addAction(tr("quit")).triggered.connect(self.quit_requested.emit)
        m.aboutToShow.connect(self._sync_menu)

    # ---- logs
    def open_path(self, path) -> None:
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
        self.open_path(path)

    def open_logs_folder(self) -> None:
        from . import device_history
        path = device_history.logs_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create the logs folder")
            return
        self.open_path(path)

    def _set_color_mode(self, levels: bool) -> None:
        self._cfg.set("color_mode", "levels" if levels else "mono")
        self._update_icon()
        self.settings_changed.emit()

    # ---- device panel
    def hidden_ids(self) -> list[str]:
        return sorted(visibility.hidden_keys(self._cfg))

    def _detail_keys(self) -> list[str]:
        v = self._cfg.get("detail_log_devices")
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    def _ensure_panel(self):
        if self._panel is None:
            from .device_panel import DevicePanel
            self._panel = DevicePanel(dark=self._dark, show_logging=True)
            self._panel.visibility_toggled.connect(self._on_visibility)
            self._panel.show_disconnected_toggled.connect(self._on_show_disconnected)
            self._panel.show_all_requested.connect(self._on_show_all)
            self._panel.logging_toggled.connect(self._on_logging)
            self._panel.open_log_requested.connect(self.open_detail_log_requested)
        return self._panel

    def _refresh_panel(self) -> None:
        p = self._panel
        if p is None or not p.isVisible():
            return
        self._push_model(p)

    def _push_model(self, p) -> None:
        model = build_panel_model(
            self._devices, visibility.hidden_keys(self._cfg), bool(self._cfg.get("show_disconnected")),
            int(self._cfg.get("low_threshold")), self._dark, self._cfg.get("color_mode") != "mono")
        p.set_model(model, self._detail_keys())

    def open_panel(self, anchor=None) -> None:
        """Open the device panel at `anchor` (QRect/QPoint); default: the tray icon, else the cursor."""
        p = self._ensure_panel()
        p.set_dark(self._dark)
        self._push_model(p)
        if anchor is None:
            g = self._tray.geometry()
            anchor = g if g.isValid() and not g.isEmpty() else QCursor.pos()
        p.show_at(anchor)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_panel()

    def _on_visibility(self, key: str, visible: bool) -> None:
        visibility.set_hidden(self._cfg, key, not visible)
        self._changed()

    def _on_show_disconnected(self, value: bool) -> None:
        self._cfg.set("show_disconnected", bool(value))
        self._changed()

    def _on_show_all(self) -> None:
        visibility.show_all(self._cfg)
        self._changed()

    def _on_logging(self, key: str, on: bool) -> None:
        keys = [k for k in self._detail_keys() if k != key]
        if on:
            keys.append(key)
        try:
            self._cfg.set("detail_log_devices", keys)
            if on:
                self._cfg.set("detail_log_enabled", True)
        except ValueError:
            log.exception("could not save the detailed log devices")
            return
        self._changed()

    def _changed(self) -> None:
        self._update_icon()
        self.settings_changed.emit()
        self._refresh_panel()

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
        self._settings.set_startup_checked(value)

    def _sync_menu(self) -> None:
        """Refresh the checkmarks every time the menu opens."""
        self._settings.sync()

    # ------------------------------------------------------------------ state
    def set_devices(self, devices) -> None:
        self._devices = list(devices)
        self._update_icon()
        self._refresh_panel()

    def set_radio(self, on: bool) -> None:
        self._radio_on = bool(on)
        self._update_icon()
        self._refresh_panel()

    def set_dark(self, dark: bool) -> None:
        if dark != self._dark:
            self._dark = dark
            self._update_icon()
            if self._panel is not None:
                self._panel.set_dark(dark)
                self._refresh_panel()

    def _update_icon(self) -> None:
        vis = [d for d in visibility.visible_devices(self._devices, self._cfg)
               if d.connected or self._cfg.get("show_disconnected")]
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

    def notify(self, title: str, body: str, info: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Information if info else QSystemTrayIcon.MessageIcon.Warning
        self._tray.showMessage(title, body, icon, 8000)
