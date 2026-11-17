"""System tray icon and menu."""
from __future__ import annotations

import logging
import os
import subprocess
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import aliases
from . import colors
from . import custom_icons
from . import i18n
from . import icons
from . import startup
from . import taskbar
from . import visibility
from .colors import battery_color
from .i18n import tr
from .icons import make_tray_icon
from .panel_model import build_panel_model
from .settings_model import SettingsCallbacks, build_settings_model

log = logging.getLogger(__name__)

PANEL_REOPEN_GRACE_S = 0.3


def has_file_association(ext: str) -> bool:
    """True if Windows has a program to open files with extension `ext` (e.g. ".csv")."""
    if not ext or os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        # ASSOCF_NONE=0, ASSOCSTR_EXECUTABLE=2; S_OK (0) when an executable is associated
        hr = ctypes.windll.shlwapi.AssocQueryStringW(0, 2, ext, "open", buf, ctypes.byref(size))
        exe = buf.value.lower()
        # with no real association Windows answers with the "Open with" chooser (OpenWith.exe)
        return hr == 0 and bool(exe) and not exe.endswith("openwith.exe")
    except Exception:  # noqa: BLE001 - fall back to Notepad
        return False


def apply_appearance(cfg) -> None:
    """Push the colour palette and icon style from the config into the drawing modules."""
    palette = cfg.get("color_palette")
    if cfg.get("color_mode") == "mono":
        palette = "mono"
    elif palette == "mono":
        palette = "classic"
    colors.set_palette(palette, cfg.get("custom_colors"))
    i18n.apply_language_setting(cfg.get("language"))
    setter = getattr(icons, "set_icon_style", None)
    if setter is not None:
        setter(cfg.get("icon_style"))
    custom_icons.set_maps(cfg.get("custom_icons_kind"), cfg.get("custom_icons_device"))


