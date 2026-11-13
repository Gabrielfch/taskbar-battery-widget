# Implementation plan — backlog 1.5.0 / 1.6.0

Companion to [BACKLOG.md](BACKLOG.md). Sizes: S = ≤2 production files and ~150 lines; M = 1–3 days;
L = > 3 days. `∥` = can run in parallel with other tasks of the same wave (disjoint files).

## 1. Design per item

### Item 1 — Simplify sources, beta readers
- **Beta is a per-model attribute**, not a vendor group: each reader's model table declares `beta=True`
  and `read(..., beta=False)` skips those models. No parallel list to keep in sync.
- `EXPERIMENTAL_VENDOR_IDS` → `EXTRA_VENDOR_IDS`, always enumerated, still in its own `GuardedCall` so a
  slow enumeration never delays the core readers.

| Reader | Beta models | Stable (now on by default) |
| --- | --- | --- |
| `logitech_headsets` (Centurion) | 0x0AF7 PRO X 2, 0x0B45 PRO X 3, 0x0B18 G522 | legacy, Astro |
| `corsair` | family `v2`: 0x2A08 Void V2, 0x2A02 Virtuoso Max, 0x0A97 HS80 Max | Void legacy, Virtuoso XT/SE |
| `hyperx` | `alpha` 03F0:098D, `cloud2k` 0951:1718 | Cloud Flight, Cloud II (HP 0696/018B), Alpha 2 (08BE) |
| `steelseries` | families `arctis1`, `arctis7`, `arctis9`, `nova_pro`, `pro_wireless` | `arctis7plus`, `gamebuds`, `nova3/5/7` |
| `sony` | DualShock 4 over Bluetooth only | DS4 USB, PULSE, INZONE, Buds |

- `wireless.is_beta_device(dev)` resolves `dongle_key(id)` → (vid, pid) against the model tables (DS4 also
  checks `dev.link == "bluetooth"`); replaces `is_experimental_id` and purges the cache when beta turns off.
- Behaviour change to call out in release notes: XInput, Logitech legacy/Astro headsets, Jabra, Audeze,
  Lenovo and MCHOSE become active by default.
- **Config:** new `beta_readers` (False) and `bluetooth_enabled` (True); `wireless_enabled` stays and becomes
  the "2.4 GHz" toggle; `experimental_readers` is removed. Migration in `Config._load`, by presence: if
  `beta_readers` is absent and `experimental_readers` was True → `beta_readers = True`; save; idempotent.
- **Source gating:** `wireless.set_sources(dongles, bluetooth)` — dongle readers run only with `dongles`;
  readers with a BT transport (DualSense, DS4) run with `dongles or bluetooth` and their output is filtered
  by `d.link`. `monitor._scan_bluetooth` skips `scanner.scan()` when `bluetooth_enabled` is False (no
  "Bluetooth off" pill in that case).

### Item 2 — Settings menu
- New `settings_menu.py` builds *Settings ▸* from `cfg` + callbacks (keeps `tray.py` small; items 3–5 add
  entries there). `_sync_menu` delegates to the syncs the builder registers.
- Top level: device list, Refresh, Settings ▸, About…, Quit. Settings ▸: Sources ▸, Alerts ▸, Logs ▸, Start
  with Windows, Personalization ▸ (colour by level).
- No temporary *Devices ▸* submenu: items 1–4 ship together in 1.5.0, so the *Show ▸* submenu is replaced
  directly by the device panel (item 3).

### Item 3 — Device panel
- **Own frameless popup** (`DevicePanel`, `QFrame` with `Qt.Popup`), not `QMenu` + `QWidgetAction`: real
  `QScrollArea` (max 60 % of `availableGeometry`), full keyboard control (Tab, arrows, Space on the eye, Esc),
  per-row `accessibleName`. Reuse the `_foreground`/`_post_null` trick from `widget.py` for click-outside.
- Anchor: `tray.geometry()` when valid, else `QCursor.pos()`; from a pill reuse `popup.place_popup`. Opened by
  left click on the tray icon, a *Devices…* menu entry, and the `+N` pill.
- Pure model `panel_model.py`: `PanelRow(...)`, `PanelModel(connected, known, radio_off, show_disconnected)`,
  `build_panel_model(devices, cfg, threshold, dark)`.
