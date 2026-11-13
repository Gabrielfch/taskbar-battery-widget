"""JSON settings stored in %APPDATA%\\TaskbarBatteryWidget\\config.json (corrupt/missing => defaults)."""
from __future__ import annotations

import copy
import json
import os
import re
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
    "beta_readers": False,          # opt-in: readers for the least certain protocols (see README)
    "bluetooth_enabled": True,      # False: no Bluetooth scan at all
    "detail_log_enabled": False,    # per-device detailed CSV log (global switch)
    "detail_log_devices": [],       # visibility keys of the devices being logged
    "idle_grace_min": 30,           # minutes a silent (asleep) 2.4 GHz device stays listed
    "color_mode": "levels",         # "levels" | "mono"
    "hidden_devices": [],           # visibility keys (models.visibility_key) hidden from the UI
    "update_check_auto": False,     # opt-in: look for a new release at most once a day
    "update_last_check": 0,         # unix timestamp of the last automatic check (0 = never)
    "placement_mode": "taskbar",    # "taskbar" | "floating"
    "taskbar_anchor": "right",      # "right" | "left": side of the taskbar used in taskbar mode
    "placement_monitor": "primary", # "primary" or a monitor key (models-style key, see MONITOR_KEY_RE)
    "floating_positions": {},       # monitor key ("primary" or key) -> {"anchor", "dx", "dy"}
    "show_in_fullscreen": False,    # keep the widget visible over fullscreen windows
}

MAX_HIDDEN = 200
MAX_DETAIL_DEVICES = 50
MAX_TIMESTAMP = 4_102_444_800       # year 2100
COLOR_MODES = ("levels", "mono")
PLACEMENT_MODES = ("taskbar", "floating")
TASKBAR_ANCHORS = ("right", "left")
FLOATING_ANCHORS = ("top-left", "top-center", "top-right", "middle-left", "middle-right",
                    "bottom-left", "bottom-center", "bottom-right")
MAX_FLOATING = 16                   # entries in floating_positions
MAX_OFFSET = 10000                  # |dx| and |dy| are clamped to this
MONITOR_KEY_RE = re.compile(r"[0-9a-f#]{1,40}")


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


def _valid_monitor_key(value) -> bool:
    return isinstance(value, str) and (value == "primary" or MONITOR_KEY_RE.fullmatch(value) is not None)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _floating_entry(mon_key, pos):
    """Return a clean {"anchor", "dx", "dy"} (offsets clamped) or None if the entry is malformed."""
    if not _valid_monitor_key(mon_key) or not isinstance(pos, dict):
        return None
    anchor, dx, dy = pos.get("anchor"), pos.get("dx"), pos.get("dy")
    if anchor not in FLOATING_ANCHORS or not _is_int(dx) or not _is_int(dy):
        return None
    return {"anchor": anchor,
            "dx": max(-MAX_OFFSET, min(MAX_OFFSET, dx)),
            "dy": max(-MAX_OFFSET, min(MAX_OFFSET, dy))}


def _clean_floating(value) -> dict:
    """Keep the well-formed entries (first MAX_FLOATING of them); malformed ones are dropped."""
    out = {}
    for mon_key, pos in value.items():
        entry = _floating_entry(mon_key, pos)
        if entry is not None and len(out) < MAX_FLOATING:
            out[mon_key] = entry
    return out


def _copy(value):
    return copy.deepcopy(value) if isinstance(value, (list, dict)) else value


def _valid(key, value) -> bool:
    if key not in DEFAULTS:
        return False
    default = DEFAULTS[key]
    if key == "color_mode":
        return isinstance(value, str) and value in COLOR_MODES
    if key == "placement_mode":
        return isinstance(value, str) and value in PLACEMENT_MODES
    if key == "taskbar_anchor":
        return isinstance(value, str) and value in TASKBAR_ANCHORS
    if key == "placement_monitor":
        return _valid_monitor_key(value)
    if key == "floating_positions":
        return (isinstance(value, dict) and len(value) <= MAX_FLOATING
                and all(_floating_entry(k, v) is not None for k, v in value.items()))
    if key in ("hidden_devices", "detail_log_devices"):
        limit = MAX_HIDDEN if key == "hidden_devices" else MAX_DETAIL_DEVICES
        return (isinstance(value, list) and len(value) <= limit
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
        self._data = {k: _copy(v) for k, v in DEFAULTS.items()}
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        if isinstance(raw.get("detail_log_devices"), list):
            raw["detail_log_devices"] = raw["detail_log_devices"][:MAX_DETAIL_DEVICES]
        for key in DEFAULTS:
            if key == "floating_positions" and isinstance(raw.get(key), dict):
                self._data[key] = _clean_floating(raw[key])    # bad entries dropped, the rest kept
            elif key in raw and _valid(key, raw[key]):
                self._data[key] = raw[key]
        migrated_beta = False
        if "beta_readers" not in raw and raw.get("experimental_readers") is True:
            self._data["beta_readers"] = True
            migrated_beta = True
        if "experimental_readers" in raw:
            migrated_beta = True                # drop the obsolete key from the file
        for lkey in ("hidden_devices", "detail_log_devices"):
            items = self._data[lkey]
            if any(not is_visibility_key(v) for v in items):
                # migrate raw device ids (may contain MAC addresses) to hashed keys and scrub the file
                migrated = []
                for v in items:
                    k = v if is_visibility_key(v) else visibility_key(v)
                    if k not in migrated:
                        migrated.append(k)
                self._data[lkey] = migrated
                migrated_beta = True
        if migrated_beta:
            self.save()

    def get(self, key):
        return _copy(self._data.get(key, DEFAULTS.get(key)))

    def set(self, key, value):
        if key not in DEFAULTS:
            raise ValueError(f"unknown config key: {key!r}")
        if key == "detail_log_devices" and isinstance(value, list):
            value = value[:MAX_DETAIL_DEVICES]      # over the limit: truncate instead of raising into the UI
        if not _valid(key, value):
            raise ValueError(f"invalid value for {key!r}: {value!r}")
        if key == "floating_positions":
            value = _clean_floating(value)              # clamps dx/dy
        self._data[key] = _copy(value)
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
