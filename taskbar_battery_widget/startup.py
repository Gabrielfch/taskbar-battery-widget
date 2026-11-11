"""Start-with-Windows via the per-user Run key."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager / Settings > Startup apps keep the user's on/off choice here (value name = Run value name).
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
APPROVED_ENABLED = bytes([0x02] + [0] * 11)
VALUE_NAME = "TaskbarBatteryWidget"
LEGACY_VALUE_NAME = "BtBatteryBar"  # Run value of the previous app name (migration only)


def _project_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def build_command() -> str:
    if getattr(sys, "frozen", False):
        # Installer (onedir) and portable (onefile) builds: in PyInstaller onefile, sys.executable is the real
        # path of the launched .exe (not the temp extraction dir), so it is stable across logons.
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


def registered_command() -> str | None:
    """Raw command stored in the Run value, or None when absent/unreadable."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return value if isinstance(value, str) else None
    except OSError:
        return None


def _normalize(cmd: str) -> str:
    return cmd.strip().strip('"').strip().casefold()


def _points_here(cur: str | None) -> bool:
    return cur is not None and _normalize(cur) == _normalize(build_command())


def approved_disabled() -> bool:
    """True when the user turned the entry off in Task Manager / Settings (StartupApproved first byte odd)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return isinstance(value, (bytes, bytearray)) and len(value) > 0 and bool(value[0] & 1)
    except OSError:
        return False


def is_enabled() -> bool:
    """True only if the Run value exists, launches this very copy AND is not disabled in StartupApproved."""
    return _points_here(registered_command()) and not approved_disabled()


def sync(want: bool) -> bool:
    """Make the Run entry match the desired state for this copy. Never raises; returns is_enabled()."""
    try:
        mine = _points_here(registered_command())
        if want and not mine:
            set_enabled(True)
        elif not want and mine:
            set_enabled(False)
        # not want and pointing to another copy: leave it alone
    except Exception:
        pass
    try:
        return is_enabled()
    except Exception:
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
    if enabled and approved_disabled():
        # the user turns it on again explicitly: clear the "disabled" flag Windows keeps separately
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_BINARY, APPROVED_ENABLED)


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
