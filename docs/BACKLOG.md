# Backlog

Ordered by delivery sequence (dependencies first, small wins early, largest/riskiest last).
`#n` = number in the original request. Effort: S < 1 day · M 1–3 days · L > 3 days.

| Order | Item | Request | Effort | Depends on | Target |
| --- | --- | --- | --- | --- | --- |
| — | ~~Green status dot in the tray device list~~ — dropped, covered by item 4 | #4 | — | — | — |
| 1 ✅ | Simplify sources: Bluetooth + 2.4 GHz only, all on by default (except beta readers) | #3 | S | — | 1.5.0 |
| 2 ✅ | Settings menu with Personalization subgroup | #2 | M | 1 | 1.5.0 |
| 3 ✅ | Scrollable device panel (Connected / Known) with eye toggles and status colours | #6 + #4 | M | 2 | 1.5.0 |
| 4 ✅ | Per-device detailed log (1-minute samples), toggle next to the device log | #5 | M | 2, 3 | 1.5.0 |
| 5 ✅ | Widget placement: taskbar left/right, other monitors, floating corners/edges, visible in fullscreen | #1 | L | 2 | 1.6.2 |
| 6 ✅ | Fix: Razer Firefly (mouse pad, no battery) shown as a mouse at 0 % | issue #2 log | S | — | 1.12.0 |
| 7 | Razer BlackShark V2 Pro over 2.4 GHz (1532:0555) not shown — waiting for the reporter's debug log | issue #2 | S–M | — | next |
| 8 | User-defined device order (drag in the panel, keyboard alternative) | #7 | M | — | — |
| 9 ✅ | Pill styles: Classic / Soft / Transparent (new default) / Transparent compact | #8 | M | — | 1.12.0 |
| 10 ✅ | Stacked layout (one device per row) for floating corner positions | #9 | M | 9 | 1.12.0 |

---

## ~~Green status dot (#4)~~ — dropped
Superseded by item 3: the new device panel replaces the device list in the tray menu, and that panel carries
the status colours (green *Connected* section, each row coloured by its state).

## 1. Simplify sources (#3)
- *Sources* keeps only two toggles: **2.4 GHz wireless** and **Bluetooth**, both **on by default**.
- The "Experimental readers" category is removed: every reader joins the group it belongs to
  (USB dongles → 2.4 GHz; Bluetooth-only readers such as DualSense over BT → Bluetooth).
- The "DualSense over Bluetooth (extended mode)" toggle is folded into *Bluetooth*.
- **All previously experimental readers are on by default, except a small "beta" set** whose protocol notes
  were the least certain in the reviews (risk of misreading a value, or of picking the wrong collection):
  - Logitech PRO X 2 / PRO X 3 / G522 LIGHTSPEED (Centurion bridge, 3 round trips)
  - Corsair Void Wireless V2 / Virtuoso Max / HS80 Max (collection chosen by report length only)
  - HyperX Cloud Alpha Wireless and Cloud II Wireless (Kingston 0951:1718) (usage page not documented)
  - SteelSeries legacy protocol: Arctis 1 / 7 / 9, Arctis Pro Wireless, Nova Pro Wireless (report-id handling
    unverified on Windows, no reply echo check)
  - DualShock 4 over Bluetooth (calibration feature id unverified)
  Beta readers stay available behind an advanced config key `beta_readers` (default **off**), documented in
  the README, so testers can enable them; they move to the default set once someone confirms them.
- README: a clear note that most readers are implemented from public protocol notes and **untested on real
  hardware**, which ones are beta, and an explicit request for feedback: *"Tell us which devices work and
  which don't"* with a link to the *Device report* issue template (works / wrong value / not shown).
  Keep ✅ / 🟢 / 🧪 (beta) marks in the compatibility table.
- Migration: existing configs map `wireless_enabled`, `experimental_readers`, `dualsense_bt_extended` to the
  two new keys (on unless the user had turned the whole source off).
- Acceptance: tray shows exactly two source toggles; scan honours them and the beta list; migration tests;
  README note + feedback request; compatibility table updated.

## 2. Settings menu (#2)
Group the configuration entries under **Settings**, leaving the top level short.
- Top level: device list, *Refresh*, *Settings ▸*, *About…*, *Quit*.
- *Settings ▸*: *Sources ▸* (2.4 GHz, Bluetooth), *Alerts ▸* (low-battery notification, threshold),
  *Logs ▸* (open device log, open logs folder; later the detailed-log toggle from item 4),
  *Start with Windows*, *Personalization ▸*.
- *Personalization ▸*: colour by battery level (and future: theme, pill size, placement shortcut).
- Acceptance: menu structure test updated; every existing action reachable; ES/EN strings.