class TrayController(QObject):
    refresh_requested = Signal()
    quit_requested = Signal()
    settings_changed = Signal()
    ui_settings_changed = Signal()      # visibility / recording only: re-filter, no device rescan
    open_detail_log_requested = Signal(str)     # visibility key of the device whose log to open
    open_detail_folder_requested = Signal()
    detail_log_started = Signal(str)     # recording turned on for this visibility key: write a sample now

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._devices = []
        self._radio_on = True
        self._rates: dict | None = None     # key -> Rates (time-left estimates)
        self._dark = True
        self._tray = QSystemTrayIcon(self)
        self._menu = QMenu()
        self._panel = None
        self._about = None
        self._release = None            # newer release found by the automatic check, if any
        self._screen_lister = None      # () -> [(monitor key, label)], set by the app
        self._warned_placement = set()  # warning reasons already shown this session
        self._floating_hinted = False   # the "drag it" hint is shown once per session
        self._current_key_fn = None
        self._floating_anchor_fn = None
        self._has_taskbar_fn = None
        self._settings = self._build_settings()
        self._build_menu()
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

    # ---- tutorial / what's new
    def open_tutorial(self) -> None:
        """The six-step tour (first run, or Settings > Tutorial)."""
        from .tutorial import show_tutorial
        show_tutorial(self._cfg)

    def open_whats_new(self, notes: str | None = None) -> None:
        """Notes of the running version (read locally); a short message when this build has none."""
        from . import __version__, whats_new
        text = whats_new.local_notes(__version__) if notes is None else notes
        if text:
            whats_new.show_whats_new(__version__, text)
            return
        from PySide6.QtWidgets import QMessageBox
        from .dialogs import app_dialog_guard
        box = QMessageBox(QMessageBox.Icon.Information, tr("whatsnew_title", version=__version__),
                          tr("whatsnew_missing"), QMessageBox.StandardButton.Ok)
        with app_dialog_guard():
            box.exec()

    def set_available_release(self, rel) -> None:
        self._release = rel
        if self._about is not None:
            self._about.set_release(rel)

    # ------------------------------------------------------------------ menu
    def _build_settings(self):
        """The Settings tree shown inside the device panel (see settings_model / settings_view)."""
        return build_settings_model(self._cfg, SettingsCallbacks(
            set_bool=self._set_bool, set_threshold=self._set_threshold,
            set_palette=self._set_palette, set_icon_style=self._set_icon_style,
            set_panel_size=self._set_panel_size, set_ui_scale=self._set_ui_scale,
            set_language=self._set_language, set_startup=self._set_startup,
            open_device_log=self.open_device_log, open_logs_folder=self.open_logs_folder,
            open_detail_folder=self.open_detail_folder_requested.emit,
            startup_enabled=lambda: startup.is_enabled(),
            set_placement=self._set_placement, set_monitor=self._set_monitor,
            reset_position=self._reset_position, set_fullscreen_placement=self._set_fullscreen_placement,
            list_screens=self._list_screens,
            open_custom_icons=self.open_custom_icons,
            open_tutorial=lambda: self.open_tutorial(), open_whats_new=lambda: self.open_whats_new(),
            set_floating_anchor=self._set_floating_anchor, current_key=self._current_key,
            icons_centered=self._icons_centered, monitor_has_taskbar=self._monitor_has_taskbar))

    def _build_menu(self) -> None:
        m = self._menu
        m.addAction(tr("refresh")).triggered.connect(self.refresh_requested.emit)
        m.addSeparator()
        self._act_about = m.addAction(tr("menu_about"))
        self._act_about.triggered.connect(self.show_about)
        m.addAction(tr("quit")).triggered.connect(self.quit_requested.emit)

    # ---- logs
    def open_path(self, path) -> None:
        """Open a file or folder with the shell. Folders go through explorer.exe directly: a
        ShellExecute from inside the popup's click handler can silently do nothing."""
        log.info("opening %s", getattr(path, "name", "path"))
        try:
            if os.path.isdir(str(path)):
                subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
            else:
                os.startfile(str(path))
        except (OSError, AttributeError):
            log.exception("could not open %s", getattr(path, "name", "path"))
            self.notify(tr("app_name"), getattr(path, "name", str(path)))

    def open_text_file(self, path) -> None:
        """Open a text/CSV file with its associated program, or Notepad when none is associated
        (otherwise Windows shows an error / "open with" prompt instead of the file)."""
        if has_file_association(getattr(path, "suffix", "")):
            self.open_path(path)
            return
        try:
            subprocess.Popen(["notepad.exe", str(path)], close_fds=True)
        except OSError:
            log.exception("could not open %s in Notepad", getattr(path, "name", "file"))
            self.open_path(path.parent)

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

    def _set_palette(self, name: str) -> None:
        if name == "custom":
            from .custom_colors_dialog import CustomColorsDialog
            from PySide6.QtWidgets import QDialog
            from .dialogs import app_dialog_guard
            dlg = CustomColorsDialog(self._cfg.get("custom_colors"))
            with app_dialog_guard():
                accepted = dlg.exec() == QDialog.DialogCode.Accepted
            if not accepted:
                return
            self._cfg.set("custom_colors", dlg.colors())
        self._cfg.set("color_palette", name)
        # widget.py still reads color_mode: keep it in step with the palette
        self._cfg.set("color_mode", "mono" if name == "mono" else "levels")
        self._appearance_changed()

    def _set_icon_style(self, style: str) -> None:
        self._cfg.set("icon_style", style)
        self._appearance_changed()

    def _set_panel_size(self, size: str) -> None:
        self._cfg.set("panel_size", size)
        self._metrics_changed()

    def _set_ui_scale(self, pct: int) -> None:
        self._cfg.set("ui_scale", int(pct))
        self._metrics_changed()

    def _set_language(self, lang: str) -> None:
        """Change the UI language live. Panel, Settings pages, pill tooltips, tray tooltip and menu are
        redone now; an already open About / statistics window keeps its language until it is reopened."""
        self._cfg.set("language", lang)
        i18n.apply_language_setting(lang)
        self._menu.clear()
        self._build_menu()
        if self._about is not None and not self._about.isVisible():
            self._about.deleteLater()
            self._about = None
        if self._panel is not None:
            self._panel.retranslate()
        self._update_icon()
        self._refresh_panel()
        self.ui_settings_changed.emit()

    def _metrics_changed(self) -> None:
        """Panel size / interface scale changed: reshape the open panel now and re-lay out the pills."""
        self._apply_panel_metrics()
        self.ui_settings_changed.emit()

    def _apply_panel_metrics(self) -> None:
        if self._panel is not None:
            self._panel.set_metrics(self._cfg.get("panel_size"), self._cfg.get("ui_scale"))

    def open_custom_icons(self) -> None:
        """Custom icons dialog: OK saves both maps and redraws; Cancel only removes orphan imports."""
        from PySide6.QtWidgets import QDialog
        from .custom_icons_dialog import CustomIconsDialog
        from .dialogs import app_dialog_guard
        from .models import visibility_key
        devices = [(visibility_key(d.id), d.name, d.kind) for d in self._devices]
        dlg = CustomIconsDialog(self._cfg.get("custom_icons_kind"), self._cfg.get("custom_icons_device"),
                                devices, tr=tr)
        with app_dialog_guard():
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
        if accepted:
            by_kind, by_device = dlg.result_maps()
            try:
                self._cfg.set("custom_icons_kind", by_kind)
                self._cfg.set("custom_icons_device", by_device)
            except ValueError:
                log.exception("could not save the custom icons")
            self._appearance_changed()
        custom_icons.cleanup_unused(set(self._cfg.get("custom_icons_kind").values())
                                    | set(self._cfg.get("custom_icons_device").values()))

    def _on_icon_change(self, key: str) -> None:
        """Panel asked to give one device its own image (alias editor): pick, import, save, redraw."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from .custom_icons_dialog import IMAGE_FILTER
        from .dialogs import app_dialog_guard
        parent = self._panel
        with app_dialog_guard():
            path, _ = QFileDialog.getOpenFileName(parent, tr("custom_icons_choose"), "", IMAGE_FILTER)
        if not path:
            return
        try:
            name = custom_icons.import_image(path)
        except custom_icons.IconImportError as e:
            with app_dialog_guard():
                QMessageBox.warning(parent, tr("custom_icons_title"), tr(e.code))
            return
        self._save_device_icon(key, name)

    def _on_icon_reset(self, key: str) -> None:
        self._save_device_icon(key, None)

    def _save_device_icon(self, key: str, name: str | None) -> None:
        by_device = dict(self._cfg.get("custom_icons_device"))
        if name is None:
            by_device.pop(key, None)
        else:
            by_device[key] = name
        try:
            self._cfg.set("custom_icons_device", by_device)
        except ValueError:
            log.exception("could not save the custom icon")
        self._appearance_changed()
        custom_icons.cleanup_unused(set(self._cfg.get("custom_icons_kind").values())
                                    | set(self._cfg.get("custom_icons_device").values()))

    def _appearance_changed(self) -> None:
        apply_appearance(self._cfg)
        self._update_icon()
        self._refresh_panel()
        self.ui_settings_changed.emit()     # redraw only, no device rescan

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
            self._panel.set_settings_model(self._settings)
            self._apply_panel_metrics()
            self._panel.visibility_toggled.connect(self._on_visibility)
            self._panel.show_disconnected_toggled.connect(self._on_show_disconnected)
            self._panel.show_all_requested.connect(self._on_show_all)
            self._panel.logging_toggled.connect(self._on_logging)
            self._panel.alias_changed.connect(self._on_alias)
            self._panel.icon_change_requested.connect(self._on_icon_change)
            self._panel.icon_reset_requested.connect(self._on_icon_reset)
            self._panel.open_log_requested.connect(self.open_detail_log_requested)
            self._panel.refresh_requested.connect(self.refresh_requested)
            self._panel.settings_requested.connect(self.show_settings_menu)
            self._panel.about_requested.connect(self._on_panel_about)
            self._panel.quit_requested.connect(self._on_panel_quit)
        return self._panel

    def _refresh_panel(self) -> None:
        p = self._panel
        if p is None or not p.isVisible():
            return
        self._push_model(p)
        p.refresh_settings()        # an open Settings page re-reads the config / monitors

    def _push_model(self, p) -> None:
        model = build_panel_model(
            self._devices, visibility.hidden_keys(self._cfg), bool(self._cfg.get("show_disconnected")),
            int(self._cfg.get("low_threshold")), self._dark, self._cfg.get("color_mode") != "mono", radio_on=self._radio_on,
            rates=self._rates)
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

    def _on_panel_about(self) -> None:
        self.show_about()       # the panel stays open but drops its topmost band, so the dialog is drawn in front

    def _on_panel_quit(self) -> None:
        if self._panel is not None:
            self._panel.hide()
        self.quit_requested.emit()

    def show_settings_menu(self, anchor=None) -> None:
        """Show the Settings page inside the device panel (opening the panel first if it is closed).

        `anchor` (the gear button's rect) is only used when the panel has to be opened."""
        p = self._ensure_panel()
        p.set_settings_model(self._settings)
        if not p.isVisible():
            self.open_panel()
        p.show_settings()

    def _on_activated(self, reason) -> None:
        # on Windows the right click arrives as Context (no context menu is set): same as a left click
        if reason not in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.Context):
            return
        p = self._panel
        if p is not None:
            if p.isVisible():
                p.hide()
                return
            hidden_at = getattr(p, "hidden_at", None)
            if hidden_at is not None and 0 <= time.monotonic() - hidden_at < PANEL_REOPEN_GRACE_S:
                return      # this click is the one that just closed the popup
        self.open_panel()

    def _on_visibility(self, key: str, visible: bool) -> None:
        visibility.set_hidden(self._cfg, key, not visible)
        self._changed(ui_only=True)

    def _on_alias(self, key: str, alias: str) -> None:
        if not aliases.set_alias(self._cfg, key, alias):
            self._refresh_panel_force()
            return
        self._changed(ui_only=True)

    def _on_show_disconnected(self, value: bool) -> None:
        self._cfg.set("show_disconnected", bool(value))
        self._changed(ui_only=True)

    def _on_show_all(self) -> None:
        visibility.show_all(self._cfg)
        self._changed(ui_only=True)

    def _on_logging(self, key: str, on: bool) -> None:
        """Record / stop recording one device; the global switch follows the device list."""
        keys = [k for k in self._detail_keys() if k != key]
        if on:
            keys.append(key)
        try:
            self._cfg.set("detail_log_devices", keys)
        except ValueError:
            log.exception("could not save the detailed log devices")
            self._refresh_panel_force()
            return
        # enabling a device turns the global switch on; removing the last one turns it off
        enabled = True if on else (False if not keys else bool(self._cfg.get("detail_log_enabled")))
        try:
            if bool(self._cfg.get("detail_log_enabled")) != enabled:
                self._cfg.set("detail_log_enabled", enabled)
        except ValueError:
            log.exception("could not save the detailed log switch")
        self._changed(ui_only=True)
        if on:
            self.detail_log_started.emit(key)

    def _refresh_panel_force(self) -> None:
        """Redraw the panel from the config (a failed save must not leave the toggle out of step)."""
        p = self._panel
        if p is not None and p.isVisible():
            p._sig = None
            self._push_model(p)

    def _changed(self, ui_only: bool = False) -> None:
        self._update_icon()
        (self.ui_settings_changed if ui_only else self.settings_changed).emit()
        self._refresh_panel()

    def _set_bool(self, key: str, value: bool) -> None:
        self._cfg.set(key, bool(value))
        self.settings_changed.emit()

    def _set_threshold(self, value: int) -> None:
        self._cfg.set("low_threshold", int(value))
        self.settings_changed.emit()

    # ---- widget position
    def set_screen_lister(self, fn) -> None:
        """`fn() -> [(monitor key, label)]`, used to fill Position > Monitor when the menu opens."""
        self._screen_lister = fn

    def _list_screens(self) -> list:
        fn = self._screen_lister
        return list(fn()) if fn is not None else []

    def set_current_screen_key_fn(self, fn) -> None:
        """`fn() -> str | None`: key of the monitor the widget is on (for reset / anchor checks)."""
        self._current_key_fn = fn

    def set_floating_anchor_fn(self, fn) -> None:
        """`fn(anchor)`: moves the floating widget to one of the 8 anchors."""
        self._floating_anchor_fn = fn

    def set_monitor_has_taskbar_fn(self, fn) -> None:
        """`fn() -> bool`: does the widget's current monitor have a taskbar."""
        self._has_taskbar_fn = fn

    def _current_key(self):
        try:
            return self._current_key_fn() if self._current_key_fn is not None else None
        except Exception:  # noqa: BLE001
            log.exception("current screen key failed")
            return None

    def _monitor_has_taskbar(self) -> bool:
        if self._has_taskbar_fn is None:
            return True
        try:
            return bool(self._has_taskbar_fn())
        except Exception:  # noqa: BLE001
            return True

    def _icons_centered(self):
        try:
            return taskbar.taskbar_icons_centered()
        except Exception:  # noqa: BLE001
            return None

    def _set_placement(self, choice: str) -> None:
        if choice == "floating":
            self._cfg.set("placement_mode", "floating")
            if not self._floating_hinted:
                self._floating_hinted = True
                self.notify(tr("app_name"), tr("floating_hint"), info=True)
        else:
            self._cfg.set("placement_mode", "taskbar")
            self._cfg.set("taskbar_anchor", "left" if choice == "left" else "right")
            if choice == "left" and self._icons_centered() is not True:
                # explicit pick: warn right now, and keep the widget's own one-per-session warning quiet
                self._warned_placement.add("left_needs_centered_icons")
                self.notify(tr("app_name"), tr("warn_left_needs_centered"))
        self.settings_changed.emit()

    def _set_floating_anchor(self, anchor: str) -> None:
        switched = self._cfg.get("placement_mode") != "floating"
        if switched:
            self._cfg.set("placement_mode", "floating")
        fn = self._floating_anchor_fn
        if fn is not None:
            try:
                fn(anchor)
            except Exception:  # noqa: BLE001
                log.exception("could not set the floating anchor")
        if switched:
            self.settings_changed.emit()

    def _set_fullscreen_placement(self, choice: str) -> None:
        self._cfg.set("fullscreen_placement", str(choice))
        self.settings_changed.emit()

    def _set_monitor(self, key: str) -> None:
        self._cfg.set("placement_monitor", str(key))
        self.settings_changed.emit()

    def _reset_position(self) -> None:
        """Forget the stored floating position of the current monitor (and the "primary" default entry)
        so the widget falls back to the default one."""
        positions = dict(self._cfg.get("floating_positions") or {})
        key = self._current_key()
        for k in {"primary", str(key) if key is not None else "primary"}:
            positions.pop(k, None)
        self._cfg.set("floating_positions", positions)
        fs = dict(self._cfg.get("fullscreen_floating_positions") or {})    # the fullscreen spot too
        for k in {"primary", str(key) if key is not None else "primary"}:
            fs.pop(k, None)
        self._cfg.set("fullscreen_floating_positions", fs)
        self.settings_changed.emit()

    def show_placement_warning(self, reason: str) -> None:
        """Tell the user, once per session and reason, why the chosen position is not in use."""
        if reason != "left_needs_centered_icons" or reason in self._warned_placement:
            return
        self._warned_placement.add(reason)
        self.notify(tr("app_name"), tr("warn_left_needs_centered"))

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
        """The startup entry could not be changed: the open page re-reads the real state."""
        if self._panel is not None:
            self._panel.refresh_settings()

    # ------------------------------------------------------------------ state
    def set_devices(self, devices) -> None:
        self._devices = list(devices)
        self._update_icon()
        self._refresh_panel()

    def set_rates(self, rates, refresh: bool = False) -> None:
        """key -> log_stats.Rates for recorded devices (time-left estimates). With `refresh`, an open
        panel is redrawn (cheap: the icon and the pills do not change)."""
        self._rates = dict(rates) if rates else None
        if refresh:
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
