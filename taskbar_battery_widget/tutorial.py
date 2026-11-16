"""First-run mini tutorial: six short steps, each with a simple drawing made with QPainter.

The drawings reuse the app's own icons (icons.py), so they follow whatever the app looks like. The dialog is
keyboard driven (Left/Right, Enter, Escape) and every step has an accessible name. Finishing it, skipping it
or closing it with Escape all count as "seen" (config ``tutorial_seen``) so it is only shown once on its own;
Settings has an entry to open it again.
"""
from __future__ import annotations

import html
import logging

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import icons
from .dialogs import app_dialog_guard
from .i18n import tr

log = logging.getLogger(__name__)

STEPS = 6
ILLUSTRATION_H = 176
GREEN, YELLOW, RED = QColor("#4CAF50"), QColor("#F5C842"), QColor("#E5484D")


class _Colors:
    """Palette-derived colours so the drawings read well on light and dark themes."""

    def __init__(self, widget: QWidget):
        pal = widget.palette()
        self.bg = pal.window().color()
        self.dark = self.bg.lightness() < 128
        self.fg = pal.windowText().color()
        self.accent = pal.highlight().color()
        if self.accent.lightness() < 60 and self.dark:
            self.accent = QColor("#5B9DFF")
        self.card = QColor(255, 255, 255, 22) if self.dark else QColor(0, 0, 0, 14)
        self.line = QColor(255, 255, 255, 70) if self.dark else QColor(0, 0, 0, 60)
        self.muted = QColor(self.fg)
        self.muted.setAlpha(110)
        self.bar = QColor("#2B2F36") if self.dark else QColor("#E3E6EB")
        self.bar_edge = QColor(255, 255, 255, 60) if self.dark else QColor(0, 0, 0, 50)


def _round(p: QPainter, rect: QRectF, radius: float, fill: QColor | None, edge: QColor | None = None,
           width: float = 1.0) -> None:
    p.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    p.setPen(QPen(edge, width) if edge is not None else Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, radius, radius)


def _text(p: QPainter, rect: QRectF, text: str, color: QColor, px: float, bold: bool = False,
          align=Qt.AlignmentFlag.AlignCenter) -> None:
    f = QFont(p.font())
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    p.setFont(f)
    p.setPen(color)
    p.drawText(rect, int(align), text)


def _pill(p: QPainter, c: _Colors, x: float, y: float, kind: str, level: int, color: QColor,
          h: float = 30) -> float:
    """Taskbar pill: icon + percentage. Returns its width."""
    w = h * 2.15
    rect = QRectF(x, y, w, h)
    _round(p, rect, h / 2, c.card, c.line)
    icons.draw_kind_icon(p, kind, QRectF(x + 7, y + h * 0.18, h * 0.64, h * 0.64), color, style="outline")
    _text(p, QRectF(x + h * 0.9, y, w - h * 0.95, h), f"{level}%", c.fg, h * 0.42, True)
    return w


def _ring(p: QPainter, c: _Colors, rect: QRectF) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(c.accent, 2.2))
    p.drawEllipse(rect.adjusted(-5, -5, 5, 5))


def _cursor(p: QPainter, c: _Colors, x: float, y: float) -> None:
    poly = QPolygonF([QPointF(x, y), QPointF(x, y + 20), QPointF(x + 5, y + 15), QPointF(x + 9, y + 24),
                      QPointF(x + 13, y + 22), QPointF(x + 9, y + 14), QPointF(x + 16, y + 14)])
    p.setBrush(c.fg)
    p.setPen(QPen(c.bg, 1.4))
    p.drawPolygon(poly)


def _panel_card(p: QPainter, c: _Colors, rect: QRectF) -> None:
    _round(p, rect, 10, c.bar, c.accent, 1.6)


def _row_bars(p: QPainter, c: _Colors, x: float, y: float, w: float, h: float = 7) -> None:
    _round(p, QRectF(x, y, w, h), h / 2, c.muted)


