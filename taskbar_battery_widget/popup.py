"""Custom hover card for the taskbar widget (Qt tooltips are unreliable on this tool window)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QDateTime, QLocale, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .colors import band_color, level_band
from .i18n import kind_name as _kind_name, tr
from .models import Device
from .naming import sanitize_name

HOVER_DELAY_MS = 300
GAP = 8
PAD_X, PAD_Y = 12, 9
MAX_W = 320
LINE_GAP = 3


@dataclass
class PopupContent:
    title: str
    lines: list[str] = field(default_factory=list)
    dot_band: str | None = None     # colour band of the dot drawn before lines[dot_line]
    dot_line: int = 1


# --------------------------------------------------------------------------- pure helpers
def link_label(device: Device) -> str:
    link = getattr(device, "link", "bluetooth")
    return tr({"2.4ghz": "link_24ghz", "usb": "link_usb"}.get(link, "link_bluetooth"))


def status_text(device: Device, low_threshold: int) -> str:
    if not device.connected:
        return tr("disconnected")
    if device.battery is None:
        return tr("battery_unknown")
    if getattr(device, "idle", False):
        return tr("idle")
    if device.charging:
        return tr("charging")
    if device.battery >= 100:
        return tr("status_full")
    if device.battery <= low_threshold:
        return tr("status_low")
    return tr("status_ok")


def format_updated(updated: float | None, now: float | None = None) -> str:
    """'Updated 17:14' (with the date when it is not today), in the user's locale; '' when unknown."""
    if updated is None:
        return ""
    try:
        now = time.time() if now is None else now
        t, n = time.localtime(updated), time.localtime(now)
        dt = QDateTime.fromSecsSinceEpoch(int(updated))
        loc = QLocale()
        if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
            stamp = loc.toString(dt.time(), QLocale.FormatType.ShortFormat)
        else:
            stamp = loc.toString(dt, QLocale.FormatType.ShortFormat)
    except (OverflowError, OSError, ValueError):
        return ""
    return tr("updated_at", time=stamp)


def idle_text(device: Device, now: float | None = None) -> str:
    """'Asleep - last value N min (or N h) ago' (plain 'Asleep' when the age is unknown)."""
    updated = getattr(device, "updated", None)
    if updated is None:
        return tr("idle")
    now = time.time() if now is None else now
    minutes = max(1, round((now - updated) / 60))
    if minutes >= 60:
        return tr("idle_since_h", h=round(minutes / 60))
    return tr("idle_since", min=minutes)


def wrap_title(text: str, fm: QFontMetrics, width: int, max_lines: int = 2) -> list[str]:
    """Greedy wrap into at most `max_lines` lines; the last one is elided instead of truncated."""
    lines: list[str] = []
    rest = text
    while rest and len(lines) < max_lines - 1:
        if fm.horizontalAdvance(rest) <= width:
            break
        cut = 0
        for i in range(1, len(rest) + 1):
            if fm.horizontalAdvance(rest[:i]) > width:
                break
            cut = i
        cut = max(cut, 1)
        sp = rest.rfind(" ", 1, cut + 1)
        if sp > 0:
            lines.append(rest[:sp])
            rest = rest[sp:].lstrip()
        else:
            lines.append(rest[:cut])
            rest = rest[cut:]
    if rest:
        lines.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
    return lines or [""]


def build_popup_content(device: Device, low_threshold: int = 20, now: float | None = None) -> PopupContent:
    """Plain-text card content (never markup): name, kind - link, battery - status, update time."""
    name = sanitize_name(device.name) or "?"
    kind = _kind_name(device.kind) if device.kind else ""
    link = link_label(device)
    lines = [f"{kind} - {link}" if kind else link]
    if device.connected and device.battery is not None:
        idle = getattr(device, "idle", False)
        state = idle_text(device, now) if idle else status_text(device, low_threshold)
        lines.append(f"{device.battery}% - {state}")
    else:
        lines.append(status_text(device, low_threshold))
    upd = "" if getattr(device, "idle", False) else format_updated(getattr(device, "updated", None), now)
    if upd:
        lines.append(upd)
    band = (level_band(device.battery, bool(device.charging), low_threshold)
            if device.connected and device.battery is not None else None)
    return PopupContent(name, lines, dot_band=band)


