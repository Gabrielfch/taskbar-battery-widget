"""Right-click on the taskbar pills opens the device panel anchored to the widget."""
from PySide6.QtCore import QPoint, QRect

from taskbar_battery_widget import app


class _FakeWidget:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def mapToGlobal(self, p: QPoint) -> QPoint:
        return QPoint(self._x + p.x(), self._y + p.y())

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h


def test_widget_anchor_is_global_widget_rect():
    assert app.widget_anchor(_FakeWidget(1500, 1040, 180, 40)) == QRect(1500, 1040, 180, 40)


def test_main_wires_pill_context_menu_to_panel():
    src = open(app.__file__, encoding="utf-8").read()
    assert "widget.set_menu(" not in src
    assert "context_menu_requested.connect" in src and "tray.open_panel(widget_anchor(widget))" in src
