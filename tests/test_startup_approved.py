"""StartupApproved (Task Manager / Settings toggle) handling, with a simulated registry module."""
import sys

from taskbar_battery_widget import startup

DISABLED = bytes([3] + [0] * 11)
ENABLED = bytes([2] + [0] * 11)


class KeyedReg:
    """winreg stand-in keeping values per key path (the real registry is never touched)."""
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self):
        self.keys = {}

    def OpenKey(self, root, path, *a):
        if path not in self.keys:
            raise FileNotFoundError(path)

        class K:
            def __enter__(s):
                return path

            def __exit__(s, *e):
                return False
        return K()

    def QueryValueEx(self, path, name):
        vals = self.keys[path]
        if name not in vals:
            raise FileNotFoundError(name)
        return vals[name], 1

    def SetValueEx(self, path, name, _r, typ, value):
        self.keys[path][name] = value

    def DeleteValue(self, path, name):
        if name not in self.keys[path]:
            raise FileNotFoundError(name)
        del self.keys[path][name]


def _keyed(monkeypatch, approved=None):
    fake = KeyedReg()
    fake.keys[startup.RUN_KEY] = {startup.VALUE_NAME: '"C:\\Inst\\app.exe"'}
    if approved is not None:
        fake.keys[startup.APPROVED_KEY] = {startup.VALUE_NAME: approved}
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(startup, "build_command", lambda: '"C:\\Inst\\app.exe"')
    return fake


def test_disabled_in_startup_approved_is_not_enabled(monkeypatch):
    _keyed(monkeypatch, DISABLED)
    assert startup.approved_disabled() is True
    assert startup.is_enabled() is False


def test_enabled_bytes_and_missing_approved_key_count_as_enabled(monkeypatch):
    _keyed(monkeypatch, ENABLED)
    assert startup.is_enabled() is True
    _keyed(monkeypatch)                                     # no StartupApproved key at all
    assert startup.approved_disabled() is False and startup.is_enabled() is True


def test_set_enabled_rewrites_disabled_flag(monkeypatch):
    fake = _keyed(monkeypatch, DISABLED)
    startup.set_enabled(True)
    assert fake.keys[startup.APPROVED_KEY][startup.VALUE_NAME] == ENABLED
    assert startup.is_enabled() is True


def test_sync_does_not_override_task_manager_disable(monkeypatch):
    fake = _keyed(monkeypatch, DISABLED)
    assert startup.sync(True) is False
    assert fake.keys[startup.APPROVED_KEY][startup.VALUE_NAME][0] == 3
