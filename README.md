# Taskbar Battery Widget

A Windows 11 taskbar widget that shows the battery level of your wireless devices, right next to the
notification area. Spec in `docs/spec.md`.

## Features
- One pill per device (kind icon + `NN%`), live updates, follows the light/dark taskbar theme.
- Turns red at or below a configurable low threshold; low-battery notifications with cooldown.
- Estimated "charging" state, hides over fullscreen apps, tray menu (refresh, show disconnected,
  notifications, low threshold, start with Windows, quit).
- Spanish / English UI depending on the system locale.

## Supported devices
- Bluetooth Classic and BLE devices exposing the standard battery property.
- Logitech HID++ devices (receivers and direct).
- Razer devices using the classic protocol.
- Razer BlackShark V2 HyperSpeed, read directly from its USB dongle.
- HyperX Cloud Flight Wireless, read directly from its USB dongle.

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\pythonw -m taskbar_battery_widget
```
Settings: `%APPDATA%\TaskbarBatteryWidget\config.json` (settings from the previous
`%APPDATA%\BtBatteryBar` folder are copied over on first run).

## Tests
```powershell
.\.venv\Scripts\python -m pytest -q
```

## Limitations
- Windows 11 only; primary taskbar only (bottom edge verified on screen, others by unit tests).
- Devices must expose their battery level through one of the supported methods above.
