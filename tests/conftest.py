import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Never let unit tests write into the user's real profile (config.json, app.log, devices.log,
# last_known.json): point %APPDATA% / %LOCALAPPDATA% at a throwaway folder for the whole session.
_TEST_PROFILE = tempfile.mkdtemp(prefix="tbw-tests-")
os.environ["APPDATA"] = os.path.join(_TEST_PROFILE, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(_TEST_PROFILE, "Local")
os.makedirs(os.environ["APPDATA"], exist_ok=True)
os.makedirs(os.environ["LOCALAPPDATA"], exist_ok=True)

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

