# Backlog

Ordered by delivery sequence (dependencies first, small wins early, largest/riskiest last).
`#n` = number in the original request. Effort: S < 1 day · M 1–3 days · L > 3 days.

| Order | Item | Request | Effort | Depends on | Target |
| --- | --- | --- | --- | --- | --- |
| 1 | Green status dot in the tray device list | #4 | S | — | 1.5.0 |
| 2 | Simplify sources: Bluetooth + 2.4 GHz only, all on by default | #3 | S | — | 1.5.0 |
| 3 | Settings menu with Personalization subgroup | #2 | M | 2 | 1.5.0 |
| 4 | Scrollable device panel (Connected / Known) with eye toggles | #6 | M | 3 | 1.6.0 |
| 5 | Per-device detailed log (1-minute samples), toggle next to the device log | #5 | M | 3, 4 | 1.6.0 |
| 6 | Widget placement: taskbar left/right, other monitors, floating corners/edges, visible in fullscreen | #1 | L | 3 | 1.7.0 |

---

## 1. Green status dot in the tray device list (#4)
The coloured dot shown next to each device in the tray menu list (above *Refresh*) must be green.
- Clarify: green for every connected device, or keep the level colour only in the taskbar pills? Proposed:
  green = connected and reporting, grey = disconnected/stale, red only when at or below the low threshold.
- Acceptance: dot colour follows the rule above in light and dark themes; tests for the colour mapping.

## 2. Simplify sources (#3)
- *Sources* keeps only two toggles: **2.4 GHz wireless** and **Bluetooth**, both **on by default**.
- The "Experimental readers" category is removed: each experimental reader joins the group it belongs to
  (USB dongles → 2.4 GHz; Bluetooth-only readers such as DualSense over BT → Bluetooth).
- The "DualSense over Bluetooth (extended mode)" toggle is folded into *Bluetooth*.
- Migration: existing configs map `wireless_enabled`, `experimental_readers`, `dualsense_bt_extended` to the
  two new keys (on unless the user had turned the whole source off).
- **Decision to confirm:** this turns the unverified (🧪) readers **on by default**. They only send whitelisted
  query commands, but they have not been tested on real hardware. Option: keep a hidden config key
  `experimental_readers` (default on) for support/debugging, and keep the 🧪 marks in the README.
- Acceptance: tray shows exactly two source toggles; scan honours them; migration tests; README updated.

## 3. Settings menu (#2)
Group the configuration entries under **Settings**, leaving the top level short.
- Top level: device list, *Refresh*, *Settings ▸*, *About…*, *Quit*.
- *Settings ▸*: *Sources ▸* (2.4 GHz, Bluetooth), *Alerts ▸* (low-battery notification, threshold),
  *Logs ▸* (open device log, open logs folder; later the detailed-log toggle from item 5),
  *Start with Windows*, *Personalization ▸*.
- *Personalization ▸*: colour by battery level (and future: theme, pill size, placement shortcut).
- Acceptance: menu structure test updated; every existing action reachable; ES/EN strings.

## 4. Scrollable device panel (#6)
Replace the device list inside the tray menu and the *Show ▸ Disconnected / Visible devices* submenus with
a scrollable panel opened from the menu.
- Two sections: **Connected** and **Known** (disconnected / last known value).
- Each row: kind icon, name, level/status, and an **eye** button to show/hide that device on the taskbar.
- The **Known** section header has its own eye button = show/hide disconnected devices on the taskbar
  (replaces the *Show disconnected* toggle).
- Keyboard and screen-reader accessible; scrolls when there are many devices; ES/EN.
- Acceptance: hidden state persists (`hidden_devices`), header eye persists (`show_disconnected`);
  the old *Show* submenu is removed; tests for grouping, toggles and persistence.

## 5. Per-device detailed log (#5)
Optional per-device history sampled **once per minute** (date/time, battery, charging, connected, link).
- Enabled per device from an eye/record toggle in the device panel row, and globally from
  *Settings ▸ Logs* next to *Open device log* (button to open that device's log).
- Storage: `%LOCALAPPDATA%\TaskbarBatteryWidget\logs\devices\<hash>.csv`, rotating (e.g. 1 MB × 3 per
  device), no MAC/serial (hash key + sanitized name), written off the UI thread.
- Size estimate: ~60 B/line × 1 440 lines/day ≈ 85 KB/day/device → 1 MB lasts ~12 days; capped at 4 MB.
- Acceptance: toggle per device persists; 1-minute cadence verified with a fake clock; rotation and
  redaction tests; "open log" opens the device file.

## 6. Widget placement (#1)
Let the user choose where the pills are shown:
- **Taskbar**: right (next to the tray, current), left (next to Start), centre.
- **Monitor**: primary or a specific monitor (and the taskbar of that monitor when it has one).
- **Floating**: a small always-on-top widget anchored to a screen corner/edge (top/bottom × left/centre/right),
  draggable, remembers its position per monitor.
- **Fullscreen**: option to stay visible over fullscreen apps (default: hide, as today).
- Notes: secondary taskbars (`Shell_SecondaryTrayWnd`), per-monitor DPI, monitor hot-plug, auto-hide taskbars,
  and the Windows 11 flyout layer limitation (still hidden while Quick Settings/Start are open).
- Acceptance: placement chosen from *Settings ▸ Personalization ▸ Position*; survives restart and monitor
  changes; tests for placement math per mode; manual check on 1 and 2 monitors.
