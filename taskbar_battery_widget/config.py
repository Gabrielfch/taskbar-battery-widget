"""JSON settings stored in %APPDATA%\\TaskbarBatteryWidget\\config.json (corrupt/missing => defaults)."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .custom_icons import safe_name as _safe_icon_name
from .i18n import LANGUAGES as _i18n_languages
from .models import KINDS, is_visibility_key, visibility_key
from .naming import sanitize_name

DEFAULTS = {
    "low_threshold": 20,
    "notifications_enabled": True,
    "notification_cooldown_min": 60,
    "full_charge_alert": True,      # notify once when a device finishes charging
    "show_disconnected": False,
    "show_when_empty": False,
    "start_with_windows": False,
    "wireless_enabled": True,
    "dualsense_bt_extended": True,  # allow the one-off feature read that enables DualSense BT battery
    "beta_readers": False,          # opt-in: readers for the least certain protocols (see README)
    "razer_dongle_wake": False,     # opt-in: send Razer's own volatile activation frame (see reader-contract)
    "bluetooth_enabled": True,      # False: no Bluetooth scan at all
    "detail_log_enabled": False,    # per-device detailed CSV log (global switch)
    "detail_log_devices": [],       # visibility keys of the devices being logged
    "idle_grace_min": 30,           # minutes a silent (asleep) 2.4 GHz device stays listed
    "color_mode": "levels",         # "levels" | "mono"
    "color_palette": "classic",     # one of PALETTES ("mono" mirrors color_mode == "mono")
    "custom_colors": {"high": "#6EDC78", "medium": "#F5C842", "low": "#FF9999"},
    "icon_style": "outline",        # one of ICON_STYLES
    "device_aliases": {},           # visibility key -> user-chosen display name (see aliases.py)
    "custom_icons_kind": {},        # device kind -> imported image file name (see custom_icons.py)
    "custom_icons_device": {},      # visibility key -> imported image file name
    "animations": "system",         # "always" | "system" (follow Windows) | "off": animated icons and the pulses
    "app_theme": "auto",            # "auto" (follow Windows) | "light" | "dark": look of the panel and the app windows
    "stack_devices": False,         # floating at a corner: one device per row, in a column
    "pill_style": "transparent",   # "classic" | "soft" | "transparent" | "compact": look of the taskbar pills
    "hidden_devices": [],           # visibility keys (models.visibility_key) hidden from the UI
    "device_order": [],             # visibility keys in the order chosen by the user ([] = monitor order)
    "update_check_auto": False,     # opt-in: look for a new release at most once a day
    "update_last_check": 0,         # unix timestamp of the last automatic check (0 = never)
    "placement_mode": "taskbar",    # "taskbar" | "floating"
    "taskbar_anchor": "right",      # "right" | "left": side of the taskbar used in taskbar mode
    "placement_monitor": "primary", # "primary" or a monitor key (models-style key, see MONITOR_KEY_RE)
    "floating_positions": {},       # monitor key ("primary" or key) -> {"anchor", "dx", "dy"}
    "show_in_fullscreen": False,    # keep the widget visible over fullscreen windows
    "fullscreen_placement": "same", # "same" | "floating" | one of FLOATING_ANCHORS: spot used over a fullscreen app
    "fullscreen_floating_positions": {},    # like floating_positions, used when fullscreen_placement is "floating"
    "panel_size": "normal",         # one of PANEL_SIZES: height of the device panel (share of the screen)
    "ui_scale": 100,                # one of UI_SCALES (percent): fonts, rows, buttons and pills
    "language": "auto",             # one of LANGUAGES: "auto" follows the system language
    "last_seen_version": "",        # "X.Y.Z" of the last version whose news were handled ("" = never)
    "tutorial_seen": False,         # the first-run tutorial was shown (finished or skipped)
    "razer_wake_hint_shown": False, # the one-time "Razer dongle not answering" notice was shown
}

MAX_HIDDEN = 200
MAX_ALIASES = 200
MAX_CUSTOM_ICONS = 200
MAX_ALIAS_LEN = 48
MAX_DETAIL_DEVICES = 50
MAX_TIMESTAMP = 4_102_444_800       # year 2100
COLOR_MODES = ("levels", "mono")
COLOR_PALETTES = ("classic", "colorblind", "pastel", "high_contrast", "mono", "custom")
ICON_STYLES = ("outline", "filled", "simple", "retro", "battery", "none")
CUSTOM_COLOR_KEYS = ("high", "medium", "low")
HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
PANEL_SIZES = ("compact", "normal", "large")
ANIMATION_MODES = ("always", "system", "off")
APP_THEMES = ("auto", "light", "dark")
PILL_STYLES = ("classic", "soft", "transparent", "compact")
PANEL_SIZE_FRACTION = {"compact": 0.40, "normal": 0.50, "large": 0.65}   # of the screen's available height
UI_SCALES = (90, 100, 115, 130)
LANGUAGES = ("auto",) + tuple(_i18n_languages)   # "auto" + every language registered in i18n.LANGUAGES
PLACEMENT_MODES = ("taskbar", "floating")
TASKBAR_ANCHORS = ("right", "left")
FLOATING_ANCHORS = ("top-left", "top-center", "top-right", "middle-left", "middle-right",
                    "bottom-left", "bottom-center", "bottom-right")
FULLSCREEN_PLACEMENTS = ("same", "floating") + FLOATING_ANCHORS
MAX_FLOATING = 16                   # entries in floating_positions
MAX_OFFSET = 10000                  # |dx| and |dy| are clamped to this
MONITOR_KEY_RE = re.compile(r"[0-9a-f#]{1,40}")
VERSION_RE = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")


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
    if key == "color_palette":
        return isinstance(value, str) and value in COLOR_PALETTES
    if key == "icon_style":
        return isinstance(value, str) and value in ICON_STYLES
    if key == "custom_colors":
        return (isinstance(value, dict) and set(value) == set(CUSTOM_COLOR_KEYS)
                and all(isinstance(v, str) and HEX_COLOR_RE.fullmatch(v) for v in value.values()))
    if key == "panel_size":
        return isinstance(value, str) and value in PANEL_SIZES
    if key == "animations":
        return isinstance(value, str) and value in ANIMATION_MODES
    if key == "app_theme":
        return isinstance(value, str) and value in APP_THEMES
    if key == "pill_style":
        return isinstance(value, str) and value in PILL_STYLES
    if key == "language":
        return isinstance(value, str) and value in LANGUAGES
    if key == "last_seen_version":
        return isinstance(value, str) and (value == "" or VERSION_RE.fullmatch(value) is not None)
    if key == "ui_scale":
        return _is_int(value) and value in UI_SCALES
    if key == "placement_mode":
        return isinstance(value, str) and value in PLACEMENT_MODES
    if key == "taskbar_anchor":
        return isinstance(value, str) and value in TASKBAR_ANCHORS
    if key == "placement_monitor":
        return _valid_monitor_key(value)
    if key == "fullscreen_placement":
        return isinstance(value, str) and value in FULLSCREEN_PLACEMENTS
    if key in ("floating_positions", "fullscreen_floating_positions"):
        return (isinstance(value, dict) and len(value) <= MAX_FLOATING
                and all(_floating_entry(k, v) is not None for k, v in value.items()))
    if key == "device_aliases":
        return (isinstance(value, dict) and len(value) <= MAX_ALIASES
                and all(is_visibility_key(k) and isinstance(v, str) and 0 < len(v) <= MAX_ALIAS_LEN
                        and v == sanitize_name(v, MAX_ALIAS_LEN) for k, v in value.items()))
    if key in ("custom_icons_kind", "custom_icons_device"):
        ok_key = (lambda k: k in KINDS) if key == "custom_icons_kind" else is_visibility_key
        return (isinstance(value, dict) and len(value) <= MAX_CUSTOM_ICONS
                and all(isinstance(k, str) and ok_key(k) and _safe_icon_name(v) for k, v in value.items()))
    if key in ("hidden_devices", "detail_log_devices", "device_order"):
        limit = MAX_DETAIL_DEVICES if key == "detail_log_devices" else MAX_HIDDEN
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


def clean_aliases(value) -> dict:
    """Sanitised copy of a device_aliases value: bad keys/values dropped, names cleaned (no control
    or markup characters, at most MAX_ALIAS_LEN chars), at most MAX_ALIASES entries kept."""
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for k, v in value.items():
        if len(out) >= MAX_ALIASES:
            break
        if not is_visibility_key(k) or not isinstance(v, str):
            continue
        alias = sanitize_name(v, MAX_ALIAS_LEN)
        if alias:
            out[k] = alias
    return out


def clean_custom_icons(value, by_kind: bool) -> dict:
    """Sanitised copy of custom_icons_kind / custom_icons_device: bad keys/file names dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for k, v in value.items():
        if len(out) >= MAX_CUSTOM_ICONS:
            break
        if not isinstance(k, str) or not (k in KINDS if by_kind else is_visibility_key(k)):
            continue
        if _safe_icon_name(v):
            out[k] = v
    return out


