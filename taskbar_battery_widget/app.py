"""Application wiring: single instance, monitor, alerts, widget, tray."""
from __future__ import annotations

import copy
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

THEME_REFRESH_MS = 5000
APP_LOG_NAME = "app.log"
PKG_LOGGER = "taskbar_battery_widget"


class _RedactingFormatter(logging.Formatter):
    """Final safety net: MAC addresses, device/HID paths and user names never reach the log file.

    Newlines inside the message are escaped (no forged log lines); the traceback keeps its own lines
    but is redacted too (including a cached ``exc_text``).
    """

    def format(self, record: logging.LogRecord) -> str:
        from .device_history import redact_text
        rec = copy.copy(record)
        msg = redact_text(record.getMessage())
        rec.msg = msg.replace("\r", "\\r").replace("\n", "\\n")
        rec.args = None
        return redact_text(super().format(rec))


def setup_logging(logs_dir: Path | None = None) -> logging.Handler | None:
    """Rotating app.log (1 MB x 3) in the logs folder (see device_history); DEBUG with TBW_DEBUG=1."""
    from . import device_history
    try:
        folder = Path(logs_dir) if logs_dir else device_history.logs_dir()
        handler = device_history.make_file_handler(folder / APP_LOG_NAME)
    except OSError:
        return None
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    debug = os.environ.get("TBW_DEBUG") == "1"
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    # Only our own package logs to app.log: third-party libraries stay at the root default (WARNING)
    # and are never dumped into the file at DEBUG.
    for lg in (logging.getLogger(), logging.getLogger(PKG_LOGGER)):
        for h in list(lg.handlers):
            if getattr(h, "_tbw_app_log", False):
                lg.removeHandler(h)
                h.close()
    handler._tbw_app_log = True                                 # type: ignore[attr-defined]
    pkg = logging.getLogger(PKG_LOGGER)
    pkg.addHandler(handler)
    pkg.setLevel(logging.DEBUG if debug else logging.INFO)
    return handler


