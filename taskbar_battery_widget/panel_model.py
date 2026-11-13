"""Pure model of the device panel: rows grouped into Connected / Known (no widgets)."""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import i18n
from .colors import RowStyle, row_style
from .models import Device, visibility_key
from .naming import sanitize_name

def _lang(lang: str | None) -> str:
    return i18n.get_language() if lang is None else ("es" if lang.lower().startswith("es") else "en")


def _t(key: str, lang: str, **kw) -> str:
    return i18n.tr_in(lang, key, **kw)


def section_title(kind: str, lang: str | None = None) -> str:
    """Localised header text: kind is "connected" or "known"."""
    return _t("panel_connected" if kind == "connected" else "panel_known", _lang(lang))


@dataclass(frozen=True)
class PanelRow:
    key: str                # visibility_key(device_id)
    device_id: str
    name: str
    kind: str
    link: str
    battery: int | None
    charging: bool
    idle: bool
    stale: bool
    connected: bool
    style: RowStyle
    visible: bool           # False when the user hid it from the widget/tray
    status_text: str


@dataclass(frozen=True)
class PanelModel:
    connected: tuple[PanelRow, ...]
    known: tuple[PanelRow, ...]
    show_disconnected: bool
    signature: tuple


def _status(d: Device, threshold: int, lang: str) -> str:
    if getattr(d, "stale", False) and d.battery is not None:
        from .device_history import format_ago
        last = getattr(d, "last_seen", None)
        ago = format_ago(time.time() - last) if last is not None else ""
        key = "last_value_disc" if not d.connected else "last_value_unknown"
        return _t(key, lang, level=d.battery, ago=ago).rstrip()
    if not d.connected:
        return _t("disconnected", lang)
    if d.battery is None:
        return _t("battery_unknown", lang)
    if getattr(d, "idle", False):
        return _t("idle", lang)
    if d.charging:
        return _t("charging", lang)
    if d.battery >= 100:
        return _t("status_full", lang)
    if d.battery <= threshold:
        return _t("status_low", lang)
    return _t("status_ok", lang)


def _row(d: Device, hidden: set, threshold: int, dark: bool, by_level: bool, lang: str) -> PanelRow:
    key = visibility_key(d.id)
    return PanelRow(
        key=key, device_id=d.id, name=sanitize_name(d.name) or "?", kind=d.kind,
        link=getattr(d, "link", "bluetooth"), battery=d.battery, charging=bool(d.charging),
        idle=bool(getattr(d, "idle", False)), stale=bool(getattr(d, "stale", False)),
        connected=bool(d.connected), style=row_style(d, threshold, dark, by_level),
        visible=key not in hidden, status_text=_status(d, threshold, lang))


def _row_sig(r: PanelRow) -> tuple:
    s = r.style
    return (r.key, r.name, r.kind, r.link, r.battery, r.charging, r.idle, r.stale, r.connected,
            r.visible, r.status_text, s.fg.name(), s.bg.name(), s.band, s.cues)


def build_panel_model(devices, hidden_keys, show_disconnected: bool, threshold: int, dark: bool,
                      color_by_level: bool = True, lang: str | None = None) -> PanelModel:
    """Group devices into Connected / Known. Order inside each group is the input order (the
    widget and tray also show devices in the order the monitor delivers them)."""
    lg = _lang(lang)
    hidden = {x for x in (hidden_keys or ()) if isinstance(x, str)}
    rows = [_row(d, hidden, int(threshold), bool(dark), bool(color_by_level), lg) for d in devices]
    connected = tuple(r for r in rows if r.connected)
    known = tuple(r for r in rows if not r.connected)
    sig = (bool(show_disconnected), bool(dark), lg,
           tuple(_row_sig(r) for r in connected), tuple(_row_sig(r) for r in known))
    return PanelModel(connected, known, bool(show_disconnected), sig)