class Config:
    def __init__(self, path=None):
        if path:
            self.path = Path(path)
        else:
            self.path = default_path()
            migrate_legacy_config(self.path)
        self.is_new = not self.path.exists()     # no settings file yet: first run of the app
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
            if key == "device_aliases":
                self._data[key] = clean_aliases(raw.get(key))
            elif key in ("custom_icons_kind", "custom_icons_device"):
                self._data[key] = clean_custom_icons(raw.get(key), key == "custom_icons_kind")
            elif key in ("floating_positions", "fullscreen_floating_positions") and isinstance(raw.get(key), dict):
                self._data[key] = _clean_floating(raw[key])    # bad entries dropped, the rest kept
            elif key in raw and _valid(key, raw[key]):
                self._data[key] = raw[key]
        migrated_beta = False
        if not _valid("color_palette", raw.get("color_palette")) and self._data["color_mode"] == "mono":
            self._data["color_palette"] = "mono"        # pre-palette configs
            migrated_beta = True
        if self._data["color_palette"] != "mono" and self._data["color_mode"] == "mono":
            self._data["color_mode"] = "levels"         # keep both keys consistent
        elif self._data["color_palette"] == "mono":
            self._data["color_mode"] = "mono"
        if "beta_readers" not in raw and raw.get("experimental_readers") is True:
            self._data["beta_readers"] = True
            migrated_beta = True
        if "animations" not in raw and isinstance(raw.get("animations_enabled"), bool):
            self._data["animations"] = "system" if raw["animations_enabled"] else "off"
            migrated_beta = True
        if "animations_enabled" in raw:
            migrated_beta = True                # drop the obsolete key from the file
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
        if key == "device_aliases" and isinstance(value, dict) and len(value) <= MAX_ALIASES:
            value = clean_aliases(value)
        if key in ("custom_icons_kind", "custom_icons_device") and isinstance(value, dict) \
                and len(value) <= MAX_CUSTOM_ICONS:
            value = clean_custom_icons(value, key == "custom_icons_kind")
        if not _valid(key, value):
            if key in ("device_aliases", "custom_icons_kind", "custom_icons_device"):
                raise ValueError(f"invalid value for {key!r}")      # never echo personal data
            raise ValueError(f"invalid value for {key!r}: {value!r}")
        if key in ("floating_positions", "fullscreen_floating_positions"):
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
