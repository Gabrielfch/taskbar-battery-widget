# Taskbar Battery Widget

A Windows 11 taskbar widget that shows the battery level of your wireless devices, right next to the
notification area. Spec in `docs/spec.md`.

## Features
- One pill per device (kind icon + `NN%`), live updates, follows the light/dark taskbar theme.
- Turns red at or below a configurable low threshold; low-battery notifications with cooldown.
- Estimated "charging" state, hides over fullscreen apps, tray menu (refresh, show disconnected,
  notifications, low threshold, start with Windows, quit).
- Spanish / English UI depending on the system locale.

## Compatibility

Legend: ✅ verified on real hardware · 🟢 implemented from the device's protocol, expected to work (not yet
tested on hardware) · ❌ not supported.

No vendor software (Synapse, G HUB, NGENUITY…) is needed; it may stay open at the same time.

### Bluetooth (Classic and BLE)
Any paired device whose battery level Windows itself shows in *Settings → Bluetooth & devices*
(standard battery property).

| Device | Status |
| --- | --- |
| Xbox Wireless Controller (Bluetooth) | ✅ |
| Razer BlackShark V2 HyperSpeed (Bluetooth mode) | ✅ |
| Headphones, earbuds, mice, keyboards and pens that report battery to Windows | 🟢 |
| Devices that don't report battery to Windows (e.g. DualSense over BT → see Sony below) | ❌ via this path |

### Logitech (HID++ 2.0)
Devices behind a Logitech receiver or connected directly by USB, reporting battery through HID++ 2.0
features *Unified Battery* (0x1004), *Battery Status* (0x1000), *Battery Voltage* (0x1001) or
*ADC Measurement* (0x1F20).

| Device | Status |
| --- | --- |
| G903 LIGHTSPEED mouse (receiver C539) | ✅ |
| G915 WIRELESS keyboard (receiver C547) | ✅ |
| Other LIGHTSPEED, Unifying and Bolt receivers: PIDs C52B, C52F, C531, C532, C534, C539, C53A, C53D, C53F, C541, C545, C547, C548, C54D, C70E, C713 (or any HID++ device named "Receiver") | 🟢 |
| Recent Logitech mice, keyboards and LIGHTSPEED headsets (G/MX/ERGO/POP series) with HID++ 2.0 | 🟢 |
| Old HID++ 1.0-only devices (pre-2013 Unifying) | ❌ |

### Razer
| Device | Method | Status |
| --- | --- | --- |
| BlackShark V2 HyperSpeed — 2.4 GHz dongle 1532:0565 (battery + charging) | Direct dongle protocol | ✅ |
| Basilisk V3 Pro (wired/wireless), Basilisk V3 X HyperSpeed, Basilisk X HyperSpeed, Basilisk Ultimate (+ dongle) | Classic 90-byte protocol | 🟢 |
| DeathAdder V2 Pro (wired/wireless), DeathAdder V2 X HyperSpeed, DeathAdder V3 Pro (wired/wireless) | Classic 90-byte protocol | 🟢 |
| Naga V2 Pro (wired/wireless), Viper V2 Pro (wired/wireless), Viper Ultimate (wired/wireless/dongle) | Classic 90-byte protocol | 🟢 |
| Cobra Pro (wired/wireless), Mouse Dock Pro, HyperPolling Wireless Dongle | Classic 90-byte protocol | 🟢 |
| BlackShark V2 Pro (RZ04-0322 and RZ04-0453) | Classic 90-byte protocol | 🟢 |
| Other Razer devices exposing the classic 90-byte feature report | Classic protocol (generic) | 🟢 |
| Other Razer headsets with their own dongle protocol | — | ❌ |

### HyperX
| Device | Status |
| --- | --- |
| Cloud Flight Wireless (0951:16C4) — battery estimated from voltage, charging detected | ✅ |
| Cloud Flight Wireless, newer revision (0951:1723) | 🟢 |
| Other HyperX headsets (Cloud II/III Wireless, Alpha Wireless, Stinger…) | ❌ |

### Sony
| Device | Status |
| --- | --- |
| DualSense over USB (battery in 10 % steps + charging) | ✅ |
| DualSense over Bluetooth (switched to extended reports once per connection; can be turned off in *Sources*) | 🟢 |
| DualSense Edge (USB / Bluetooth) | 🟢 |
| DualShock 4 | ❌ |

### Not supported yet
Xbox controllers through the Xbox Wireless Adapter (2.4 GHz), SteelSeries, Corsair, ASUS ROG, Turtle Beach,
Audeze, JBL and other vendor-specific dongles. Each needs its own protocol reader.

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

## Build / Install
Users only need to run `TaskbarBatteryWidget-Setup-<version>.exe` (per-user install, no admin rights) or the
single-file `TaskbarBatteryWidget-portable-<version>.exe`. The builds are unsigned, so Windows SmartScreen
may warn ("More info" -> "Run anyway").

To build both (requires PyInstaller in the venv and Inno Setup 6):
```powershell
.\packaging\build.ps1
```
It runs the tests, generates the icon, builds the onedir and onefile apps and the installer, and writes
`dist\TaskbarBatteryWidget-Setup-<version>.exe` and `dist\TaskbarBatteryWidget-portable-<version>.exe`.

## Limitations
- While a Windows 11 shell flyout is open (Quick Settings, Start, notifications), the taskbar is drawn in a
  system layer above all normal windows, so the widget is hidden until the flyout closes. Avoiding this
  requires UIAccess (signed exe installed under Program Files).
- Windows 11 only; primary taskbar only (bottom edge verified on screen, others by unit tests).
- Devices must expose their battery level through one of the supported methods above.