def install_excepthooks() -> None:
    """Log uncaught exceptions (main thread and worker threads) to app.log."""
    prev_hook = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            prev_hook(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        try:
            prev_hook(exc_type, exc, tb)
        except Exception:
            pass

    def thread_hook(args) -> None:
        if args.exc_type is SystemExit:
            return
        log.critical("uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook


def run_shutdown(theme_timer, widget, tray, monitor, history, lock, detail=None) -> None:
    """Stop everything; the single-instance lock is released whatever fails along the way."""
    try:
        theme_timer.stop()
        if detail is not None:
            try:
                detail[0].stop()
                detail[1].close()
            except Exception:
                log.exception("detail log close failed")
        widget.stop()
        tray.hide()
        try:
            monitor.stop()
        except Exception:
            log.exception("monitor.stop failed")
        finally:
            history.close()
    finally:
        lock.unlock()


def full_device_list(history, devices, cfg, now: float) -> list:
    """History update that always keeps hidden and disconnected devices in the list.

    The device panel needs the complete list; the widget and the tray filter by `show_disconnected`
    and `hidden_devices` themselves and the alerts ignore hidden/stale/disconnected devices.
    """
    return history.update(devices, now, show_disconnected=True, hidden=(),
                          threshold=int(cfg.get("low_threshold")))


DETAIL_LOG_INTERVAL_MS = 60_000


def ms_to_next_minute(now: float) -> int:
    """Milliseconds from `now` (epoch seconds) to the next minute boundary (1..60000)."""
    ms = int(round((60.0 - (now % 60.0)) * 1000))
    return max(1, min(DETAIL_LOG_INTERVAL_MS, ms))


class DetailLogDriver(QObject):
    """Feeds the detailed logger once per minute, aligned to the minute boundary."""

    def __init__(self, logger, cfg, clock=time.time, parent=None):
        super().__init__(parent)
        self._logger = logger
        self._cfg = cfg
        self._clock = clock
        self._devices: list = []
        self._first = QTimer(self)
        self._first.setSingleShot(True)
        self._first.timeout.connect(self._on_first)
        self._periodic = QTimer(self)
        self._periodic.setInterval(DETAIL_LOG_INTERVAL_MS)
        self._periodic.timeout.connect(self.tick)

    def set_devices(self, devices) -> None:
        self._devices = list(devices)

    def start(self) -> None:
        self._first.setInterval(ms_to_next_minute(self._clock()))
        self._first.start()

    def stop(self) -> None:
        self._first.stop()
        self._periodic.stop()

    def _on_first(self) -> None:
        self.tick()
        self._periodic.start()

    def enabled_keys(self) -> set:
        if not self._cfg.get("detail_log_enabled"):
            return set()
        v = self._cfg.get("detail_log_devices")
        return {k for k in v if isinstance(k, str)} if isinstance(v, list) else set()

    def tick(self) -> None:
        try:
            self._logger.tick(self._devices, self.enabled_keys())
        except Exception:
            log.exception("detail log tick failed")


def open_detail_log(logger, devices, key: str, open_path) -> None:
    """Open the CSV of the device with visibility key `key` (its folder if it was not written yet)."""
    from .models import visibility_key
    dev = next((d for d in devices if visibility_key(d.id) == key), None)
    if dev is None:
        return
    path = logger.path_for(dev)
    if path.exists():
        open_path(path)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("could not create the detailed logs folder")
        return
    open_path(path.parent)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Taskbar Battery Widget")

    lock = QLockFile(str(Path(tempfile.gettempdir()) / "TaskbarBatteryWidget.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return 0

    setup_logging()
    install_excepthooks()
    log.info("starting (debug=%s)", os.environ.get("TBW_DEBUG") == "1")

    from . import taskbar
    from .alerts import AlertManager
    from .config import Config
    from .detail_log import DetailLogger
    from .device_history import DeviceHistory
    from .monitor import DeviceMonitor
    from .tray import TrayController
    from .visibility import visible_devices
    from .widget import TaskbarWidget

    from . import startup
    startup.migrate_legacy()
    cfg = Config()
    try:
        # An entry that already launches this copy (e.g. just migrated from the legacy name) wins over a
        # stale config value, so sync() never removes it.
        if startup.is_enabled() and not cfg.get("start_with_windows"):
            cfg.set("start_with_windows", True)
        # Only packaged builds (installer/portable) re-point the Run value automatically; a source
        # checkout must not steal the startup entry from the installed app just by being launched.
        if getattr(sys, "frozen", False):
            startup.sync(bool(cfg.get("start_with_windows")))
    except Exception:
        log.exception("start-with-Windows sync failed")
    alerts = AlertManager(cfg)
    monitor = DeviceMonitor(cfg=cfg)
    widget = TaskbarWidget(cfg)
    tray = TrayController(cfg)
    widget.set_menu(tray.menu)

    try:
        history = DeviceHistory()
    except Exception:
        log.exception("device history unavailable; starting with an empty store")
        history = DeviceHistory(load=False)
    raw: list = []
    latest: list = []
    detail_logger = DetailLogger()
    detail_driver = DetailLogDriver(detail_logger, cfg)

    def on_devices(devices) -> None:
        raw[:] = devices
        devices = full_device_list(history, devices, cfg, time.time())
        latest[:] = devices
        detail_driver.set_devices(devices)
        widget.set_devices(devices)
        tray.set_devices(devices)
        try:
            for title, body in alerts.check(visible_devices(devices, cfg), time.time()):
                tray.notify(title, body)
        except Exception:
            log.exception("alert check/notify failed")

    def on_radio(on: bool) -> None:
        widget.set_radio(on)
        tray.set_radio(on)

    def on_settings() -> None:
        widget.set_config(cfg)
        if raw:
            on_devices(list(raw))       # show_disconnected / hidden may have changed
        monitor.refresh()

    def refresh_theme() -> None:
        light = taskbar.taskbar_uses_light_theme()
        widget.set_light_theme(light)
        tray.set_dark(not light)

    monitor.devices_changed.connect(on_devices)
    monitor.radio_changed.connect(on_radio)
    tray.refresh_requested.connect(monitor.refresh)
    tray.settings_changed.connect(on_settings)
    tray.quit_requested.connect(app.quit)
    widget.panel_requested.connect(tray.open_panel)
    tray.open_detail_log_requested.connect(
        lambda key: open_detail_log(detail_logger, latest, key, tray.open_path))

    def open_detail_folder() -> None:
        try:
            detail_logger.base.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create the detailed logs folder")
            return
        tray.open_path(detail_logger.base)
    tray.open_detail_folder_requested.connect(open_detail_folder)

    theme_timer = QTimer()
    theme_timer.setInterval(THEME_REFRESH_MS)
    theme_timer.timeout.connect(refresh_theme)

    def shutdown() -> None:
        run_shutdown(theme_timer, widget, tray, monitor, history, lock, (detail_driver, detail_logger))

    app.aboutToQuit.connect(shutdown)

    from .about import AutoUpdateChecker
    auto_updates = AutoUpdateChecker(cfg, tray)

    refresh_theme()
    tray.show()
    widget.start()
    theme_timer.start()
    detail_driver.start()
    monitor.start()
    auto_updates.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
