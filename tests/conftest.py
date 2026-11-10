import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_real_wireless_scan(monkeypatch):
    """Never touch real HID dongles from unit tests."""
    from taskbar_battery_widget import wireless
    monkeypatch.setattr(wireless, "scan_wireless", lambda: [])

