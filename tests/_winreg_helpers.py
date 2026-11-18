"""One fake `winreg` module shared by the taskbar registry tests."""
import sys
import types


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_winreg(monkeypatch, value=None, missing=False, open_fails=False, name=None):
    """Install a fake `winreg`: QueryValueEx returns (value, 4), raises FileNotFoundError when `missing`,
    OpenKey raises OSError when `open_fails`; `name`, when given, is the only value name allowed."""
    def open_key(root, path, *a):
        if open_fails:
            raise OSError("denied")
        return _Key()

    def query(k, value_name):
        if name is not None:
            assert value_name == name
        if missing:
            raise FileNotFoundError
        return (value, 4)

    mod = types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=open_key, QueryValueEx=query)
    monkeypatch.setitem(sys.modules, "winreg", mod)
