"""JSON settings stored in %APPDATA%\\TaskbarBatteryWidget\\config.json (corrupt/missing => defaults)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .models import is_visibility_key, visibility_key

DEFAULTS = {
    "low_threshold": 20,
    "notifications_enabled": True,
    "notification_cooldown_min": 60,
    "show_disconnected": False,
    "show_when_empty": False,
    "start_with_windows": False,
    "wireless_enabled": True,
    "dualsense_bt_extended": True,  # allow the one-off feature read that enables DualSense BT battery
    "experimental_readers": False,  # opt-in: unverified vendor readers (headsets, Sony, Xbox, ...)
    "idle_grace_min": 30,           # minutes a silent (asleep) 2.4 GHz device stays listed
    "color_mode": "levels",         # "levels" | "mono"
    "hidden_devices": [],           # visibility keys (models.visibility_key) hidden from the UI
    "update_check_auto": False,     # opt-in: look for a new release at most once a day
    "update_last_check": 0,         # unix timestamp of the last automatic check (0 = never)
}

MAX_HIDDEN = 200
MAX_TIMESTAMP = 4_102_444_800       # year 2100
COLOR_MODES = ("levels", "mono")


LEGACY_DIR = "BtBatteryBar"  # settings folder of the previous app name (migration only)


def _appdata() -> str:
    return os.environ.get("APPDATA") or str(Path.home())


def default_path() -> Path:
    return Path(_appdata()) / "TaskbarBatteryWidget" / "config.json"


def migrate_legacy_config(new_path: Path, legacy_path: Path | None = None) -> bool:
    """Copy the old-name config over if the new one is missing (the old file is kept)."""
    legacy = legacy_path or Path(_appdata()) / LEGACY_DIR / "config.json"
    try:
        if new_path.exists() or not legacy.is_file():
            return False
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy, new_path)
        return True
    except OSError:
        return False


def _valid(key, value) -> bool:
    if key not in DEFAULTS:
        return False
    default = DEFAULTS[key]
    if key == "color_mode":
        return isinstance(value, str) and value in COLOR_MODES
    if key == "hidden_devices":
        return (isinstance(value, list) and len(value) <= MAX_HIDDEN
                and all(isinstance(v, str) and 0 < len(v) <= 200 for v in value))
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    if key == "low_threshold":
        return 1 <= value <= 100
    if key == "idle_grace_min":
        return 1 <= value <= 1440
    if key == "update_last_check":
        return 0 <= value <= MAX_TIMESTAMP
    if key == "notification_cooldown_min":
        return value >= 1
    return value >= 0


class Config:
    def __init__(self, path=None):
        if path:
            self.path = Path(path)
        else:
            self.path = default_path()
            migrate_legacy_config(self.path)
        self._data = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key in DEFAULTS:
            if key in raw and _valid(key, raw[key]):
                self._data[key] = raw[key]
        hidden = self._data["hidden_devices"]
        if any(not is_visibility_key(v) for v in hidden):
            # migrate raw device ids (may contain MAC addresses) to hashed keys and scrub the file
            migrated = []
            for v in hidden:
                k = v if is_visibility_key(v) else visibility_key(v)
                if k not in migrated:
                    migrated.append(k)
            self._data["hidden_devices"] = migrated
            self.save()

    def get(self, key):
        v = self._data.get(key, DEFAULTS.get(key))
        return list(v) if isinstance(v, list) else v

    def set(self, key, value):
        if key not in DEFAULTS:
            raise ValueError(f"unknown config key: {key!r}")
        if not _valid(key, value):
            raise ValueError(f"invalid value for {key!r}: {value!r}")
        self._data[key] = list(value) if isinstance(value, list) else value
        self.save()

    def save(self):
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=self.path.stem + ".", suffix=".tmp",
                                       dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self._data, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
