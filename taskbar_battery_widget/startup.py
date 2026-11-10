"""Start-with-Windows via the per-user Run key."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TaskbarBatteryWidget"
LEGACY_VALUE_NAME = "BtBatteryBar"  # Run value of the previous app name (migration only)


def _project_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def build_command() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    interp = pyw if pyw.exists() else exe
    # Run entries start with an arbitrary cwd, so add the project root to sys.path explicitly (appended,
    # so it can never shadow the stdlib or site-packages)
    # (equivalent to `pythonw -m taskbar_battery_widget`). The root travels as argv[1], never inside the code
    # string, so quotes/spaces in the path cannot break the command line.
    code = ("import sys,runpy;sys.path.append(sys.argv[1]);"
            "runpy.run_module('taskbar_battery_widget',run_name='__main__')")
    return subprocess.list2cmdline([str(interp), "-c", code, _project_root()])


def is_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, build_command())
        else:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except FileNotFoundError:
                pass
        if enabled:
            try:
                winreg.DeleteValue(k, LEGACY_VALUE_NAME)
            except FileNotFoundError:
                pass


def legacy_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, LEGACY_VALUE_NAME)
        return True
    except OSError:
        return False


def migrate_legacy() -> None:
    """If the old-name Run value exists, rewrite it under the new name (old one removed)."""
    try:
        if legacy_enabled():
            set_enabled(True)
    except OSError:
        pass
