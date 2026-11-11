"""Minimal i18n: Spanish if the system locale starts with "es", else English.

Stable keys (use `tr(key, **kw)`; unknown keys return the key itself):
    app_name, bluetooth_off, no_devices, refresh, show_disconnected, notifications,
    low_threshold, start_with_windows, quit, devices, low_title,
    low_body ({name}, {level}), charging, disconnected, battery_unknown,
    pill_off, pill_none (short pill labels), wireless_devices, color_levels,
    visible_devices, show_all, status_full/low/ok, updated_at ({time}), link_bluetooth, link_24ghz, link_usb,
    kind_<kind> for every kind in models.KINDS
    (kind_keyboard, kind_mouse, kind_headset, kind_gamepad, kind_speaker, kind_phone,
     kind_tablet, kind_watch, kind_computer, kind_pen, kind_remote, kind_microphone,
     kind_camera, kind_tv, kind_car, kind_printer, kind_generic).
Helper: kind_name(kind) -> localized kind label. set_language("es"|"en") overrides detection.
"""
from __future__ import annotations

import sys

EN = {
    "app_name": "Taskbar Battery Widget",
    "bluetooth_off": "Bluetooth off",
    "pill_off": "OFF",
    "pill_none": "--",
    "no_devices": "No devices",
    "refresh": "Refresh",
    "show_disconnected": "Disconnected devices",
    "menu_show": "Show", "menu_sources": "Sources", "menu_alerts": "Alerts",
    "more_devices": "More devices", "low_warning": "low battery",
    "notifications": "Notifications",
    "low_threshold": "Low battery threshold",
    "start_with_windows": "Start with Windows",
    "wireless_devices": "2.4 GHz wireless receivers",
    "dualsense_bt_extended": "DualSense over Bluetooth (extended mode)",
    "link_bluetooth": "Bluetooth", "link_24ghz": "2.4 GHz", "link_usb": "USB",
    "quit": "Quit",
    "color_levels": "Color by battery level",
    "visible_devices": "Visible devices",
    "show_all": "Show all",
    "status_full": "Full", "status_low": "Low", "status_ok": "OK",
    "updated_at": "Updated {time}",
    "idle": "Asleep",
    "idle_since": "Asleep · last value {min} min ago",
    "idle_since_h": "Asleep · last value {h} h ago",
    "devices": "Devices",
    "low_title": "Low battery",
    "low_body": "{name} is at {level}%",
    "charging": "Charging",
    "disconnected": "Disconnected",
    "battery_unknown": "Battery unknown",
    "kind_keyboard": "Keyboard", "kind_mouse": "Mouse", "kind_headset": "Headset",
    "kind_gamepad": "Gamepad", "kind_speaker": "Speaker", "kind_phone": "Phone",
    "kind_tablet": "Tablet", "kind_watch": "Watch", "kind_computer": "Computer",
    "kind_pen": "Pen", "kind_remote": "Remote", "kind_microphone": "Microphone",
    "kind_camera": "Camera", "kind_tv": "TV", "kind_car": "Car",
    "kind_printer": "Printer", "kind_generic": "Device",
}

ES = {
    "app_name": "Taskbar Battery Widget",
    "bluetooth_off": "Bluetooth apagado",
    "pill_off": "OFF",
    "pill_none": "--",
    "no_devices": "Sin dispositivos",
    "refresh": "Actualizar",
    "show_disconnected": "Desconectados",
    "menu_show": "Mostrar", "menu_sources": "Fuentes", "menu_alerts": "Alertas",
    "more_devices": "Más dispositivos", "low_warning": "batería baja",
    "notifications": "Avisar cuando la batería esté baja",
    "low_threshold": "Umbral de batería baja",
    "start_with_windows": "Iniciar con Windows",
    "wireless_devices": "Inalámbricos 2.4 GHz",
    "dualsense_bt_extended": "DualSense por Bluetooth (modo extendido)",
    "link_bluetooth": "Bluetooth", "link_24ghz": "2.4 GHz", "link_usb": "USB",
    "quit": "Salir",
    "color_levels": "Color según el nivel",
    "visible_devices": "Dispositivos visibles",
    "show_all": "Mostrar todos",
    "status_full": "Completa", "status_low": "Baja", "status_ok": "Normal",
    "updated_at": "Actualizado {time}",
    "idle": "En reposo",
    "idle_since": "En reposo · último valor hace {min} min",
    "idle_since_h": "En reposo · último valor hace {h} h",
    "devices": "Dispositivos",
    "low_title": "Batería baja",
    "low_body": "{name} está al {level}%",
    "charging": "Cargando",
    "disconnected": "Desconectado",
    "battery_unknown": "Batería desconocida",
    "kind_keyboard": "Teclado", "kind_mouse": "Ratón", "kind_headset": "Auriculares",
    "kind_gamepad": "Mando", "kind_speaker": "Altavoz", "kind_phone": "Teléfono",
    "kind_tablet": "Tableta", "kind_watch": "Reloj", "kind_computer": "Ordenador",
    "kind_pen": "Lápiz", "kind_remote": "Control remoto", "kind_microphone": "Micrófono",
    "kind_camera": "Cámara", "kind_tv": "TV", "kind_car": "Coche",
    "kind_printer": "Impresora", "kind_generic": "Dispositivo",
}

_TABLES = {"en": EN, "es": ES}


def detect_language() -> str:
    name = ""
    try:
        if sys.platform == "win32":
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                name = buf.value
        if not name:
            import locale

            name = locale.getlocale()[0] or ""
    except Exception:
        name = ""
    return "es" if name.lower().startswith("es") else "en"


_lang = detect_language()


def set_language(lang: str) -> None:
    global _lang
    _lang = "es" if lang.lower().startswith("es") else "en"


def get_language() -> str:
    return _lang


def tr(key: str, **kw) -> str:
    text = _TABLES[_lang].get(key) or EN.get(key) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def kind_name(kind: str) -> str:
    return tr(f"kind_{kind}")
