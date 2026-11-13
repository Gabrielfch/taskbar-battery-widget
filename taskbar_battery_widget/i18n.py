"""Minimal i18n: Spanish if the system locale starts with "es", else English.

Stable keys (use `tr(key, **kw)`; unknown keys return the key itself):
    app_name, bluetooth_off, no_devices, refresh, notifications,
    low_threshold, start_with_windows, quit, devices, low_title,
    low_body ({name}, {level}), charging, disconnected, battery_unknown,
    pill_off, pill_none (short pill labels), wireless_devices, color_levels,
    show_all, status_full/low/ok, updated_at ({time}), link_bluetooth, link_24ghz, link_usb,
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
    "menu_devices": "Devices",
    "panel_connected": "Connected", "panel_known": "Known devices",
    "panel_show_in_bar": "Show {name} in the taskbar",
    "panel_show_known": "Show known devices in the taskbar",
    "panel_known_eye_needed": "Turn on 'Show known devices in the taskbar' first",
    "panel_radio_off": "Bluetooth is off",
    "panel_more_one": "{n} more device. Open the list",
    "panel_more_other": "{n} more devices. Open the list",
    "panel_record": "Record {name} to the detailed log",
    "panel_open_log": "Open the log of {name}",
    "panel_empty": "No devices",
    "panel_hide_in_bar": "Hide {name} from the taskbar",
    "panel_hide_known": "Hide known devices from the taskbar",
    "panel_record_start": "Start per-minute recording of {name}",
    "panel_record_stop": "Stop per-minute recording of {name}",
    "panel_about": "About",
    "detail_log": "Detailed log (every minute)",
    "open_detail_folder": "Open detailed logs folder",
    "bluetooth_off": "Bluetooth off",
    "pill_off": "OFF",
    "pill_none": "--",
    "no_devices": "No devices",
    "refresh": "Refresh",
    "menu_sources": "Sources", "menu_alerts": "Alerts",
    "more_devices": "More devices", "low_warning": "low battery",
    "notifications": "Notifications",
    "low_threshold": "Low battery threshold",
    "start_with_windows": "Start with Windows",
    "wireless_devices": "2.4 GHz and USB",
    "bluetooth_source": "Bluetooth",
    "menu_settings": "Settings", "menu_logs": "Logs", "menu_personalization": "Personalization",
    "link_bluetooth": "Bluetooth", "link_24ghz": "2.4 GHz", "link_usb": "USB",
    "quit": "Quit",
    "color_levels": "Color by battery level",
    "show_all": "Show all in the taskbar",
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
    "last_value_disc": "Disconnected · last value {level}% {ago}",
    "last_value_unknown": "Battery unknown · last value {level}% {ago}",
    "ago_min": "{n} min ago", "ago_h": "{n} h ago",
    "ago_d": "{n} day ago", "ago_ds": "{n} days ago",
    "open_device_log": "Open device log",
    "open_logs_folder": "Open logs folder",
    "battery_unknown": "Battery unknown",
    "menu_about": "About Taskbar Battery Widget…",
    "about_title": "About Taskbar Battery Widget",
    "about_version": "Version {version}",
    "about_description": "Shows the battery level of your Bluetooth and wireless devices on the Windows taskbar.",
    "about_license": "Released under the {link}.",
    "about_license_link": "MIT License",
    "about_links": "Links",
    "about_notices": "Third-party notices",
    "about_notices_local": "Installed copy of the third-party notices",
    "about_repo": "Project on GitHub",
    "about_releases": "Releases",
    "about_report_device": "Report a device",
    "about_logs": "Logs folder",
    "about_open_folder": "Open folder",
    "about_tech": "Technical information",
    "about_copy": "Copy",
    "about_copied": "Copied to the clipboard.",
    "tech_app": "App version", "tech_python": "Python", "tech_qt": "Qt / PySide6",
    "tech_windows": "Windows build", "tech_install": "Installation",
    "mode_installed": "installed", "mode_portable": "portable", "mode_source": "from source",
    "about_check": "Check for updates",
    "about_auto": "Check for updates automatically (once a day)",
    "upd_checking": "Checking for updates…",
    "upd_uptodate": "You have the latest version.",
    "upd_new": "A new version {version} is available.",
    "upd_notes": "View release notes",
    "upd_install": "Download and install",
    "upd_portable_hint": "This copy is not the installed version, so it cannot update itself. "
                         "Download the new version from the release page.",
    "upd_confirm_title": "Install update",
    "upd_confirm": "The app will close and version {version} will be installed. Continue?",
    "upd_downloading": "Downloading and verifying version {version}…",
    "upd_launching": "Starting the installer…",
    "upd_cancel": "Cancel",
    "upd_notify_title": "Update available",
    "upd_notify_body": "Version {version} is available. Open About to install it.",
    "upd_err_network": "Could not reach GitHub. Check your connection and try again.",
    "upd_err_url": "The download address is not valid. The update was not installed.",
    "upd_err_size": "The download is larger than expected. The update was not installed.",
    "upd_err_nosum": "The release has no checksum for the installer. The update was not installed.",
    "upd_err_hash": "The downloaded file failed the integrity check and was deleted. "
                    "The update was not installed.",
    "upd_err_cancelled": "Download cancelled.",
    "upd_err_launch": "Could not start the installer.",
    "upd_err_unverified": "The authenticity of this update cannot be verified, so it was not installed. "
                          "Download it manually from the release page.",
    "upd_err_mode": "Only the installed copy can update itself. Download the new version from the release page.",
    "upd_err_timeout": "The download took too long and was stopped. Try again later.",
    "upd_confirm2_title": "Update verified",
    "upd_confirm2": "Update verified. Install now? The app will close.",
    "upd_close": "Close",
    "kind_keyboard": "Keyboard", "kind_mouse": "Mouse", "kind_headset": "Headset",
    "kind_gamepad": "Gamepad", "kind_speaker": "Speaker", "kind_phone": "Phone",
    "kind_tablet": "Tablet", "kind_watch": "Watch", "kind_computer": "Computer",
    "kind_pen": "Pen", "kind_remote": "Remote", "kind_microphone": "Microphone",
    "kind_camera": "Camera", "kind_tv": "TV", "kind_car": "Car",
    "kind_printer": "Printer", "kind_generic": "Device",
}

ES = {
    "menu_devices": "Dispositivos",
    "panel_connected": "Conectados", "panel_known": "Dispositivos conocidos",
    "panel_show_in_bar": "Mostrar {name} en la barra",
    "panel_show_known": "Mostrar conocidos en la barra",
    "panel_known_eye_needed": "Activa «Mostrar conocidos en la barra» primero",
    "panel_radio_off": "Bluetooth desactivado",
    "panel_more_one": "{n} dispositivo más. Abrir la lista",
    "panel_more_other": "{n} dispositivos más. Abrir la lista",
    "panel_record": "Registrar {name} en el registro detallado",
    "panel_open_log": "Abrir el registro de {name}",
    "panel_empty": "Sin dispositivos",
    "panel_hide_in_bar": "Ocultar {name} de la barra",
    "panel_hide_known": "Ocultar conocidos de la barra",
    "panel_record_start": "Iniciar el registro por minuto de {name}",
    "panel_record_stop": "Detener el registro por minuto de {name}",
    "panel_about": "Acerca de",
    "detail_log": "Registro detallado (cada minuto)",
    "open_detail_folder": "Abrir carpeta de registros detallados",
    "app_name": "Taskbar Battery Widget",
    "bluetooth_off": "Bluetooth apagado",
    "pill_off": "OFF",
    "pill_none": "--",
    "no_devices": "Sin dispositivos",
    "refresh": "Actualizar",
    "menu_sources": "Fuentes", "menu_alerts": "Alertas",
    "more_devices": "Más dispositivos", "low_warning": "batería baja",
    "notifications": "Avisar cuando la batería esté baja",
    "low_threshold": "Umbral de batería baja",
    "start_with_windows": "Iniciar con Windows",
    "wireless_devices": "2.4 GHz y USB",
    "bluetooth_source": "Bluetooth",
    "menu_settings": "Configuración", "menu_logs": "Registros", "menu_personalization": "Personalización",
    "link_bluetooth": "Bluetooth", "link_24ghz": "2.4 GHz", "link_usb": "USB",
    "quit": "Salir",
    "color_levels": "Color según el nivel",
    "show_all": "Mostrar todos en la barra",
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
    "last_value_disc": "Desconectado · último valor {level} % {ago}",
    "last_value_unknown": "Batería desconocida · último valor {level} % {ago}",
    "ago_min": "hace {n} min", "ago_h": "hace {n} h",
    "ago_d": "hace {n} día", "ago_ds": "hace {n} días",
    "open_device_log": "Abrir registro de dispositivos",
    "open_logs_folder": "Abrir carpeta de registros",
    "battery_unknown": "Batería desconocida",
    "menu_about": "Acerca de Taskbar Battery Widget…",
    "about_title": "Acerca de Taskbar Battery Widget",
    "about_version": "Versión {version}",
    "about_description": "Muestra el nivel de batería de tus dispositivos Bluetooth e inalámbricos en la barra de tareas de Windows.",
    "about_license": "Distribuido bajo la {link}.",
    "about_license_link": "licencia MIT",
    "about_links": "Enlaces",
    "about_notices": "Avisos de terceros",
    "about_notices_local": "Copia instalada de los avisos de terceros",
    "about_repo": "Proyecto en GitHub",
    "about_releases": "Versiones (Releases)",
    "about_report_device": "Informar de un dispositivo",
    "about_logs": "Carpeta de registros",
    "about_open_folder": "Abrir carpeta",
    "about_tech": "Información técnica",
    "about_copy": "Copiar",
    "about_copied": "Copiado al portapapeles.",
    "tech_app": "Versión de la app", "tech_python": "Python", "tech_qt": "Qt / PySide6",
    "tech_windows": "Compilación de Windows", "tech_install": "Instalación",
    "mode_installed": "instalada", "mode_portable": "portable", "mode_source": "desde el código fuente",
    "about_check": "Buscar actualizaciones",
    "about_auto": "Buscar actualizaciones automáticamente (una vez al día)",
    "upd_checking": "Buscando actualizaciones…",
    "upd_uptodate": "Tienes la última versión.",
    "upd_new": "Hay una versión nueva {version}.",
    "upd_notes": "Ver novedades",
    "upd_install": "Descargar e instalar",
    "upd_portable_hint": "Esta copia no es la versión instalada, así que no puede actualizarse sola. "
                         "Descarga la versión nueva desde la página de la release.",
    "upd_confirm_title": "Instalar actualización",
    "upd_confirm": "Se cerrará la app y se instalará la versión {version}. ¿Continuar?",
    "upd_downloading": "Descargando y verificando la versión {version}…",
    "upd_launching": "Iniciando el instalador…",
    "upd_cancel": "Cancelar",
    "upd_notify_title": "Actualización disponible",
    "upd_notify_body": "Hay una versión nueva {version}. Abre «Acerca de» para instalarla.",
    "upd_err_network": "No se pudo conectar con GitHub. Revisa tu conexión e inténtalo de nuevo.",
    "upd_err_url": "La dirección de descarga no es válida. No se instaló la actualización.",
    "upd_err_size": "La descarga es mayor de lo esperado. No se instaló la actualización.",
    "upd_err_nosum": "La release no incluye la suma de verificación del instalador. No se instaló la actualización.",
    "upd_err_hash": "El archivo descargado no pasó la comprobación de integridad y se ha borrado. "
                    "No se instaló la actualización.",
    "upd_err_cancelled": "Descarga cancelada.",
    "upd_err_launch": "No se pudo iniciar el instalador.",
    "upd_err_unverified": "No se puede verificar la autenticidad de esta actualización, así que no se instaló. "
                          "Descárgala manualmente desde la página de la release.",
    "upd_err_mode": "Solo la copia instalada puede actualizarse sola. Descarga la versión nueva desde la página de la release.",
    "upd_err_timeout": "La descarga tardó demasiado y se detuvo. Inténtalo más tarde.",
    "upd_confirm2_title": "Actualización verificada",
    "upd_confirm2": "Actualización verificada. ¿Instalar ahora? La app se cerrará.",
    "upd_close": "Cerrar",
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


def tr_in(lang: str, key: str, **kw) -> str:
    """Like tr() but for an explicit language ("es" or anything else = English)."""
    text = (ES if lang == "es" else EN).get(key) or EN.get(key) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def kind_name(kind: str) -> str:
    return tr(f"kind_{kind}")
