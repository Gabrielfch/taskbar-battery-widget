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

from PySide6.QtCore import QLockFile, QTimer
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


def run_shutdown(theme_timer, widget, tray, monitor, history, lock) -> None:
    """Stop everything; the single-instance lock is released whatever fails along the way."""
    try:
        theme_timer.stop()
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
    from .device_history import DeviceHistory
    from .monitor import DeviceMonitor
    from .tray import TrayController
    from .visibility import hidden_keys, visible_devices
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

    def on_devices(devices) -> None:
        raw[:] = devices
        devices = history.update(devices, time.time(),
                                 show_disconnected=bool(cfg.get("show_disconnected")),
                                 hidden=hidden_keys(cfg),
                                 threshold=int(cfg.get("low_threshold")))
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

    theme_timer = QTimer()
    theme_timer.setInterval(THEME_REFRESH_MS)
    theme_timer.timeout.connect(refresh_theme)

    def shutdown() -> None:
        run_shutdown(theme_timer, widget, tray, monitor, history, lock)

    app.aboutToQuit.connect(shutdown)

    from .about import AutoUpdateChecker
    auto_updates = AutoUpdateChecker(cfg, tray)

    refresh_theme()
    tray.show()
    widget.start()
    theme_timer.start()
    monitor.start()
    auto_updates.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