- `app.on_devices` must always call `history.update(show_disconnected=True, hidden=())` so hidden/disconnected
  devices stay in the model; filtering moves to consumers (widget and tray already filter; alerts ignore
  stale/disconnected).
- Colours: pure `colors.row_style(device, threshold, dark)` shared with `widget._rebuild`; Connected header
  green, Known grey; non-colour cues ("!", outline, moon, bolt); contrast ≥ 4.5 tested with `contrast_ratio`.
- Signals: `visibility_toggled(device_id, bool)`, `show_disconnected_toggled(bool)`, later
  `logging_toggled(device_id, bool)`, `open_log(device_id)`. Hide/show logic moves to
  `visibility.set_hidden(cfg, key, hidden)`. Rebuild only when the signature changes; keep scroll position.
- Removed: `_rebuild_visible_menu`, the *Visible devices* submenu, disabled device lines in the menu.

### Item 4 — Per-device detailed log
- `detail_log.py`: `DetailLogger(base, clock).tick(devices, now)`; daemon writer thread fed by
  `queue.Queue(maxsize=1000)`; `close()` flushes. A 60 s `QTimer` aligned to the minute in `app.py` copies the
  post-history device list and enqueues (scan stays at 30 s).
- File `logs/devices/<slug>_<key>.csv`, columns `time_iso,battery,charging,connected,link,idle`; own rotation
  1 MB × 3 (4 MB per device); at most 20 active devices.
- **Privacy:** `visibility_key` is an unsalted truncated sha256 of an id that can contain a MAC (brute-forceable
  within an OUI). Log file names use `sha256(salt + id)` with a random per-install salt in `%LOCALAPPDATA%`;
  names go through `_clean_name` + `redact_text`.
- Config: `detail_log_enabled` (False, global) + `detail_log_devices` (list of keys). Enabling a device turns
  the global switch on. Disk errors are logged and never stop the app.

### Item 5 — Widget placement
- Keys (defaults = current behaviour): `placement_mode` (`taskbar`/`floating`), `taskbar_anchor`
  (`right`/`left`), `placement_monitor` (`primary` or a monitor key), `floating_positions` (monitor →
  `{anchor, dx, dy}`), `show_in_fullscreen` (False). Monitor key = hash of manufacturer/model/serial (+ `#n`
  for duplicates); a missing monitor falls back to primary without overwriting the choice.
- **Refactor first:** pure `placement.py` (`compute_placement(env, placement) -> PlacementResult`);
  `taskbar.get_taskbar_infos()` with secondaries (`Shell_SecondaryTrayWnd`), `has_tray`, `device_name`;
  match screens by `szDevice` ↔ `QScreen.name()` (not by size); per-monitor fullscreen; extract
  `win_window.py` (`_ZGuard`, `_apply_ex_styles`, `popup_menu_foreground`) with re-exports.
- Left anchor only when icons are centred (`TaskbarAl == 1`), else warn and fall back to right. Secondary
  taskbars have no `TrayNotifyWnd` → own fallback rect.
- Floating: `Qt.Tool | WindowStaysOnTopHint | WindowDoesNotAcceptFocus`, inside `availableGeometry`; drag with
  ~4 px threshold, snap to nearest of 8 anchors, store `dx, dy`; context menu *Reset position*.
- Per-monitor DPI (`setScreen` before `move`, recompute pill height/limits, cache key includes screen + size);
  hot-plug via `screenAdded/Removed`, `primaryScreenChanged`, `geometryChanged`.
- `show_in_fullscreen` works over borderless windows only (not exclusive DirectX); the Win11 flyout layer
  still hides it.

## 2. Tasks

### 1.5.0 — sources, settings menu, device panel, detailed log (items 1–4)

