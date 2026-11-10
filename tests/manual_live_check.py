"""Manual live check: logs DeviceMonitor events and screenshots the taskbar on connect/disconnect.

Run while the app is running, then switch a Bluetooth device on/off.
Usage: .venv\\Scripts\\python tests\\manual_live_check.py [seconds]
"""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskbar_battery_widget import taskbar  # noqa: E402
from taskbar_battery_widget.monitor import DeviceMonitor  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 240

app = QApplication(sys.argv)
t0 = time.monotonic()
last = {"connected": None}
shots = {"n": 0}


def grab(tag: str) -> None:
    info = taskbar.get_taskbar_info()
    screen = app.primaryScreen()
    img = screen.grabWindow(0)
    if info:
        l, t, r, b = info.rect
        dpr = screen.devicePixelRatio()
        img = img.copy(int(l / dpr), int(t / dpr), int((r - l) / dpr), int((b - t) / dpr))
    shots["n"] += 1
    path = OUT / f"screenshot-live-{shots['n']}-{tag}.png"
    img.save(str(path))
    print(f"[{time.monotonic() - t0:6.1f}s] screenshot {path.name}", flush=True)


def on_devices(devs) -> None:
    conn = sorted(f"{d.name} {d.battery}%{' (charging)' if d.charging else ''}" for d in devs if d.connected)
    print(f"[{time.monotonic() - t0:6.1f}s] devices_changed connected={conn}", flush=True)
    key = tuple(conn)
    if last["connected"] is not None and key != last["connected"]:
        before, after = len(last["connected"]), len(key)
        tag = "connected" if after > before else "disconnected" if after < before else "changed"
        QTimer.singleShot(2500, lambda: grab(tag))
    last["connected"] = key


mon = DeviceMonitor()
mon.devices_changed.connect(on_devices)
mon.radio_changed.connect(lambda on: print(f"[{time.monotonic() - t0:6.1f}s] radio {on}", flush=True))
mon.start()
QTimer.singleShot(DURATION * 1000, app.quit)
app.aboutToQuit.connect(mon.stop)
app.exec()
print("done", flush=True)