def place_popup(anchor: tuple[int, int, int, int], size: tuple[int, int],
                screen: tuple[int, int, int, int], edge: str = "bottom",
                gap: int = GAP) -> tuple[int, int]:
    """Top-left of the popup: above a bottom taskbar, below a top one, beside a side one.

    anchor/screen are (x, y, w, h) in the same logical coordinates; the result is clamped to screen.
    """
    ax, ay, aw, ah = anchor
    pw, ph = size
    sx, sy, sw, sh = screen
    if edge == "top":
        x, y = ax + (aw - pw) // 2, ay + ah + gap
    elif edge == "left":
        x, y = ax + aw + gap, ay + (ah - ph) // 2
    elif edge == "right":
        x, y = ax - pw - gap, ay + (ah - ph) // 2
    else:
        x, y = ax + (aw - pw) // 2, ay - ph - gap
    x = max(sx, min(x, sx + sw - pw))
    y = max(sy, min(y, sy + sh - ph))
    return x, y


# --------------------------------------------------------------------------- the widget
DOT = 8
DOT_GAP = 6


class HoverPopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._content = PopupContent("")
        self._dark = True
        self._styled = False
        self._title: list[str] = [""]
        self._lines: list[str] = []
        self._dot: QColor | None = None

    def _fonts(self) -> tuple[QFont, QFont]:
        body = QFont("Segoe UI")
        body.setPixelSize(12)
        title = QFont(body)
        title.setPixelSize(13)
        title.setWeight(QFont.Weight.Bold)
        return title, body

    def _dot_indent(self) -> int:
        return DOT + DOT_GAP if self._dot is not None else 0

    def set_content(self, content: PopupContent, dark: bool = True, show_dot: bool = True) -> None:
        self._content, self._dark = content, dark
        self._dot = band_color(content.dot_band, dark) if show_dot and content.dot_band else None
        tf, bf = self._fonts()
        tfm, bfm = QFontMetrics(tf), QFontMetrics(bf)
        inner = MAX_W - 2 * PAD_X
        self._title = wrap_title(content.title, tfm, inner)
        ind = self._dot_indent()
        self._lines = [bfm.elidedText(t, Qt.TextElideMode.ElideRight,
                                      inner - (ind if i == content.dot_line else 0))
                       for i, t in enumerate(content.lines)]
        w = max([tfm.horizontalAdvance(t) for t in self._title]
                + [bfm.horizontalAdvance(t) + (ind if i == content.dot_line else 0)
                   for i, t in enumerate(self._lines)])
        h = tfm.height() * len(self._title) + sum(bfm.height() + LINE_GAP for _ in self._lines)
        self.setFixedSize(w + 2 * PAD_X, h + 2 * PAD_Y)
        self.setAccessibleName(content.title)
        self.setAccessibleDescription(", ".join(content.lines))
        self.update()

    def show_at(self, pos: tuple[int, int]) -> None:
        self.move(*pos)
        if not self.isVisible():
            self.show()
        self.raise_topmost()

    def raise_topmost(self) -> None:
        if not self.isVisible():
            return
        from . import widget as _w        # lazy: widget imports this module
        if not self._styled:
            self._styled = True
            _w._apply_ex_styles(int(self.winId()))
        _w._assert_topmost(int(self.winId()))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._dark:
            bg, border, fg, sub = QColor(32, 32, 32, 240), QColor(255, 255, 255, 40), \
                QColor(255, 255, 255), QColor(200, 200, 200)
        else:
            bg, border, fg, sub = QColor(249, 249, 249, 245), QColor(0, 0, 0, 40), \
                QColor(20, 20, 20), QColor(80, 80, 80)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setBrush(bg)
        p.setPen(border)
        p.drawRoundedRect(r, 8, 8)
        tf, bf = self._fonts()
        y = PAD_Y
        p.setFont(tf)
        p.setPen(fg)
        th = QFontMetrics(tf).height()
        for t in self._title:
            p.drawText(QRectF(PAD_X, y, self.width() - 2 * PAD_X, th),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), t)
            y += th
        p.setFont(bf)
        p.setPen(sub)
        bh = QFontMetrics(bf).height()
        ind = self._dot_indent()
        for i, t in enumerate(self._lines):
            x = PAD_X
            if i == self._content.dot_line and self._dot is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._dot)
                p.drawEllipse(QRectF(PAD_X, y + LINE_GAP + (bh - DOT) / 2, DOT, DOT))
                p.setPen(sub)
                x += ind
            p.drawText(QRectF(x, y + LINE_GAP, self.width() - PAD_X - x, bh),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), t)
            y += bh + LINE_GAP
        p.end()
