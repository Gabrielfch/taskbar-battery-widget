"""Regression: a native file dialog (not a QWindow) makes Qt report 'no focus window / inactive app'.
The device panel used to hide then, and the dialog (a child of the panel) vanished on click."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import dialogs, i18n
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model

KEY = visibility_key("dev-1")


@pytest.fixture
def panel(qapp, monkeypatch):
    i18n.set_language("en")
    p = dp.DevicePanel(dark=True)
    p.set_model(build_panel_model([Device(id="dev-1", name="Mouse", kind="mouse", battery=50, connected=True)],
                                  (), True, 20, True, lang="en"))
    p.show_at(QPoint(300, 300))
    p._armed = True
    yield p
    p.close()
    p.deleteLater()


def inactive(monkeypatch, on=True):
    st = Qt.ApplicationState.ApplicationInactive if on else Qt.ApplicationState.ApplicationActive
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: st))


def simulate_native_dialog_focus_loss(p):
    p._on_focus_window_changed(None)
    p._on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
    QTest.qWait(dp.HIDE_CHECK_MS + 80)


def test_panel_survives_native_dialog_and_editing_continues(panel, monkeypatch):
    panel._buttons[(KEY, "alias")].click()
    assert panel._edit is not None
    with dialogs.app_dialog_guard():
        inactive(monkeypatch)
        simulate_native_dialog_focus_loss(panel)
        assert panel.isVisible()
        assert panel._edit is not None
    # dialog closed while the app is still inactive: now it hides
    QTest.qWait(dp.HIDE_CHECK_MS + 80)
    assert not panel.isVisible()


def test_panel_recovers_when_app_active_after_dialog(panel, monkeypatch):
    tops = []
    monkeypatch.setattr(panel, "_set_topmost", lambda on: tops.append(on))
    with dialogs.app_dialog_guard():
        assert tops == [False]                      # lowered so the dialog is drawn in front
        inactive(monkeypatch)
        simulate_native_dialog_focus_loss(panel)
        inactive(monkeypatch, False)
    QTest.qWait(dp.HIDE_CHECK_MS + 80)
    assert panel.isVisible() and tops[-1] is True


def test_panel_survives_qt_modal_dialog_without_explicit_guard(panel, monkeypatch):
    dlg = QDialog()
    dlg.setModal(True)
    dlg.show()
    try:
        assert dialogs.dialog_open()
        inactive(monkeypatch)
        simulate_native_dialog_focus_loss(panel)
        assert panel.isVisible()
    finally:
        dlg.close()
    assert not dialogs.dialog_open()


def test_guard_nesting_and_exception_safety():
    assert not dialogs.dialog_open()
    with pytest.raises(RuntimeError):
        with dialogs.app_dialog_guard():
            with dialogs.app_dialog_guard():
                assert dialogs.dialog_open()
            assert dialogs.dialog_open()
            raise RuntimeError
    assert not dialogs.dialog_open()


def test_without_guard_panel_hides(panel, monkeypatch):
    inactive(monkeypatch)
    simulate_native_dialog_focus_loss(panel)
    assert not panel.isVisible()
