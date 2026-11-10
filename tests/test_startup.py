import sys

from taskbar_battery_widget import startup


def _argv_roundtrip(cmd):
    """Parse a command line with the Windows CommandLineToArgvW rules."""
    import ctypes
    from ctypes import wintypes as wt

    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    n = ctypes.c_int(0)
    ptr = shell32.CommandLineToArgvW(cmd, ctypes.byref(n))
    try:
        return [ptr[i] for i in range(n.value)]
    finally:
        ctypes.WinDLL("kernel32").LocalFree(ptr)


def test_build_command_root_with_quote_and_spaces(monkeypatch):
    root = "C:\\My Apps\\it's here\\proj"
    monkeypatch.setattr(startup, "_project_root", lambda: root)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = startup.build_command()
    argv = _argv_roundtrip(cmd)
    assert argv[1] == "-c"
    assert "sys.argv[1]" in argv[2] and root not in argv[2]
    assert "sys.path.append(sys.argv[1])" in argv[2] and "insert" not in argv[2]
    assert argv[-1] == root
    assert "runpy.run_module('taskbar_battery_widget',run_name='__main__')" in argv[2]
    assert len(argv) == 4


def test_build_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\Program Files\\Bt\\bt.exe")
    assert startup.build_command() == '"C:\\Program Files\\Bt\\bt.exe"'


class FakeReg:
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, *a):
        class K:
            def __enter__(s):
                return s

            def __exit__(s, *e):
                return False
        return K()

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, k, name, _r, _t, value):
        self.values[name] = value

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_enable_disable_with_mocked_registry(monkeypatch):
    fake = FakeReg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(startup, "build_command", lambda: "CMD")
    assert startup.is_enabled() is False
    startup.set_enabled(True)
    assert fake.values == {startup.VALUE_NAME: "CMD"}
    assert startup.is_enabled() is True
    startup.set_enabled(False)
    startup.set_enabled(False)   # deleting a missing value is fine
    assert startup.is_enabled() is False


def test_migrate_legacy_value_with_mocked_registry(monkeypatch):
    fake = FakeReg()
    fake.values[startup.LEGACY_VALUE_NAME] = "OLD"
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(startup, "build_command", lambda: "CMD")
    startup.migrate_legacy()
    assert fake.values == {startup.VALUE_NAME: "CMD"}


def test_migrate_legacy_noop_without_old_value(monkeypatch):
    fake = FakeReg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    startup.migrate_legacy()
    assert fake.values == {}
