"""Original vector icons drawn with QPainter (24x24 design grid, stroked outlines)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


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


# ----------------------------------------------------------------------------- icon styles
ICON_STYLES = ("outline", "filled", "simple", "retro", "battery", "none")
_style = "outline"


def set_icon_style(style: str) -> None:
    """Select the active device-icon style (unknown values fall back to "outline")."""
    global _style
    _style = style if style in ICON_STYLES else "outline"


def get_icon_style() -> str:
    return _style


def _grid(painter: QPainter, rect: QRectF) -> None:
    """Map the 24x24 design grid onto the centred square of ``rect`` (caller saves/restores)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)


def _draw_outline(painter: QPainter, kind: str, rect: QRectF, color: QColor, width: float = 1.7,
                  drawers=None) -> None:
    drawer = (drawers or _DRAWERS).get(kind) or _DRAWERS.get(kind, _generic)
    painter.save()
    _grid(painter, rect)
    painter.setPen(_stroke_pen(color, width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    drawer(painter)
    painter.restore()


# ---- filled: solid silhouettes, inner details punched out (drawn offscreen so Clear is safe)
def _fr(p, x, y, w, h, r=2.0):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _fpoly(p, pts):
    p.drawPolygon(QPolygonF([_P(*q) for q in pts]))


def _sline(p, c, pts, w=2.0):
    p.save()
    p.setPen(_stroke_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _poly(p, pts)
    p.restore()


def _spath(p, c, path, w=2.4):
    p.save()
    p.setPen(_stroke_pen(c, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()


def _cut_line(p, pts, w=1.5):
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(_stroke_pen(QColor(0, 0, 0), w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _poly(p, pts)
    p.restore()


def _cut_shape(p, kind, *a):
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0))
    if kind == "rr":
        _fr(p, *a)
    else:
        p.drawEllipse(_P(a[0], a[1]), a[2], a[3] if len(a) > 3 else a[2])
    p.restore()


def _f_keyboard(p, c):
    _fr(p, 2, 6, 20, 12, 2.5)
    for x in (6, 10, 14, 18):
        _cut_shape(p, "e", x, 10, 1.1)
    for x in (6, 18):
        _cut_shape(p, "e", x, 14, 1.1)
    _cut_line(p, [(9.5, 14), (14.5, 14)], 1.8)


def _f_mouse(p, c):
    _fr(p, 6.5, 2.5, 11, 19, 5.5)
    _cut_line(p, [(12, 2.5), (12, 9.5)], 1.3)
    _cut_line(p, [(6.5, 9.5), (17.5, 9.5)], 1.3)


def _f_headset(p, c):
    path = QPainterPath(_P(4.5, 15))
    path.arcTo(QRectF(4.5, 3.5, 15, 17), 180, -180)
    path.lineTo(19.5, 15)
    _spath(p, c, path, 2.4)
    _fr(p, 2.5, 12.5, 4.5, 8, 1.8)
    _fr(p, 17, 12.5, 4.5, 8, 1.8)


def _f_gamepad(p, c):
    path = QPainterPath(_P(7, 7))
    path.lineTo(17, 7)
    path.cubicTo(21, 7, 23, 12, 22, 16)
    path.cubicTo(21.5, 19, 18.5, 18.5, 17, 16)
    path.lineTo(7, 16)
    path.cubicTo(5.5, 18.5, 2.5, 19, 2, 16)
    path.cubicTo(1, 12, 3, 7, 7, 7)
    p.drawPath(path)
    _cut_line(p, [(7, 9.2), (7, 13.8)], 1.6)
    _cut_line(p, [(4.7, 11.5), (9.3, 11.5)], 1.6)
    _cut_shape(p, "e", 16, 10.5, 1.2)
    _cut_shape(p, "e", 18.5, 12.5, 1.2)


def _f_speaker(p, c):
    _fr(p, 6, 2.5, 12, 19, 2.5)
    _cut_shape(p, "e", 12, 14.5, 3.6)
    _cut_shape(p, "e", 12, 7, 1.4)


def _f_phone(p, c):
    _fr(p, 7, 2.5, 10, 19, 2.5)
    _cut_shape(p, "rr", 9, 5, 6, 10.5, 0.8)
    _cut_line(p, [(10.7, 18.5), (13.3, 18.5)], 1.4)


def _f_tablet(p, c):
    _fr(p, 4, 3.5, 16, 17, 2.5)
    _cut_shape(p, "rr", 6.5, 6, 11, 9.5, 0.8)
    _cut_line(p, [(10.7, 18), (13.3, 18)], 1.4)


def _f_watch(p, c):
    _fr(p, 7, 6.5, 10, 11, 3)
    _fpoly(p, [(9, 6), (9.8, 2.5), (14.2, 2.5), (15, 6)])
    _fpoly(p, [(9, 18), (9.8, 21.5), (14.2, 21.5), (15, 18)])
    _cut_line(p, [(12, 9.3), (12, 12), (14, 13.5)], 1.5)


def _f_computer(p, c):
    _fr(p, 3, 4.5, 18, 11.5, 2)
    _fr(p, 1.5, 18, 21, 2.4, 1.2)
    _cut_shape(p, "rr", 5, 6.5, 14, 7.5, 0.8)


def _f_pen(p, c):
    _fpoly(p, [(4.5, 19.5), (5.8, 14.8), (16.5, 4), (20, 7.5), (9.2, 18.2)])
    _cut_line(p, [(14.3, 6.4), (17.6, 9.7)], 1.3)


def _f_remote(p, c):
    _fr(p, 8, 2.5, 8, 19, 3)
    _cut_shape(p, "e", 12, 7, 1.7)
    for y in (12, 15, 18):
        _cut_line(p, [(10.5, y), (13.5, y)], 1.5)


def _f_microphone(p, c):
    _fr(p, 9, 2.5, 6, 11, 3)
    path = QPainterPath(_P(5.8, 11))
    path.arcTo(QRectF(5.8, 6.5, 12.4, 11), 180, 180)
    _spath(p, c, path, 1.9)
    _sline(p, c, [(12, 17.5), (12, 21)], 1.9)
    _sline(p, c, [(8.5, 21), (15.5, 21)], 1.9)


def _f_camera(p, c):
    _fr(p, 2.5, 7, 19, 12.5, 2.5)
    _fpoly(p, [(7.5, 7), (9.5, 4), (14.5, 4), (16.5, 7)])
    _cut_shape(p, "e", 12, 13.2, 4.2)
    p.drawEllipse(_P(12, 13.2), 2.4, 2.4)


def _f_tv(p, c):
    _fr(p, 2.5, 4.5, 19, 12.5, 2)
    _sline(p, c, [(8, 20.5), (16, 20.5)], 2)
    _sline(p, c, [(12, 17), (12, 20.5)], 2)
    _cut_shape(p, "rr", 4.8, 6.8, 14.4, 7.9, 0.8)


def _f_car(p, c):
    _fpoly(p, [(2.5, 16), (3, 11.5), (5.8, 6.5), (18.2, 6.5), (21, 11.5), (21.5, 16)])
    _cut_line(p, [(6.5, 11), (17.5, 11)], 1.3)
    _cut_shape(p, "e", 7, 17, 3.4)
    _cut_shape(p, "e", 17, 17, 3.4)
    p.drawEllipse(_P(7, 17), 2.3, 2.3)
    p.drawEllipse(_P(17, 17), 2.3, 2.3)


def _f_printer(p, c):
    _fr(p, 3, 9, 18, 8, 2)
    _fr(p, 7, 3.5, 10, 6.5, 1)
    _cut_shape(p, "rr", 6.2, 13.2, 11.6, 8.3, 1)
    _fr(p, 7.6, 14.6, 8.8, 5.4, 0.8)
    _cut_line(p, [(9.5, 17.3), (14.5, 17.3)], 1.1)


def _f_generic(p, c):
    _fr(p, 6, 6, 12, 12, 2.5)
    for v in (9.5, 14.5):
        _sline(p, c, [(v, 3), (v, 6)], 1.9)
        _sline(p, c, [(v, 18), (v, 21)], 1.9)
        _sline(p, c, [(3, v), (6, v)], 1.9)
        _sline(p, c, [(18, v), (21, v)], 1.9)
    _cut_shape(p, "e", 12, 12, 2.2)


def _f_bluetooth(p, c):
    _sline(p, c, [(7, 8), (17, 16), (12, 21), (12, 3), (17, 8), (7, 16)], 2.4)


_FILLED = {
    "keyboard": _f_keyboard, "mouse": _f_mouse, "headset": _f_headset, "gamepad": _f_gamepad,
    "speaker": _f_speaker, "phone": _f_phone, "tablet": _f_tablet, "watch": _f_watch,
    "computer": _f_computer, "pen": _f_pen, "remote": _f_remote, "microphone": _f_microphone,
    "camera": _f_camera, "tv": _f_tv, "car": _f_car, "printer": _f_printer, "generic": _f_generic,
    "bluetooth": _f_bluetooth,
}


def _draw_filled(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    w, h = max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height()))
    try:
        dpr = float(painter.device().devicePixelRatioF()) or 1.0
    except Exception:
        dpr = 1.0
    img = QImage(max(1, round(w * dpr)), max(1, round(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    c = QColor(color)
    alpha = c.alphaF()
    c.setAlpha(255)
    q = QPainter(img)
    _grid(q, QRectF(0, 0, w, h))
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(c)
    fn = _FILLED.get(kind)
    if fn is None and kind == "bluetooth_off":
        _f_bluetooth(q, c)
        _sline(q, c, [(4, 4), (20, 20)], 2.4)
    else:
        (fn or _f_generic)(q, c)
    q.end()
    painter.save()
    painter.setOpacity(painter.opacity() * alpha)
    painter.drawImage(QPointF(rect.x(), rect.y()), img)
    painter.restore()


# ---- simple: chunky geometric glyphs
_SIMPLE_COLOR = [QColor(0, 0, 0)]       # colour of the filled parts, set right before drawing


def _s_dot(p, x, y, r):
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_SIMPLE_COLOR[0])
    p.drawEllipse(_P(x, y), r, r)
    p.restore()


def _sm_mouse(p):
    _rr(p, 7, 3, 10, 18, 5)
    _poly(p, [(12, 6.5), (12, 10)])


def _sm_keyboard(p):
    _rr(p, 2.5, 6.5, 19, 11, 2)
    _poly(p, [(7, 10.8), (17, 10.8)])
    _poly(p, [(8.5, 14.2), (15.5, 14.2)])


def _sm_headset(p):
    path = QPainterPath(_P(5.5, 14))
    path.arcTo(QRectF(5.5, 4, 13, 14), 180, -180)
    p.drawPath(path)
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_SIMPLE_COLOR[0])
    p.drawRoundedRect(QRectF(3, 12.5, 5, 8), 1.8, 1.8)
    p.drawRoundedRect(QRectF(16, 12.5, 5, 8), 1.8, 1.8)
    p.restore()


def _sm_gamepad(p):
    _rr(p, 2.5, 7, 19, 10, 5)
    _s_dot(p, 8, 12, 1.5)
    _s_dot(p, 16, 12, 1.5)


def _sm_speaker(p):
    _rr(p, 6.5, 3, 11, 18, 2.5)
    _s_dot(p, 12, 15, 2.6)
    _s_dot(p, 12, 7, 1.1)


def _sm_phone(p):
    _rr(p, 7, 3, 10, 18, 2.5)
    _poly(p, [(11, 17.5), (13, 17.5)])


def _sm_generic(p):
    _rr(p, 5.5, 5.5, 13, 13, 3)
    _s_dot(p, 12, 12, 2)


_SIMPLE = {"mouse": _sm_mouse, "keyboard": _sm_keyboard, "headset": _sm_headset,
           "gamepad": _sm_gamepad, "speaker": _sm_speaker, "phone": _sm_phone, "generic": _sm_generic}


def _draw_simple(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    _SIMPLE_COLOR[0] = QColor(color)
    _draw_outline(painter, kind, rect, color, 2.3, drawers=_SIMPLE)


# ---- retro: original 16x16 pixel-art bitmaps (k outline, w highlight, g face, d shadow, a accent)
_RETRO = {
    "mouse": [
        "................",
        "....kkkkkkkk....",
        "...kwggkgggdk...",
        "...kwggagggdk...",
        "...kwggagggdk...",
        "...kwggkgggdk...",
        "...kkkkkkkkkk...",
        "...kwggggggdk...",
        "...kwggggggdk...",
        "...kwggggggdk...",
        "...kwggggggdk...",
        "...kgggggggdk...",
        "....kggggddk....",
        "....kddddddk....",
        "......kkkk......",
        "................",
    ],
    "keyboard": [
        "................",
        "................",
        "................",
        "................",
        ".kkkkkkkkkkkkkk.",
        ".kwwwwwwwwwwwwk.",
        ".kgwgwgwgwgwgdk.",
        ".kgggggggggggdk.",
        ".kgwgwgwgwgwgdk.",
        ".kggaaaaaaaagdk.",
        ".kddddddddddddk.",
        ".kkkkkkkkkkkkkk.",
        "................",
        "................",
        "................",
        "................",
    ],
    "headset": [
        "................",
        ".....kkkkkk.....",
        "...kkwggggdkk...",
        "..kwggkkkkggdk..",
        "..kwgk....kgdk..",
        "..kwgk....kgdk..",
        ".kkkkkk..kkkkkk.",
        ".kwggdk..kwggdk.",
        ".kwgadk..kwgadk.",
        ".kwgadk..kwgadk.",
        ".kwggdk..kwggdk.",
        ".kkddkk..kkddkk.",
        "..kkkk....kkkk..",
        "................",
        "................",
        "................",
    ],
    "gamepad": [
        "................",
        "................",
        "................",
        "................",
        "..kkkkkkkkkkkk..",
        ".kwggggggggggdk.",
        ".kwgkgggggaggdk.",
        ".kwkkkggggggadk.",
        ".kwgkggggggggdk.",
        ".kgggggggggggdk.",
        ".kgggkkkkkkgddk.",
        ".kgggk....kgddk.",
        ".kkkkk....kkkkk.",
        "................",
        "................",
        "................",
    ],
    "speaker": [
        "................",
        "...kkkkkkkkkk...",
        "...kwggggggdk...",
        "...kwgggkggdk...",
        "...kwggggggdk...",
        "...kwgkkkkgdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwgkkkkgdk...",
        "...kwggggggdk...",
        "...kwggggggdk...",
        "...kgggggggdk...",
        "...kddddddddk...",
        "...kkkkkkkkkk...",
        "................",
    ],
    "phone": [
        "................",
        "...kkkkkkkkkk...",
        "...kwggggggdk...",
        "...kwkkkkkkdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwkaaaakdk...",
        "...kwkkkkkkdk...",
        "...kwggggggdk...",
        "...kwggkkggdk...",
        "...kwggggggdk...",
        "...kddddddddk...",
        "...kkkkkkkkkk...",
        "................",
    ],
    "generic": [
        "................",
        "................",
        ".....k....k.....",
        ".....d....d.....",
        "...kkkkkkkkkk...",
        "...kwggggggdk...",
        ".kdkwggggggdkdk.",
        "...kwgaaaagdk...",
        "...kwgaaaagdk...",
        ".kdkwggggggdkdk.",
        "...kwggggggdk...",
        "...kkkkkkkkkk...",
        ".....d....d.....",
        ".....k....k.....",
        "................",
        "................",
    ],
}
_RETRO_ALIAS = {"bluetooth": "generic", "bluetooth_off": "generic"}
_RETRO_PAL = {"k": (0, 0, 0), "w": (255, 255, 255), "g": (192, 192, 192), "d": (128, 128, 128)}
_retro_cache: dict = {}


def retro_bitmap(kind: str) -> list[str]:
    return _RETRO.get(_RETRO_ALIAS.get(kind, kind), _RETRO["generic"])


def _retro_image(kind: str, accent: QColor) -> QImage:
    key = (_RETRO_ALIAS.get(kind, kind) if kind in _RETRO or kind in _RETRO_ALIAS else "generic",
           accent.red(), accent.green(), accent.blue())
    img = _retro_cache.get(key)
    if img is not None:
        return img
    rows = retro_bitmap(kind)
    n = len(rows)
    img = QImage(n, n, QImage.Format.Format_ARGB32)
    img.fill(0)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "a":
                img.setPixelColor(x, y, QColor(accent.red(), accent.green(), accent.blue()))
            elif ch in _RETRO_PAL:
                img.setPixelColor(x, y, QColor(*_RETRO_PAL[ch]))
    # light halo around the black outline so it survives dark backgrounds
    halo = QColor(255, 255, 255, 120)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < n and 0 <= ny < n and rows[ny][nx] == "k":
                    img.setPixelColor(x, y, halo)
                    break
    if len(_retro_cache) > 256:
        _retro_cache.clear()
    _retro_cache[key] = img
    return img


def _draw_retro(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    img = _retro_image(kind, color)
    n = img.width()
    side = min(rect.width(), rect.height())
    try:
        dpr = float(painter.device().devicePixelRatioF()) or 1.0
    except Exception:
        dpr = 1.0
    dev = side * dpr
    scale = math.floor(dev / n)
    dest = (n * scale / dpr) if scale >= 1 else side
    x = round((rect.center().x() - dest / 2) * dpr) / dpr
    y = round((rect.center().y() - dest / 2) * dpr) / dpr
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.setOpacity(painter.opacity() * color.alphaF())
    painter.drawImage(QRectF(x, y, dest, dest), img)
    painter.restore()


# ---- battery: horizontal battery gauge (ignores the device kind)
def _draw_battery(painter: QPainter, rect: QRectF, color: QColor, level: int | None,
                  charging: bool) -> None:
    painter.save()
    _grid(painter, rect)
    painter.setPen(_stroke_pen(color, 1.7))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _rr(painter, 1.5, 6.5, 18.5, 11, 2.4)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(21.2, 10, 2, 4), 0.8, 0.8)
    if level is not None:
        lv = max(0, min(100, int(level)))
        inner = QRectF(3.7, 8.7, 14.1, 6.6)
        if lv > 0:
            painter.drawRoundedRect(QRectF(inner.x(), inner.y(), max(1.5, inner.width() * lv / 100.0),
                                           inner.height()), 0.9, 0.9)
    else:
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(color)
        painter.drawText(QRectF(1.5, 6.5, 18.5, 11), int(Qt.AlignmentFlag.AlignCenter), "?")
    if charging:
        contrast = QColor(0, 0, 0) if color.lightness() > 140 else QColor(255, 255, 255)
        contrast.setAlpha(color.alpha())
        draw_bolt(painter, QRectF(7.6, 3.6, 6.8, 16.8), contrast)
        draw_bolt(painter, QRectF(8.4, 4.8, 5.2, 14.4), color)
    painter.restore()


def draw_kind_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor,
                   level: int | None = None, charging: bool = False,
                   style: str | None = None) -> None:
    """Draw the icon for ``kind`` inside ``rect`` using ``color`` (unknown kinds -> generic).

    ``style`` defaults to the active style (see ``set_icon_style``); ``level``/``charging`` are
    only used by the "battery" style. The "none" style draws nothing.
    """
    st = style if style in ICON_STYLES else _style
    if st == "none":
        return
    if st == "filled":
        _draw_filled(painter, kind, rect, color)
    elif st == "simple":
        _draw_simple(painter, kind, rect, color)
    elif st == "retro":
        _draw_retro(painter, kind, rect, color)
    elif st == "battery":
        _draw_battery(painter, rect, color, level, charging)
    else:
        _draw_outline(painter, kind, rect, color)


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


def _stroke_pen(color: QColor, width: float = 1.7) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_eye(painter: QPainter, rect: QRectF, color: QColor, open: bool = True) -> None:
    """Eye (visible) or eye with a slash (hidden), 24x24 design grid like the kind icons."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    painter.setPen(_stroke_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(_P(2, 12))
    path.cubicTo(6, 5, 18, 5, 22, 12)
    path.cubicTo(18, 19, 6, 19, 2, 12)
    painter.drawPath(path)
    if open:
        painter.drawEllipse(_P(12, 12), 3.2, 3.2)
    else:
        _poly(painter, [(4, 4), (20, 20)])
    painter.restore()


def draw_record(painter: QPainter, rect: QRectF, color: QColor, active: bool = True) -> None:
    """Record marker: filled dot inside a ring when active, ring only when not."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    painter.setPen(_stroke_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(_P(12, 12), 9, 9)
    if active:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(_P(12, 12), 4.5, 4.5)
    painter.restore()


def draw_pencil(painter: QPainter, rect: QRectF, color: QColor, flag: bool = False) -> None:
    """Pencil (edit alias), 24x24 design grid; `flag` is unused (shared icon helper signature)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    painter.setPen(_stroke_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _poly(painter, [(4, 20), (5.2, 15), (16, 4.2), (19.8, 8), (9, 18.8)], close=True)
    _poly(painter, [(13.6, 6.6), (17.4, 10.4)])
    painter.restore()


def _icon_from(draw, size: int, color: QColor, flag: bool) -> QIcon:
    icon = QIcon()
    for scale in (1, 2):
        px = max(1, int(size)) * scale
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        draw(p, QRectF(0, 0, px / scale, px / scale), color, flag)
        p.end()
        icon.addPixmap(pm)
    return icon


def eye_icon(size: int, open: bool, color: QColor) -> QIcon:
    """QIcon with 1x and 2x pixmaps of the eye (open) / slashed eye (closed)."""
    return _icon_from(draw_eye, size, color, open)


def pencil_icon(size: int, color: QColor) -> QIcon:
    """QIcon with 1x and 2x pixmaps of the pencil."""
    return _icon_from(draw_pencil, size, color, False)


def record_icon(size: int, active: bool, color: QColor) -> QIcon:
    """QIcon with 1x and 2x pixmaps of the record marker (filled when active)."""
    return _icon_from(draw_record, size, color, active)


def _stroked(painter: QPainter, rect: QRectF, color: QColor):
    """Common set-up of the 24x24 stroked toolbar glyphs (caller must painter.restore())."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    painter.setPen(_stroke_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)


def draw_refresh(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Circular arrow: a 270 degree arc with an arrow head at its end."""
    _stroked(painter, rect, color)
    path = QPainterPath()
    path.arcMoveTo(QRectF(4, 4, 16, 16), 50)
    path.arcTo(QRectF(4, 4, 16, 16), 50, 270)
    painter.drawPath(path)
    end = path.currentPosition()
    tang = (-math.sin(math.radians(320)), -math.cos(math.radians(320)))     # travel direction (y down)
    for sign in (-1, 1):
        a = math.radians(sign * 38)
        bx = -(tang[0] * math.cos(a) - tang[1] * math.sin(a))
        by = -(tang[0] * math.sin(a) + tang[1] * math.cos(a))
        _poly(painter, [(end.x(), end.y()), (end.x() + 4.5 * bx, end.y() + 4.5 * by)])
    painter.restore()


def draw_show_all(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Open eye with a small plus in the top right corner."""
    painter.save()
    draw_eye(painter, rect, color, True)
    painter.restore()
    _stroked(painter, rect, color)
    _poly(painter, [(20, 1.8), (20, 6.2)])
    _poly(painter, [(17.8, 4), (22.2, 4)])
    painter.restore()


def draw_gear(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Cog: eight trapezoid teeth around a ring with a centre hole."""
    _stroked(painter, rect, color)
    pts = []
    for i in range(8):
        base = i * 45.0
        for off, r in ((-11, 6.6), (-7, 9.6), (7, 9.6), (11, 6.6)):
            a = math.radians(base + off)
            pts.append((12 + r * math.cos(a), 12 + r * math.sin(a)))
    _poly(painter, pts, close=True)
    painter.drawEllipse(_P(12, 12), 3, 3)
    painter.restore()


def draw_about(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Circled 'i'."""
    _stroked(painter, rect, color)
    painter.drawEllipse(_P(12, 12), 9, 9)
    _poly(painter, [(12, 11), (12, 16.5)])
    painter.drawPoint(_P(12, 7.7))
    painter.restore()


def draw_power(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Power symbol: open ring with a bar."""
    _stroked(painter, rect, color)
    path = QPainterPath()
    path.arcMoveTo(QRectF(4.5, 5, 15, 15), 120)
    path.arcTo(QRectF(4.5, 5, 15, 15), 120, 300)
    painter.drawPath(path)
    _poly(painter, [(12, 2.8), (12, 11)])
    painter.restore()


def refresh_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_refresh, size, color, True)


def show_all_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_show_all, size, color, True)


def gear_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_gear, size, color, True)


def about_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_about, size, color, True)


def power_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_power, size, color, True)
