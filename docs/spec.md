# Taskbar Battery Widget — v1 spec

App name: **Taskbar Battery Widget** (package `taskbar_battery_widget`).
Python 3.14 venv at `.venv`, PySide6 6.12, winrt 4.x.

## Acceptance criteria
1. Shows battery % of **connected** paired Bluetooth Classic and BLE devices, live (watchers + 30 s poll).
2. A frameless, always-on-top, non-activating widget sits on the Windows taskbar, just left of the
   notification area (tray/clock), one "pill" per device: kind icon + `NN%`. Text/icons follow the
   taskbar theme (light/dark). Pill turns red ≤ low threshold; tooltip shows name, %, kind.
3. Widget hides when a fullscreen app is in the foreground on that monitor and when no device is shown
   (unless `show_when_empty`). Re-asserts topmost so it stays above the taskbar.
4. Tray icon with menu: device list (read-only), Refresh, Show disconnected devices (toggle),
   Notifications (toggle), Low threshold (10/15/20/25/30 %), Start with Windows (toggle), Quit.
5. Low-battery toast (QSystemTrayIcon.showMessage) once per device when it crosses ≤ threshold,
   repeated at most every `notification_cooldown_min`; re-armed after rising above threshold + 5.
6. "Charging" estimated when battery rises ≥ 2 points within 30 min window; cleared after 30 min
   without rise or when it drops.
7. Settings persisted in `%APPDATA%\TaskbarBatteryWidget\config.json`; corrupt/missing file ⇒ defaults.
8. Bluetooth radio off ⇒ no devices + pill/tooltip "Bluetooth off" state; recovers when turned on.
9. UI language: Spanish if system locale starts with `es`, else English.
10. `python -m pytest` passes; `python -m taskbar_battery_widget` runs.

## Package layout and contracts (`taskbar_battery_widget/`)
```
models.py      @dataclass Device: id:str, name:str, kind:str, battery:int|None, connected:bool,
               address:str="", charging:bool=False      (kind ∈ KINDS)
               KINDS = ("keyboard","mouse","headset","gamepad","speaker","phone","tablet","watch",
                        "computer","pen","remote","microphone","camera","tv","car","printer","generic")
naming.py      clean_name(str)->str  (strip " Hands-Free AG", " Hands-Free", " Stereo", " Audio",
               " AVRCP Transport", leading "AVRCP Transport for ", Spanish variants, trim)
               kind_from_cod(major:int|None, minor:int|None)->str|None   (Bluetooth CoD)
               kind_from_appearance(app:int|None)->str|None              (BLE appearance; category=app>>6)
               kind_from_name(name)->str  (keyword fallback, default "generic")
winrt_props.py unbox(obj) (IPropertyValue._from(obj).type int code: 1 u8,2 i16,3 u16,4 i32,5 u32,
               6 i64,7 u64,11 bool,12 str,16 guid → get_*), prop(props, key, default=None)
scanner.py     BATTERY_KEY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"
               merge(paired:list[dict], battery_nodes:list[dict]) -> list[Device]   (PURE, tested)
                 paired dict: {id,name,container_id,address,connected,major,minor,appearance,is_le}
                 node dict:   {name,battery,container_id,present,instance_id}
                 join by container_id (fallback: 12-hex MAC in instance_id == address w/o ':');
                 battery = value of present nodes, else max of all; dedupe same container_id.
               async scan() -> list[Device]; async is_radio_on() -> bool
monitor.py     class DeviceMonitor(QObject): signals devices_changed(list) [list[Device]],
               radio_changed(bool); start(), stop(), refresh(). Background thread with its own
               asyncio loop; DeviceWatchers (paired BT + BLE AEP selectors, and DEVICE nodes with
               battery key) trigger a debounced (~1 s) rescan; poll every 30 s; applies ChargeEstimator.
charge.py      class ChargeEstimator: update(dev_id:str, level:int|None, now:float)->bool
alerts.py      class AlertManager(config): check(devices, now:float)->list[tuple[str,str]]  (title, body)
config.py      class Config: get(key), set(key, value) (saves), path; DEFAULTS =
               {low_threshold:20, notifications_enabled:True, notification_cooldown_min:60,
                show_disconnected:False, show_when_empty:False, start_with_windows:False}
               Config(path=None) — path override for tests.
startup.py     is_enabled()->bool, set_enabled(bool)  (HKCU\...\CurrentVersion\Run, value "TaskbarBatteryWidget";
               command = pythonw -m taskbar_battery_widget or sys.executable when frozen)
i18n.py        tr(key, **kw)->str, es/en dicts
taskbar.py     ctypes Win32: TaskbarInfo(rect, tray_rect, edge, autohide, monitor_rect) | None
               get_taskbar_info(); is_fullscreen_foreground(monitor_rect)->bool;
               taskbar_uses_light_theme()->bool (HKCU ...Themes\Personalize SystemUsesLightTheme)
icons.py       draw_kind_icon(painter:QPainter, kind:str, rect:QRectF, color:QColor)  (original vector art)
               make_tray_icon(level:int|None, dark:bool)->QIcon
widget.py      class TaskbarWidget(QWidget): set_devices(list[Device]), set_radio(bool), set_config(cfg)
tray.py        class TrayController(QObject) building QSystemTrayIcon + menu; emits refresh/quit
app.py         main(): QApplication, single-instance guard (QLockFile in temp), wires everything
__main__.py    from .app import main; main()
```
