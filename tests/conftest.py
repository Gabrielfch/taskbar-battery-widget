import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """Reset module-level registries so a deliberately hung thread or a cache entry from one test
    cannot leak into the next (e.g. a 'logitech' call still in-flight makes later scans skip it)."""
    from taskbar_battery_widget import hid_win, hidpp, razer, wireless

    def _reset():
        wireless._inflight.clear()
        hidpp._group_inflight.clear()
        hidpp._info_cache.clear()
        hid_win._info_cache.clear()
        getattr(razer, "_last", {}).clear()
        wireless.reset_cache()

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _no_real_wireless_scan(monkeypatch):
    """Never touch real HID dongles from unit tests."""
    from taskbar_battery_widget import wireless
    monkeypatch.setattr(wireless, "scan_wireless", lambda: [])

