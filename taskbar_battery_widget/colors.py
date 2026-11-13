"""Battery-level colours (pure, no widgets): readable on dark and light taskbars."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

HIGH_MIN = 60
MEDIUM_MIN = 30

# (dark taskbar, light taskbar). Every colour keeps >= 4.5:1 text contrast against the pill
# backgrounds below (normal/hover/low tints composited over the taskbar base colour).
_PALETTE = {
    "green": ((110, 220, 120), (17, 97, 40)),
    "amber": ((245, 200, 66), (114, 74, 0)),
    "orange": ((255, 158, 72), (139, 58, 0)),
    "red": ((255, 153, 153), (165, 24, 24)),
    "grey": ((182, 182, 182), (82, 82, 82)),
}

# Pill background tints: (taskbar base, (rgb, alpha) normal, hover, low, low+hover) per theme.
TASKBAR_BASE = {True: (32, 32, 32), False: (243, 243, 243)}
_TINTS = {
    True: {"normal": ((255, 255, 255), 24), "hover": ((255, 255, 255), 44),
           "low": ((255, 105, 105), 38), "low_hover": ((255, 105, 105), 64)},
    False: {"normal": ((0, 0, 0), 22), "hover": ((0, 0, 0), 32),
            "low": ((176, 0, 0), 30), "low_hover": ((176, 0, 0), 52)},
}


def pill_tint(kind: str, dark: bool) -> QColor:
    """Translucent fill painted for a pill (kind: normal | hover | low | low_hover)."""
    (r, g, b), a = _TINTS[bool(dark)][kind]
    return QColor(r, g, b, a)


def composite(fg: QColor, bg: QColor) -> QColor:
    """`fg` (with its alpha) painted over the opaque colour `bg`."""
    a = fg.alpha() / 255
    return QColor(*(round(bg.red() + (fg.red() - bg.red()) * a),
                    round(bg.green() + (fg.green() - bg.green()) * a),
                    round(bg.blue() + (fg.blue() - bg.blue()) * a)))


def pill_backgrounds(dark: bool) -> dict[str, QColor]:
    """Opaque colour each pill background ends up as over the taskbar base colour."""
    base = QColor(*TASKBAR_BASE[bool(dark)])
    return {k: composite(pill_tint(k, dark), base) for k in _TINTS[bool(dark)]}


def band_color(band: str, dark: bool) -> QColor:
    return QColor(*_PALETTE[band][0 if dark else 1])


def level_band(level: int | None, charging: bool, low_threshold: int) -> str:
    """Name of the colour band: green | amber | orange | red | grey."""
    if level is None:
        return "grey"
    if charging:
        return "green"
    if level <= low_threshold:
        return "red"
    if level < MEDIUM_MIN:
        return "orange"
    if level < HIGH_MIN:
        return "amber"
    return "green"


def battery_color(level: int | None, charging: bool, low_threshold: int, dark: bool) -> QColor:
    """Colour for a battery level. `dark` = the taskbar is dark (so use the bright variants)."""
    rgb = _PALETTE[level_band(level, charging, low_threshold)][0 if dark else 1]
    return QColor(*rgb)


def luminance(c: QColor) -> float:
    def ch(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast_ratio(a: QColor, b: QColor) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------------------------------------------------------------------------- per-row style
IDLE_ALPHA = 153     # ~60 % opacity of the icon and bar of sleeping devices (never of the text)
DIM_ALPHA = 170      # icon of a disconnected / last-known device


@dataclass(frozen=True)
class RowStyle:
    """Pure description of how a device is drawn (shared by the pills and the device panel).

    `fg` is the opaque text colour, `accent` the level colour (None = no level colour: mono mode,
    unknown or disconnected), `bg` the opaque background the text sits on. `cues` are the
    non-colour hints: "warn" ("!" glyph, orange/red), "alert" (outline, red), "charging" (bolt),
    "idle" (moon), "stale" (last known value), "unknown" (no reading), "disconnected".
    """
    fg: QColor
    accent: QColor | None
    bg: QColor
    band: str
    dimmed: bool
    alert: bool
    cues: tuple[str, ...]


def _plain_fg(dark: bool) -> QColor:
    return QColor(255, 255, 255) if dark else QColor(20, 20, 20)


def row_style(device, threshold: int, dark: bool, color_by_level: bool = True) -> RowStyle:
    """Mirror of the colour/cue decisions of `widget.TaskbarWidget._rebuild` and `_text_color`."""
    dark = bool(dark)
    bgs = pill_backgrounds(dark)
    battery = getattr(device, "battery", None)
    connected = bool(getattr(device, "connected", True))
    grey = band_color("grey", dark)
    if getattr(device, "stale", False) and battery is not None:
        cues = ("stale",) if connected else ("stale", "disconnected")
        return RowStyle(grey, None, bgs["normal"], "grey", True, False, cues)
    if not connected:
        return RowStyle(grey, None, bgs["normal"], "grey", True, False, ("disconnected",))
    if battery is None:
        return RowStyle(grey, None, bgs["normal"], "grey", False, False, ("unknown",))
    charging = bool(getattr(device, "charging", False))
    idle = bool(getattr(device, "idle", False))
    band = level_band(battery, charging, threshold)
    alert = band == "red"
    cues = tuple(c for c, on in (("warn", band in ("orange", "red")), ("alert", alert),
                                 ("charging", charging), ("idle", idle)) if on)
    accent = band_color(band, dark) if color_by_level else None
    if accent is not None:
        fg = accent
    else:
        fg = band_color("red", dark) if alert else _plain_fg(dark)
    return RowStyle(fg, accent, bgs["low" if alert else "normal"], band, False, alert, cues)


def section_header_color(kind: str, dark: bool) -> QColor:
    """Panel section header: green for connected devices, grey for known (not connected) ones."""
    return band_color("green" if kind == "connected" else "grey", dark)
