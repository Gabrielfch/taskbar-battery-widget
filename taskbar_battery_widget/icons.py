"""Original vector icons drawn with QPainter (24x24 design grid, stroked outlines)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _P(x, y):
    return QPointF(x, y)


def _poly(p: QPainter, pts, close=False):
    path = QPainterPath(_P(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(_P(*pt))
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _rr(p, x, y, w, h, r=2.0):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _keyboard(p):
    _rr(p, 2, 6, 20, 12, 2.5)
    for x in (6, 10, 14, 18):
        p.drawPoint(_P(x, 10))
    for x in (6, 18):
        p.drawPoint(_P(x, 14))
    _poly(p, [(9, 14), (15, 14)])


def _mouse(p):
    _rr(p, 6.5, 2.5, 11, 19, 5.5)
    _poly(p, [(12, 2.5), (12, 9.5)])
    _poly(p, [(6.5, 9.5), (17.5, 9.5)])


def _headset(p):
    path = QPainterPath(_P(4, 15))
    path.arcTo(QRectF(4, 3, 16, 18), 180, -180)
    path.lineTo(20, 15)
    p.drawPath(path)
    _rr(p, 2.5, 13, 4, 7, 1.5)
    _rr(p, 17.5, 13, 4, 7, 1.5)


def _gamepad(p):
    path = QPainterPath(_P(7, 7))
    path.lineTo(17, 7)
    path.cubicTo(21, 7, 23, 12, 22, 16)
    path.cubicTo(21.5, 19, 18.5, 18.5, 17, 16)
    path.lineTo(7, 16)
    path.cubicTo(5.5, 18.5, 2.5, 19, 2, 16)
    path.cubicTo(1, 12, 3, 7, 7, 7)
    p.drawPath(path)
    _poly(p, [(7, 9.5), (7, 13.5)])
    _poly(p, [(5, 11.5), (9, 11.5)])
    p.drawPoint(_P(16, 10.5))
    p.drawPoint(_P(18.5, 12.5))


def _speaker(p):
    _rr(p, 6, 2.5, 12, 19, 2.5)
    p.drawEllipse(_P(12, 14.5), 3.5, 3.5)
    p.drawEllipse(_P(12, 7), 1.2, 1.2)


def _phone(p):
    _rr(p, 7, 2.5, 10, 19, 2.5)
    _poly(p, [(10.5, 18.5), (13.5, 18.5)])


def _tablet(p):
    _rr(p, 4, 3.5, 16, 17, 2.5)
    _poly(p, [(10.5, 17.5), (13.5, 17.5)])


def _watch(p):
    _rr(p, 7, 6.5, 10, 11, 3)
    _poly(p, [(9, 6.5), (9.8, 2.5), (14.2, 2.5), (15, 6.5)])
    _poly(p, [(9, 17.5), (9.8, 21.5), (14.2, 21.5), (15, 17.5)])
    _poly(p, [(12, 9.5), (12, 12), (14, 13.5)])


def _computer(p):
    _rr(p, 3, 4.5, 18, 11.5, 2)
    _poly(p, [(2, 19.5), (22, 19.5)])


def _pen(p):
    _poly(p, [(5, 19), (6, 15), (16.5, 4.5), (19.5, 7.5), (9, 18), (5, 19)])
    _poly(p, [(14.5, 6.5), (17.5, 9.5)])


def _remote(p):
    _rr(p, 8, 2.5, 8, 19, 3)
    p.drawEllipse(_P(12, 7), 1.6, 1.6)
    _poly(p, [(10.5, 12), (13.5, 12)])
    _poly(p, [(10.5, 15), (13.5, 15)])
    _poly(p, [(10.5, 18), (13.5, 18)])


def _microphone(p):
    _rr(p, 9, 2.5, 6, 11, 3)
    path = QPainterPath(_P(5.5, 11))
    path.arcTo(QRectF(5.5, 6.5, 13, 11), 180, 180)
    p.drawPath(path)
    _poly(p, [(12, 17.5), (12, 21)])
    _poly(p, [(8.5, 21), (15.5, 21)])


def _camera(p):
    _rr(p, 2.5, 7, 19, 12.5, 2.5)
    _poly(p, [(8, 7), (9.5, 4.5), (14.5, 4.5), (16, 7)])
    p.drawEllipse(_P(12, 13.2), 3.6, 3.6)


def _tv(p):
    _rr(p, 2.5, 4.5, 19, 12.5, 2)
    _poly(p, [(8, 20.5), (16, 20.5)])
    _poly(p, [(12, 17), (12, 20.5)])


def _car(p):
    _poly(p, [(3, 15.5), (3.5, 11.5), (6, 7), (18, 7), (20.5, 11.5), (21, 15.5)], close=False)
    _poly(p, [(3, 15.5), (21, 15.5)])
    _poly(p, [(6.5, 11.5), (17.5, 11.5)])
    p.drawEllipse(_P(7, 17), 2, 2)
    p.drawEllipse(_P(17, 17), 2, 2)


def _printer(p):
    _rr(p, 3, 9, 18, 8, 2)
    _poly(p, [(7, 9), (7, 3.5), (17, 3.5), (17, 9)])
    _rr(p, 7, 14, 10, 6.5, 1)


def _generic(p):
    # a small "chip": rounded square with pins
    _rr(p, 6, 6, 12, 12, 2.5)
    for v in (9.5, 14.5):
        _poly(p, [(v, 3), (v, 6)])
        _poly(p, [(v, 18), (v, 21)])
        _poly(p, [(3, v), (6, v)])
        _poly(p, [(18, v), (21, v)])


def _bluetooth(p):
    _poly(p, [(7, 8), (17, 16), (12, 21), (12, 3), (17, 8), (7, 16)])


def _bluetooth_off(p):
    _bluetooth(p)
    _poly(p, [(4, 4), (20, 20)])


_DRAWERS = {
    "keyboard": _keyboard, "mouse": _mouse, "headset": _headset, "gamepad": _gamepad,
    "speaker": _speaker, "phone": _phone, "tablet": _tablet, "watch": _watch,
    "computer": _computer, "pen": _pen, "remote": _remote, "microphone": _microphone,
    "camera": _camera, "tv": _tv, "car": _car, "printer": _printer, "generic": _generic,
    "bluetooth": _bluetooth, "bluetooth_off": _bluetooth_off,
}


def draw_kind_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """Draw the icon for ``kind`` inside ``rect`` using ``color`` (unknown kinds -> generic)."""
    drawer = _DRAWERS.get(kind, _generic)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    pen = QPen(color, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    drawer(painter)
    painter.restore()


def draw_bolt(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Small filled lightning bolt (charging marker)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    w, h = rect.width(), rect.height()

    def pt(x, y):
        return QPointF(rect.x() + x * w, rect.y() + y * h)

    poly = QPolygonF([pt(0.60, 0.0), pt(0.10, 0.58), pt(0.46, 0.58), pt(0.36, 1.0),
                      pt(0.92, 0.38), pt(0.54, 0.38)])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(poly)
    painter.restore()


def make_tray_icon(level: int | None, dark: bool, fill_color: QColor | None = None) -> QIcon:
    """Battery glyph with fill proportional to ``level`` (None -> outline with '?').

    ``dark`` means the taskbar is dark, so strokes are light.
    """
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fg = QColor(255, 255, 255) if dark else QColor(32, 32, 32)
    low = level is not None and level <= 20
    fill = QColor(235, 70, 70) if low else (QColor(120, 220, 130) if dark else QColor(30, 150, 60))
    if fill_color is not None:
        fill = QColor(fill_color)
    body = QRectF(6, 14, 46, 36)
    pen = QPen(fg, 4)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(body, 7, 7)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(54, 25, 6, 14), 2, 2)
    if level is not None:
        lv = max(0, min(100, level))
        inner = QRectF(body.x() + 5, body.y() + 5, body.width() - 10, body.height() - 10)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(inner.x(), inner.y(), max(3.0, inner.width() * lv / 100), inner.height()), 3, 3)
    else:
        f = QFont()
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, "?")
    p.end()
    return QIcon(pm)