## 3. Scrollable device panel (#6, includes #4)
Replace the device list inside the tray menu and the *Show ▸ Disconnected / Visible devices* submenus with
a scrollable panel opened from the menu.
- Two sections: **Connected** and **Known** (disconnected / last known value).
- **Colours:** the *Connected* section is highlighted in **green** (header and accent); every row is coloured by
  its own state — battery level colour (green / yellow / orange / red), charging, idle (dimmed), unknown
  (grey), disconnected/last known (dimmed grey with the last value). Readable in light and dark themes and
  with the non-colour cues already used in the pills ("!", outline, moon).
- Each row: kind icon, name, level/status, and an **eye** button to show/hide that device on the taskbar.
- The **Known** section header has its own eye button = show/hide disconnected devices on the taskbar
  (replaces the *Show disconnected* toggle).
- Keyboard and screen-reader accessible; scrolls when there are many devices; ES/EN.
- Acceptance: hidden state persists (`hidden_devices`), header eye persists (`show_disconnected`);
  the old *Show* submenu is removed; tests for grouping, toggles and persistence.

## 4. Per-device detailed log (#5)
Optional per-device history sampled **once per minute** (date/time, battery, charging, connected, link).
- Enabled per device from an eye/record toggle in the device panel row, and globally from
  *Settings ▸ Logs* next to *Open device log* (button to open that device's log).
- Storage: `%LOCALAPPDATA%\TaskbarBatteryWidget\logs\devices\<hash>.csv`, rotating (e.g. 1 MB × 3 per
  device), no MAC/serial (hash key + sanitized name), written off the UI thread.
- Size estimate: ~60 B/line × 1 440 lines/day ≈ 85 KB/day/device → 1 MB lasts ~12 days; capped at 4 MB.
- Acceptance: toggle per device persists; 1-minute cadence verified with a fake clock; rotation and
  redaction tests; "open log" opens the device file.

## 5. Widget placement (#1)
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

## 6. Razer Firefly shown at 0 % (issue #2 log)
The reporter's `devices.log` lists "Razer Firefly | mouse | usb | 0%": a mouse pad without a battery that
answers the 90-byte battery query. Do not list Razer devices without a battery (mouse pads, docks without a
mouse, keyboards on cable…): e.g. a name/PID deny-list and/or ignoring a constant 0 % with no charging state.
Acceptance: Firefly never appears; real Razer mice at a genuine low level still do.

## 7. Razer BlackShark V2 Pro 2.4 GHz (issue #2)
1532:0555 is in `razer.KNOWN_DEVICES` but does not appear over the dongle (Bluetooth works). Asked the reporter
for a `TBW_DEBUG=1` log (`razer` lines). Possible causes: collection skipped (feature length < 91), no answer
to the 90-byte protocol (like the HyperSpeed 0565, which needs the ODESSA reader), or the scan budget used up
by other Razer devices first.

## 8. User-defined device order (#7)
- Today pills and panel follow the order in which the monitor delivers devices (`panel_model.py`), so they
  move when a device connects/disconnects or is hidden and shown again.
- Drag a device row in the panel (inside *Connected* and inside *Known*) to reorder it, with a visual drop
  indicator.
- Mandatory keyboard alternative: Alt+Up/Down on the focused row and/or *Move up* / *Move down* in a context
  menu.
- Stored in config, e.g. `"device_order"`: list of device keys, validated (max length, bounded strings).
  Default empty = current order. Unknown keys are ignored; new devices go to the end.
- Taskbar pills, the tray menu and the panel follow the same order. Show/hide (eye) and
  connect/disconnect never reorder. A reorder redraws the taskbar immediately.
- *Forget device* also removes it from `device_order`. No config version bump unless needed: old files take
  the default.

## 9. Pill styles (#8)
- New option *Settings ▸ Personalization ▸ Pill style*:
  - **A · Classic**: today's look (background, rounded corners).
  - **B · Soft**: fainter background, small corners (radius ~4 px).
  - **C · Transparent (new default)**: no background; pills separated by a thin, discreet vertical line in
    the taskbar's secondary text colour.
  - **D · Transparent compact**: no background, no separators, less padding.
- In C and D there is no background or border: low/critical battery is shown by colouring the value red and
  prefixing "!", and the critical pulse animates the text/icon instead of the background.
- Must respect the light/dark taskbar theme, interface scale, palettes and custom colours, icons and existing
  animations, and minimum contrast (text must stay readable without a background on light and dark
  taskbars).
- Changing the style applies live.

## 10. Stacked layout (#9)
- Toggle *Stack devices* in *Settings ▸ Personalization ▸ Position*. Applies in any floating position
  (corners and edges, user's choice after testing); on a horizontal taskbar the pills stay in a row.
- Stacked = one device per row (icon + level + charging bolt), aligned in a column, every row as wide as the
  widest pill so the values line up.
- Works with the four pill styles, with dragging in floating mode and its snapping to corners/edges, and with
  the fullscreen position. If the column does not fit on the screen, it is cut with "+N" as today.
