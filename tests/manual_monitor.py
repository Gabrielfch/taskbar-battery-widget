"""Manual smoke: DeviceMonitor must emit devices_changed within 5 s. Run: python tests/manual_monitor.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from taskbar_battery_widget.monitor import DeviceMonitor  # noqa: E402

app = QCoreApplication([])
t0 = time.monotonic()
got = []


def on_devices(devs):
    got.append(devs)
    print(f"devices_changed after {time.monotonic() - t0:.2f}s:")
    for d in devs:
        print("  ", d)
    QTimer.singleShot(0, app.quit)


mon = DeviceMonitor()
mon.devices_changed.connect(on_devices)
mon.radio_changed.connect(lambda on: print("radio_changed:", on))
mon.start()
QTimer.singleShot(5000, app.quit)
app.exec()
mon.stop()
print("OK" if got else "FAIL: no devices_changed within 5 s")
sys.exit(0 if got else 1)
