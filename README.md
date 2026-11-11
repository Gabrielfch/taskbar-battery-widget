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
tested on hardware) · 🧪 experimental reader, implemented from public protocol notes and **off by default** —
enable it in the tray menu *Sources → Experimental readers (unverified)* · ❌ not supported.

No vendor software (Synapse, G HUB, NGENUITY…) is needed; it may stay open at the same time. Every reader
only sends read/query commands from a fixed whitelist; a device that doesn't answer keeps its last value
until the idle grace period ends.

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
| Old HID++ 1.0-only devices (pre-2013 Unifying) — battery registers 0x07 / 0x0D (read only) | 🟢 |
| Headsets G533, G535, G633 / G635 / G733 / G933 / G935, G PRO / PRO X (older) | 🧪 |
| Headsets PRO X 2 LIGHTSPEED, PRO X 3 LIGHTSPEED, G522 LIGHTSPEED (Centurion protocol) | 🧪 |
| Astro A50 Gen 5 | 🧪 |
| G930, Astro A50 Gen 4 and older Astro | ❌ |

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
| BlackShark V2 Pro PlayStation edition (needs a mode-changing command) | — | ❌ |
| Other Razer headsets with their own dongle protocol | — | ❌ |

### HyperX
| Device | Status |
| --- | --- |
| Cloud Flight Wireless (0951:16C4) — battery estimated from voltage, charging detected | ✅ |
| Cloud Flight Wireless, newer revision (0951:1723) | 🟢 |
| Cloud II Wireless (HP 03F0:0696, 03F0:018B; Kingston 0951:1718) | 🧪 |
| Cloud Alpha Wireless (03F0:098D), Cloud Alpha 2 Wireless (03F0:08BE) | 🧪 |
| Cloud III Wireless, Cloud Alpha S, Stinger | ❌ |

### SteelSeries (1038) 🧪
| Device | Status |
| --- | --- |
| Arctis 1 / 1 Xbox / 7X / 7P, Arctis 7 / 7 (2019), Arctis 9, Arctis 7+ | 🧪 |
| Arctis Nova 3P / 3X, Nova 5 / 5X, Nova 7 / 7X / 7P (and Diablo / WoW editions), Nova Pro Wireless | 🧪 |
| Arctis Pro Wireless, Arctis GameBuds | 🧪 |

### Corsair (1B1C) 🧪
| Device | Status |
| --- | --- |
| Void / Void Pro / Void Elite / Void RGB wireless family (legacy protocol) | 🧪 |
| Virtuoso RGB Wireless XT / SE | 🧪 |
| Void Wireless V2, Virtuoso Max Wireless, HS80 Max Wireless | 🧪 |

### Audeze, Jabra, Lenovo, MCHOSE 🧪
| Device | Status |
| --- | --- |
| Audeze Maxwell, Maxwell 2 (PC and Xbox dongles) | 🧪 |
| Jabra Link 380, Jabra Link 390, Jabra Evolve2 65 Flex (USB) | 🧪 |
| Lenovo Wireless VoIP headset | 🧪 |
| MCHOSE X9, MCHOSE V9 Pro | 🧪 |
| Plantronics / Poly BT600 (needs session commands), Roccat Elo 7.1 Air (no battery query) | ❌ |

### Sony
| Device | Status |
| --- | --- |
| DualSense over USB (battery in 10 % steps + charging) | ✅ |
| DualSense over Bluetooth (switched to extended reports once per connection; can be turned off in *Sources*) | 🟢 |
| DualSense Edge (USB / Bluetooth) | 🟢 |
| DualShock 4 v1 / v2 and DS4 USB wireless adapter (USB / Bluetooth) | 🧪 |
| PlayStation PULSE 3D headset (shown after the headset sends a status update) | 🧪 |
| INZONE H5, INZONE H9 II, INZONE Buds | 🧪 |

### Xbox
| Device | Status |
| --- | --- |
| Xbox controllers over Bluetooth | ✅ (see Bluetooth) |
| Xbox controllers through the Xbox Wireless Adapter or any XInput pad (battery in 4 steps: 5 / 30 / 65 / 100 %) | 🧪 |
| Xbox controller on a USB cable (Windows reports no battery level) | ❌ |

### Not supported yet
ASUS ROG, Turtle Beach, JBL and other vendor-specific dongles not listed above. Each needs its own protocol
reader.

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
