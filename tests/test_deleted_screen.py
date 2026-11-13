"""A QScreen destroyed while the widget reads it (monitor unplug, RDP, headless CI) must be skipped."""
from taskbar_battery_widget import widget as w


def test_device_names_skips_destroyed_screen(qapp, monkeypatch):
    real = qapp.primaryScreen()
    monkeypatch.setattr(w.shiboken6, "isValid", lambda obj: obj is real)
    gone = object()     # stands in for a deleted QScreen wrapper: any attribute access would fail
    assert w._device_names([gone, real]) == ["", w._device_names([real])[0]]


def test_screen_infos_ignores_destroyed_screens(qapp, monkeypatch):
    monkeypatch.setattr(w.shiboken6, "isValid", lambda obj: False)
    assert w.screen_infos() == []


def test_screen_infos_retries_after_runtime_error(qapp, monkeypatch):
    calls = []
    real_keys = w.screen_keys

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("libshiboken: Internal C++ object (PySide6.QtGui.QScreen) already deleted.")
        return real_keys()
    monkeypatch.setattr(w, "screen_keys", flaky)
    infos = w.screen_infos()
    assert len(calls) == 2 and len(infos) == len(qapp.screens())