# ------------------------------------------------------------------ the six drawings
def _draw_1(p: QPainter, c: _Colors, w: float, h: float) -> None:
    # the panel peeking above the taskbar
    panel = QRectF(w / 2 + 10, 8, 150, 78)
    _panel_card(p, c, panel)
    for i, kind in enumerate(("mouse", "headset")):
        y = panel.top() + 10 + i * 30
        icons.draw_kind_icon(p, kind, QRectF(panel.left() + 10, y, 20, 20), c.fg, style="outline")
        _row_bars(p, c, panel.left() + 38, y + 3, 58)
        _row_bars(p, c, panel.left() + 38, y + 13, 34, 5)
    # taskbar strip
    bar = QRectF(24, h - 62, w - 48, 50)
    _round(p, bar, 10, c.bar, c.bar_edge)
    x = bar.right() - 14 - 2 * 65 - 40
    first = x
    x += _pill(p, c, x, bar.top() + 10, "mouse", 87, GREEN) + 8
    x += _pill(p, c, x, bar.top() + 10, "headset", 42, YELLOW) + 12
    tray = icons.make_tray_icon(80, c.dark).pixmap(22, 22)
    p.drawPixmap(int(x), int(bar.top() + 14), tray)
    _cursor(p, c, first + 56, bar.top() + 30)


def _draw_2(p: QPainter, c: _Colors, w: float, h: float) -> None:
    card = QRectF(w / 2 - 150, 10, 300, h - 20)
    _panel_card(p, c, card)
    for i, (kind, visible) in enumerate((("mouse", True), ("keyboard", False))):
        y = card.top() + 16 + i * 66
        icons.draw_kind_icon(p, kind, QRectF(card.left() + 14, y + 6, 30, 30), c.fg if visible else c.muted,
                             style="outline")
        _row_bars(p, c, card.left() + 56, y + 8, 80, 9)
        _row_bars(p, c, card.left() + 56, y + 24, 50, 6)
        eye = QRectF(card.right() - 90, y + 8, 26, 26)
        pencil = QRectF(card.right() - 50, y + 8, 26, 26)
        icons.draw_eye(p, eye, c.fg if visible else c.muted, open=visible)
        icons.draw_pencil(p, pencil, c.fg)
        if i == 0:
            _ring(p, c, eye)
            _ring(p, c, pencil)


