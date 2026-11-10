"""Application wiring: single instance, monitor, alerts, widget, tray."""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

THEME_REFRESH_MS = 5000


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Taskbar Battery Widget")

    lock = QLockFile(str(Path(tempfile.gettempdir()) / "TaskbarBatteryWidget.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return 0

    from . import taskbar
    from .alerts import AlertManager
    from .config import Config
    from .monitor import DeviceMonitor
    from .tray import TrayController
    from .visibility import visible_devices
    from .widget import TaskbarWidget

    from . import startup
    startup.migrate_legacy()
    cfg = Config()
    alerts = AlertManager(cfg)
    monitor = DeviceMonitor(cfg=cfg)
    widget = TaskbarWidget(cfg)
    tray = TrayController(cfg)
    widget.set_menu(tray.menu)

    def on_devices(devices) -> None:
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
        theme_timer.stop()
        widget.stop()
        tray.hide()
        try:
            monitor.stop()
        except Exception:
            log.exception("monitor.stop failed")
        finally:
            lock.unlock()

    app.aboutToQuit.connect(shutdown)

    refresh_theme()
    tray.show()
    widget.start()
    theme_timer.start()
    monitor.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