Wave A (sources + menu):
| Id | Goal | Files | Size | Depends |
| --- | --- | --- | --- | --- |
| T01 | `beta_readers`, `bluetooth_enabled`, migration, drop `experimental_readers` | `config.py` + tests | S | — |
| T02a ∥ | Centurion models beta | `logitech_headsets.py` + tests | S | — |
| T02b ∥ | Corsair v2 beta | `corsair.py` + tests | S | — |
| T02c ∥ | HyperX alpha / cloud2k beta | `hyperx.py` + tests | S | — |
| T02d ∥ | SteelSeries 5 legacy families beta | `steelseries.py` + tests | S | — |
| T02e ∥ | DS4 over BT beta | `sony.py` + tests | S | — |
| T03 | `set_beta_readers`, `set_sources`, `is_beta_device`, always enumerate extra vendors, beta purge | `wireless.py` + tests | M | T01, T02* |
| T04 | Wire flags in monitor, skip BT scan when off | `monitor.py` + tests | S | T03 |
| T05 | Sources = 2 toggles | `tray.py`, `i18n.py` + tests | S | T01 |
| T06 ∥ | README: untested note, beta list, `beta_readers`, feedback request, ✅/🟢/🧪 | `README.md` | S | T02* |
| T07 | `settings_menu.py` + menu restructure (no temporary *Devices ▸*) | `settings_menu.py`, `tray.py`, `i18n.py` + tests | M | T05 |

Wave B (device panel + detailed log; T11 and T20 can start during wave A):

| Id | Goal | Files | Size | Depends |
| --- | --- | --- | --- | --- |
| T10 ∥ | `panel_model.py`, `row_style` | `panel_model.py`, `colors.py` | S | T07 |
| T11 ∥ | Eye and record icons | `icons.py` | S | — |
| T12 | `visibility.set_hidden`, history always complete | `visibility.py`, `app.py` | S | T10 |
| T13 | `DevicePanel` (rows, sections, scroll, keyboard, eyes) | `device_panel.py` | L | T10–T12 |
| T14 | Tray/widget integration, remove old submenus | `tray.py`, `widget.py`, `i18n.py` | M | T13 |
| T20 ∥ | Detailed-log keys + `detail_log.py` (writer, rotation, redaction, salt) | `detail_log.py`, `config.py` | M | T01 |
| T21 | Minute timer, panel toggle, *Settings ▸ Logs* entry | `app.py`, `device_panel.py`, `settings_menu.py` | M | T13, T14, T20 |

### 1.6.0 — widget placement (item 5)
| Id | Goal | Files | Size | Depends |
| --- | --- | --- | --- | --- |
| T30 | Extract `win_window.py`; `update_position` behind `placement.py`, no behaviour change | `win_window.py`, `widget.py`, `placement.py` | M | T14 |
| T31 ∥ | `get_taskbar_infos()`, per-monitor fullscreen, monitor key | `taskbar.py` + tests | M | — |
| T32 ∥ | Placement config keys, `floating_positions` validation | `config.py` + tests | S | — |
| T33 | Placement math per mode | `placement.py` + tests | M | T30–T32 |
| T34 | Apply placement, floating drag, hot-plug, geometry cache, `show_in_fullscreen` | `widget.py` | L | T33 |
| T35 | *Personalization ▸ Position* + *Reset position* | `settings_menu.py`, `tray.py`, `i18n.py` | M | T32, T34 |

Acceptance criteria and tests per task follow BACKLOG.md; every task keeps the full suite green.

## 3. Risks and manual tests
- More readers on by default → beta limited to the least certain protocols; release notes say so.
- Tests pinned to `_experimental` → concentrated in T03.
- `Qt.Popup` from the tray (focus, overflow icon) → reuse `_foreground`, handle empty `tray.geometry()`.
- Two identical monitors → match by `szDevice`.
- Win11 centred icons / secondary taskbar without tray → `TaskbarAl` check, own fallback, user warning.

Manual: each beta device with/without `beta_readers`; DualSense/DS4 over BT with the Bluetooth toggle on/off;
two monitors with different DPI (drag, secondary taskbar, hot-plug); borderless fullscreen on another monitor;
auto-hide taskbar; panel with Narrator and keyboard only; 1-hour detailed-log session.

## 4. Open decisions
1. Taskbar **centre** anchor: not offered in 1.6.0 (no free-gap info without UI Automation); covered by
   floating top/bottom-centre. Later spike if needed.
2. Wired controllers (`link == "usb"`) under the 2.4 GHz toggle, labelled "2.4 GHz and USB".
3. `dualsense_bt_extended` kept as an advanced key (default True), no menu entry, not migrated.
4. Detailed log: global switch + per-device list; enabling a device turns the global switch on.
5. Left click on a pill in floating mode keeps opening the device popup; the panel opens from the tray icon
   and the `+N` pill.