def _draw_3(p: QPainter, c: _Colors, w: float, h: float) -> None:
    card = QRectF(w / 2 - 160, 10, 320, h - 20)
    _panel_card(p, c, card)
    y = card.top() + 12
    icons.draw_kind_icon(p, "headset", QRectF(card.left() + 14, y + 4, 28, 28), c.fg, style="outline")
    _row_bars(p, c, card.left() + 54, y + 9, 80, 9)
    rec = QRectF(card.right() - 100, y + 4, 26, 26)
    icons.draw_record(p, rec, RED, True)
    _ring(p, c, rec)
    dots = QRectF(card.right() - 62, y + 4, 40, 26)
    _round(p, dots, 6, c.card, c.line)
    _text(p, dots, "…", c.fg, 18, True)
    # mini discharge chart + estimate
    chart = QRectF(card.left() + 16, y + 48, card.width() - 32, h - 20 - 48 - 34)
    _round(p, chart, 6, c.card, c.line)
    path = QPainterPath(QPointF(chart.left() + 8, chart.top() + 10))
    pts = [(0.18, 0.28), (0.34, 0.42), (0.5, 0.55), (0.62, 0.8), (0.64, 0.2), (0.8, 0.34), (0.95, 0.5)]
    for fx, fy in pts:
        path.lineTo(chart.left() + 8 + fx * (chart.width() - 16), chart.top() + 8 + fy * (chart.height() - 16))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(GREEN, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    _text(p, QRectF(card.left() + 16, card.bottom() - 34, card.width() - 32, 26),
          "≈ " + tr("dur_h", n=6), c.fg, 15, True)


def _draw_4(p: QPainter, c: _Colors, w: float, h: float) -> None:
    gear = QRectF(w / 2 - 190, h / 2 - 22, 44, 44)
    icons.draw_gear(p, gear, c.fg)
    _ring(p, c, gear)
    # arrow
    ax = gear.right() + 14
    p.setPen(QPen(c.accent, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(ax, h / 2), QPointF(ax + 38, h / 2))
    p.drawLine(QPointF(ax + 38, h / 2), QPointF(ax + 30, h / 2 - 7))
    p.drawLine(QPointF(ax + 38, h / 2), QPointF(ax + 30, h / 2 + 7))
    card = QRectF(ax + 56, 10, 250, h - 20)
    _panel_card(p, c, card)
    icons.draw_back(p, QRectF(card.left() + 12, card.top() + 10, 22, 22), c.fg)
    _row_bars(p, c, card.left() + 44, card.top() + 17, 90, 9)
    y = card.top() + 44
    for i in range(3):
        _round(p, QRectF(card.left() + 10, y, card.width() - 20, 24), 6, c.card)
        _row_bars(p, c, card.left() + 20, y + 9, 70 + 12 * i)
        chev = icons.draw_chevron_down if i == 1 else icons.draw_chevron_right
        chev(p, QRectF(card.right() - 34, y + 3, 18, 18), c.fg)
        y += 28
        if i == 1:
            for j in range(2):
                p.setPen(QPen(c.fg, 1.4))
                p.setBrush(c.accent if j == 0 else Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(card.left() + 28, y + 6), 4.5, 4.5)
                _row_bars(p, c, card.left() + 42, y + 3, 60, 6)
                y += 16


def _draw_5(p: QPainter, c: _Colors, w: float, h: float) -> None:
    cx = w / 2
    # colour swatches
    for i, col in enumerate((GREEN, YELLOW, RED)):
        p.setPen(QPen(c.line, 1))
        p.setBrush(col)
        p.drawEllipse(QPointF(cx - 150 + i * 30, 36), 11, 11)
    # icon styles
    for i, style in enumerate(("outline", "filled", "retro")):
        icons.draw_kind_icon(p, "gamepad", QRectF(cx + 10 + i * 44, 20, 32, 32), c.fg, style=style)
    # scale + language chips
    _text(p, QRectF(cx - 160, 66, 40, 34), "A", c.fg, 14, True)
    _text(p, QRectF(cx - 126, 62, 46, 40), "A", c.fg, 26, True)
    for i, code in enumerate(("EN", "ES", "FR")):
        chip = QRectF(cx + 10 + i * 48, 70, 40, 26)
        _round(p, chip, 13, c.card, c.line)
        _text(p, chip, code, c.fg, 12, True)
    # floating pill dragged around
    pill_rect = QRectF(cx - 70, h - 66, 140, 48)
    pen = QPen(c.accent, 1.6, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(pill_rect.translated(-26, 6), 24, 24)
    _pill(p, c, pill_rect.left() + 20, pill_rect.top() + 9, "mouse", 87, GREEN)
    # move arrows
    mx, my = pill_rect.right() + 26, pill_rect.center().y()
    p.setPen(QPen(c.accent, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tip = QPointF(mx + dx * 16, my + dy * 16)
        p.drawLine(QPointF(mx, my), tip)
        p.drawLine(tip, QPointF(tip.x() - dx * 6 + dy * 5, tip.y() - dy * 6 + dx * 5))
        p.drawLine(tip, QPointF(tip.x() - dx * 6 - dy * 5, tip.y() - dy * 6 - dx * 5))


def _draw_6(p: QPainter, c: _Colors, w: float, h: float) -> None:
    screen = QRectF(24, 10, w / 2 - 10, h - 20)
    _round(p, screen, 8, QColor("#1B2A41") if c.dark else QColor("#CFE0F5"), c.line)
    _round(p, screen.adjusted(10, 10, -10, -30), 5, QColor(255, 255, 255, 28))
    strip = QRectF(screen.left() + 8, screen.bottom() - 20, screen.width() - 16, 12)
    p.setPen(QPen(c.line, 1, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(strip, 5, 5)
    _cursor(p, c, screen.center().x(), screen.center().y() - 12)
    # Shift keycap
    cap = QRectF(w / 2 + 40, 18, 96, 40)
    _round(p, cap, 8, c.card, c.accent, 2)
    _text(p, cap, "Shift", c.fg, 16, True)
    # alerts
    bell = QRectF(w / 2 + 40, h - 70, 34, 34)
    icons.glyph_icon("alerts", 34, c.fg).paint(p, bell.toRect())
    _text(p, QRectF(bell.right() + 6, bell.top(), 44, 34), "20%", RED, 15, True)
    batt = QRectF(w / 2 + 134, h - 70, 34, 34)
    icons.draw_bolt(p, batt, GREEN)
    _text(p, QRectF(batt.right() + 4, batt.top(), 52, 34), "100%", GREEN, 15, True)


_DRAW = (_draw_1, _draw_2, _draw_3, _draw_4, _draw_5, _draw_6)


class _Illustration(QWidget):
    """The drawing of the current step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.step = 0
        self.setFixedHeight(ILLUSTRATION_H)
        self.setMinimumWidth(460)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_step(self, step: int) -> None:
        self.step = max(0, min(STEPS - 1, step))
        self.update()

    def paintEvent(self, _event) -> None:
        c = _Colors(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            _round(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, c.card)
            _DRAW[self.step](p, c, float(self.width()), float(self.height()))
        finally:
            p.end()


class _Dots(QWidget):
    """Step indicator: one dot per step, the current one filled."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.step = 0
        self.setFixedHeight(16)
        self.setMinimumWidth(STEPS * 18)

    def set_step(self, step: int) -> None:
        self.step = step
        self.update()

    def paintEvent(self, _event) -> None:
        c = _Colors(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            step_w = 18
            x0 = (self.width() - STEPS * step_w) / 2 + step_w / 2
            for i in range(STEPS):
                cur = i == self.step
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c.accent if cur else c.muted)
                r = 4.5 if cur else 3.2
                p.drawEllipse(QPointF(x0 + i * step_w, self.height() / 2), r, r)
        finally:
            p.end()


class TutorialDialog(QDialog):
    """Six-step tour. ``finished`` is "done" (Get started) or "skipped" (Skip / Escape / window close)."""

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.step = 0
        self.finished = ""
        self.setWindowTitle(tr("tut_title"))
        self.setModal(True)
        self.setMinimumWidth(500)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.step_label = QLabel()
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.step_label)
        self.art = _Illustration(self)
        root.addWidget(self.art)
        self.title_label = QLabel()
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumHeight(64)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.text_label)
        self.dots = _Dots(self)
        root.addWidget(self.dots)

        row = QHBoxLayout()
        self.skip_button = QPushButton(tr("tut_skip"))
        self.prev_button = QPushButton(tr("tut_prev"))
        self.next_button = QPushButton(tr("tut_next"))
        self.next_button.setDefault(True)
        self.skip_button.clicked.connect(self.skip)
        self.prev_button.clicked.connect(self.previous)
        self.next_button.clicked.connect(self.next)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.prev_button)
        row.addWidget(self.next_button)
        root.addLayout(row)
        self.setTabOrder(self.next_button, self.prev_button)
        self.setTabOrder(self.prev_button, self.skip_button)
        self._show_step()
        self.next_button.setFocus()

    # ---- navigation
    def _show_step(self) -> None:
        n, last = self.step + 1, self.step == STEPS - 1
        title, text = tr(f"tut_{n}_title"), tr(f"tut_{n}_text")
        progress = tr("tut_step", n=n, total=STEPS)
        self.step_label.setText(f"<span style='color:gray'>{html.escape(progress)}</span>")
        self.title_label.setText(f"<b style='font-size:14pt'>{html.escape(title)}</b>")
        self.text_label.setText(text)
        self.art.set_step(self.step)
        self.dots.set_step(self.step)
        self.prev_button.setEnabled(self.step > 0)
        self.skip_button.setVisible(not last)
        self.next_button.setText(tr("tut_start") if last else tr("tut_next"))
        # accessibility: the screen reader hears "Step 2 of 6: title" and the text
        self.setAccessibleDescription(f"{progress}: {title}. {text}")
        self.title_label.setAccessibleName(f"{progress}: {title}")
        self.text_label.setAccessibleName(text)
        self.art.setAccessibleName(title)
        self.art.setAccessibleDescription(text)
        self.dots.setAccessibleName(progress)
        self.next_button.setAccessibleName(self.next_button.text())

    def next(self) -> None:
        if self.step >= STEPS - 1:
            self.finished = "done"
            self.accept()
            return
        self.step += 1
        self._show_step()

    def previous(self) -> None:
        if self.step > 0:
            self.step -= 1
            self._show_step()

    def skip(self) -> None:
        self.finished = "skipped"
        self.reject()

    def done(self, result: int) -> None:
        """Every way out counts as seen: the tutorial never reappears on its own."""
        if not self.finished:
            self.finished = "skipped"
        if self._cfg is not None:
            try:
                self._cfg.set("tutorial_seen", True)
            except ValueError:
                log.info("could not save tutorial_seen")
        super().done(result)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Right:
            if self.step < STEPS - 1:
                self.next()
        elif key == Qt.Key.Key_Left:
            self.previous()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            if isinstance(focus, QPushButton) and focus is not self.next_button:
                focus.click()
            else:
                self.next()
        else:
            super().keyPressEvent(event)             # Escape -> reject -> done()


def show_tutorial(cfg=None, parent=None) -> str:
    """Run the tutorial modally (guarded: the device panel stays open). Returns "done" or "skipped"."""
    dlg = TutorialDialog(cfg, parent)
    with app_dialog_guard():
        dlg.exec()
    return dlg.finished
