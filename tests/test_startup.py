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


# ------------------------------------------------------------ copy-awareness / sync
def _fake(monkeypatch, stored=None):
    fake = FakeReg()
    if stored is not None:
        fake.values[startup.VALUE_NAME] = stored
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(startup, "build_command", lambda: '"C:\\Inst\\app.exe"')
    return fake


def test_value_pointing_elsewhere_is_not_enabled_and_sync_rewrites(monkeypatch):
    fake = _fake(monkeypatch, "pythonw.exe -c dev")
    assert startup.registered_command() == "pythonw.exe -c dev"
    assert startup.is_enabled() is False
    assert startup.sync(True) is True
    assert fake.values[startup.VALUE_NAME] == '"C:\\Inst\\app.exe"'


def test_sync_true_adds_missing_value(monkeypatch):
    fake = _fake(monkeypatch)
    assert startup.registered_command() is None
    assert startup.sync(True) is True
    assert fake.values[startup.VALUE_NAME] == '"C:\\Inst\\app.exe"'


def test_sync_false_removes_only_own_value(monkeypatch):
    fake = _fake(monkeypatch, '"C:\\Inst\\app.exe"')
    assert startup.sync(False) is False
    assert startup.VALUE_NAME not in fake.values
    fake = _fake(monkeypatch, "other copy")
    assert startup.sync(False) is False
    assert fake.values[startup.VALUE_NAME] == "other copy"


def test_quote_case_whitespace_normalization(monkeypatch):
    fake = _fake(monkeypatch, '  c:\\inst\\APP.exe ')
    assert startup.is_enabled() is True
    startup.sync(True)
    assert fake.values[startup.VALUE_NAME] == '  c:\\inst\\APP.exe '   # untouched


def test_sync_never_raises(monkeypatch):
    _fake(monkeypatch)

    def fail(enabled):
        raise PermissionError()

    monkeypatch.setattr(startup, "set_enabled", fail)
    assert startup.sync(True) is False


def test_app_start_calls_sync(monkeypatch, tmp_path, qapp):
    import tempfile
    from taskbar_battery_widget import alerts, app as app_mod, config

    class Stop(Exception):
        pass

    calls = []
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(startup, "migrate_legacy", lambda: None)
    monkeypatch.setattr(startup, "is_enabled", lambda: False)
    monkeypatch.setattr(startup, "sync", lambda want: calls.append(want) or want)

    class Cfg:
        def get(self, k):
            return True

    monkeypatch.setattr(config, "Config", Cfg)

    def boom(cfg):
        raise Stop

    monkeypatch.setattr(alerts, "AlertManager", boom)
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        app_mod.main()
    except Stop:
        pass
    assert calls == [True]


def test_app_start_from_source_does_not_sync(monkeypatch, tmp_path, qapp):
    """A source checkout must not re-point the Run value of the installed app."""
    import sys
    import tempfile
    from taskbar_battery_widget import alerts, app as app_mod, config

    class Stop(Exception):
        pass

    calls = []
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(startup, "migrate_legacy", lambda: None)
    monkeypatch.setattr(startup, "is_enabled", lambda: False)
    monkeypatch.setattr(startup, "sync", lambda want: calls.append(want) or want)

    class Cfg:
        def get(self, k):
            return True

    monkeypatch.setattr(config, "Config", Cfg)
    monkeypatch.setattr(alerts, "AlertManager", lambda cfg: (_ for _ in ()).throw(Stop()))
    try:
        app_mod.main()
    except Stop:
        pass
    assert calls == []
