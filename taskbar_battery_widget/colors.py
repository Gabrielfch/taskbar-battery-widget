"""Battery-level colours (pure, no widgets): readable on dark and light taskbars."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

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


def color_dot_icon(color: QColor, size: int = 16) -> QIcon:
    """Small filled circle used as the menu icon of a device."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    m = size * 0.2
    p.drawEllipse(int(m), int(m), int(size - 2 * m), int(size - 2 * m))
    p.end()
    return QIcon(pm)
