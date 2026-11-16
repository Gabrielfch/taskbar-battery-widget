"""Guard around the dialogs the application opens (file pickers, message boxes, colour pickers...).

The device panel closes itself when the application stops being active. A native Windows common dialog
(QFileDialog's static helpers, QColorDialog...) is not a QWindow, so while it is up Qt sees "no focus
window" / an inactive application and the panel would hide, taking its child dialog down with it.
Every dialog opening goes through `app_dialog_guard()`: while one is open `dialog_open()` is true, the
panel neither hides nor keeps its topmost band (so the dialog is drawn in front), and when the last one
closes the panel re-evaluates its state (`closed` signal).
"""
from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication


class _Hub(QObject):
    opened = Signal()       # first dialog opened
    closed = Signal()       # last dialog closed


hub = _Hub()
_depth = 0


def dialog_open() -> bool:
    """A dialog of this application is open (explicit guard, or any Qt application-modal widget)."""
    if _depth > 0:
        return True
    try:
        return QApplication.activeModalWidget() is not None
    except Exception:
        return False


@contextmanager
def app_dialog_guard():
    global _depth
    _depth += 1
    if _depth == 1:
        hub.opened.emit()
    try:
        yield
    finally:
        _depth = max(0, _depth - 1)
        if _depth == 0:
            hub.closed.emit()
