"""Per-device event log and last-known battery values.

* ``devices.log`` (rotating text, one line per event) in ``%LOCALAPPDATA%\\TaskbarBatteryWidget\\logs``.
* ``last_known.json`` in ``%LOCALAPPDATA%\\TaskbarBatteryWidget``: the last real reading of every device,
  so a device that is gone (or reports no level) can still be shown dimmed with its last value.

Nothing here stores a Bluetooth address, a serial number or a HID path: devices are identified by
``models.visibility_key`` (a hash) plus their sanitized name.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import math
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from .models import KINDS, Device, is_visibility_key, visibility_key
from .naming import sanitize_name

log = logging.getLogger(__name__)

APP_DIR = "TaskbarBatteryWidget"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
BATTERY_MIN_INTERVAL_S = 300        # at most one 'battery' line per device every 5 min
LOW_REARM_MARGIN = 2                # hysteresis so 20 <-> 21 % does not spam 'low'
MAX_ENTRIES = 200
MAX_AGE_S = 90 * 86400
SAVE_INTERVAL_S = 600               # throttle for timestamp-only changes (keeps disk I/O off the UI thread)
SAVE_RETRY_S = 60                   # minimum wait after a failed save
WARN_INTERVAL_S = 600               # at most one repeated warning per kind every 10 min
MAX_FILE_BYTES = 1_000_000          # last_known.json larger than this is ignored
DAY_S = 86400.0
LINKS = ("bluetooth", "2.4ghz", "usb")

_MAC_SEP = re.compile(r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}")
_MAC_DOT = re.compile(r"[0-9A-Fa-f]{4}(?:\.[0-9A-Fa-f]{4}){2}")
_MAC_PLAIN = re.compile(r"[0-9A-Fa-f]{12,}")
_HID_PATH = re.compile(r"\\\\[?.]\\\S+|\b(?:BTHENUM|BTHLE|HID|USB|SWD)(?:\\|#)\S+", re.IGNORECASE)
_USER_PATH = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\s\"']+", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Remove MAC addresses, device/HID paths and user names in paths from free text.

    The markers contain no '<', '>' or '&' so `sanitize_name` leaves them alone (stable on reload).
    """
    s = str(text)
    s = _USER_PATH.sub(lambda m: m.group(1) + "[user]", s)
    s = _HID_PATH.sub("[hid-path]", s)
    s = _MAC_SEP.sub("[mac]", s)
    s = _MAC_DOT.sub("[mac]", s)
    return _MAC_PLAIN.sub("[mac]", s)


def base_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / APP_DIR


def logs_dir(base: Path | None = None) -> Path:
    return (Path(base) if base else base_dir()) / "logs"


def device_log_path(base: Path | None = None) -> Path:
    return logs_dir(base) / "devices.log"


def last_known_path(base: Path | None = None) -> Path:
    return (Path(base) if base else base_dir()) / "last_known.json"


def make_file_handler(path: Path) -> logging.Handler:
    """UTF-8 rotating handler (LOG_MAX_BYTES x LOG_BACKUPS); the file is created on first write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True)


def format_ago(seconds: float) -> str:
    """'N min ago' / 'N h ago' / 'N days ago' (localized)."""
    from .i18n import tr
    minutes = max(1, round(max(0.0, seconds) / 60))
    if minutes < 60:
        return tr("ago_min", n=minutes)
    hours = round(minutes / 60)
    if hours < 24:
        return tr("ago_h", n=hours)
    days = round(hours / 24)
    return tr("ago_d" if days == 1 else "ago_ds", n=days)


def _clean_name(name: str) -> str:
    # redact BEFORE truncating (a cut MAC would no longer match), and once more after for safety
    return redact_text(sanitize_name(redact_text(name)) or "?").replace("|", "/")


def _valid_ts(x, now: float) -> bool:
    return (not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)
            and 0 < x <= now + DAY_S)


def _valid_entry(v, now: float | None = None) -> dict | None:
    if not isinstance(v, dict):
        return None
    now = time.time() if now is None else now
    try:
        bat = v.get("battery")
        if bat is not None:
            if isinstance(bat, bool) or not isinstance(bat, int) or not 0 <= bat <= 100:
                return None
        ts, disc, seen = v.get("ts"), v.get("disc"), v.get("seen")
        for x in (ts, disc, seen):
            if x is not None and not _valid_ts(x, now):
                return None
        link = v.get("link")
        kind = v.get("kind")
        return {
            "name": _clean_name(v.get("name") if isinstance(v.get("name"), str) else "?"),
            "kind": kind if kind in KINDS else "generic",
            "link": link if link in LINKS else "bluetooth",
            "battery": bat,
            "charging": bool(v.get("charging", False)),
            "ts": float(ts) if ts is not None else None,
            "disc": float(disc) if disc is not None else None,
            "seen": float(seen) if seen is not None else (float(ts) if ts is not None else 0.0),
        }
    except Exception:                       # hostile file: never let it stop the app
        return None


class DeviceHistory:
    def __init__(self, base: Path | None = None, load: bool = True):
        self._base = Path(base) if base else base_dir()
        self._known: dict[str, dict] = {}
        self._rt: dict[str, dict] = {}          # per-session state (never persisted)
        self._pkeys: frozenset = frozenset()    # keys listed in the current update
        self._warned: dict[str, float] = {}
        self._dirty = False                     # a field that matters changed
        self._ts_dirty = False                  # only timestamps moved
        self._last_save = 0.0
        self._save_failed = False
        self._logger: logging.Logger | None = None
        self._handler: logging.Handler | None = None
        if load:
            try:
                self._load()
            except Exception:
                self._known = {}
                log.warning("could not load last_known.json; starting empty")

    # ------------------------------------------------------------------ persistence
    def _warn_ok(self, tag: str, now: float) -> bool:
        last = self._warned.get(tag)
        if last is not None and 0 <= now - last < WARN_INTERVAL_S:
            return False
        self._warned[tag] = now
        return True

    def _load(self) -> None:
        path = last_known_path(self._base)
        self._cleanup_tmp(path.parent)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                log.warning("last_known.json is too large; ignored")
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                   # OSError, ValueError, RecursionError, MemoryError...
            return
        devs = raw.get("devices") if isinstance(raw, dict) else None
        if not isinstance(devs, dict):
            return
        now = time.time()
        for key, v in devs.items():
            if not is_visibility_key(key):
                continue
            e = _valid_entry(v, now)
            if e is not None:
                self._known[key] = e
        self._prune(now)

    @staticmethod
    def _cleanup_tmp(folder: Path) -> None:
        try:
            for f in folder.glob("last_known.*.tmp"):
                try:
                    f.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _prune(self, now: float) -> None:
        def newest(e):
            return max(e.get("seen") or 0.0, e.get("ts") or 0.0)
        for k in [k for k, e in self._known.items() if now - newest(e) > MAX_AGE_S]:
            del self._known[k]
        if len(self._known) > MAX_ENTRIES:
            keep = sorted(self._known, key=lambda k: newest(self._known[k]), reverse=True)[:MAX_ENTRIES]
            self._known = {k: self._known[k] for k in keep}

    def _save(self, now: float) -> None:
        self._prune(now)
        path = last_known_path(self._base)
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="last_known.", suffix=".tmp", dir=str(path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "devices": self._known}, f, indent=1)
            os.replace(tmp, path)                # atomic: no fsync needed (keeps the UI thread free)
            tmp = None
            self._dirty = self._ts_dirty = False
            self._save_failed = False
            self._last_save = now
        except Exception:
            self._save_failed = True
            self._last_save = now                # back off instead of retrying on every refresh
            if self._warn_ok("save", now):
                log.warning("could not save last_known.json")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def flush(self, now: float | None = None) -> None:
        if self._dirty or self._ts_dirty:
            self._save(time.time() if now is None else now)

    def close(self) -> None:
        self.flush()
        if self._handler is not None:
            try:
                self._handler.close()
            except Exception:
                pass
            self._handler = self._logger = None

    # ------------------------------------------------------------------ event log
    def _write(self, now: float, key: str, ent: dict, event: str, battery, charging) -> None:
        try:
            if self._logger is None:
                self._handler = make_file_handler(device_log_path(self._base))
                self._handler.setFormatter(logging.Formatter("%(message)s"))
                self._logger = logging.Logger("tbw.devices")      # private: not in the logger tree
                self._logger.setLevel(logging.INFO)
                self._logger.addHandler(self._handler)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            bat = f"{int(battery)}%" if battery is not None else "-"
            self._logger.info(" | ".join((
                stamp, f"{ent['name']} [{key[2:8]}]", ent["kind"], ent["link"], event, bat,
                "charging" if charging else "no")))
        except Exception:
            log.warning("could not write the device log", exc_info=True)

    # ------------------------------------------------------------------ main entry point
    def update(self, devices, now: float | None = None, show_disconnected: bool = False,
               hidden=(), threshold: int = 20) -> list[Device]:
        """Record events/last values for `devices` and return the list to display.

        Devices that are disconnected or without a level get their last known value (``stale=True``);
        with `show_disconnected`, known non-Bluetooth devices that are no longer listed are appended
        as disconnected entries (unless hidden or already present). Never raises.
        """
        devices = list(devices)
        now = time.time() if now is None else float(now)
        try:
            out = self._update(devices, now, bool(show_disconnected), set(hidden or ()), int(threshold))
            since = now - self._last_save
            if self._save_failed:
                due = (self._dirty or self._ts_dirty) and not 0 <= since < SAVE_RETRY_S
            else:
                due = self._dirty or (self._ts_dirty and not 0 <= since < SAVE_INTERVAL_S)
            if due:
                self._save(now)
            return out
        except Exception:
            if self._warn_ok("update", now):
                log.exception("device history update failed")
            return devices

    def _update(self, devices, now, show_disc, hidden, threshold) -> list[Device]:
        out: list[Device] = []
        present: dict[str, Device] = {}
        self._pkeys = frozenset(visibility_key(d.id) for d in devices)
        for d in devices:
            key = visibility_key(d.id)
            present[key] = d
            ent = self._entry(key, d, now)
            live = d.connected and d.battery is not None
            rt = self._rt.setdefault(key, {})
            if d.connected:
                if not rt.get("connected"):
                    rt.clear()
                    rt["connected"] = True
                    ent["disc"] = None
                    self._dirty = True
                    self._write(now, key, ent, "connected", d.battery if live else None,
                                bool(d.charging) and live)
            elif rt.get("connected") or (not rt and ent["disc"] is None):
                if rt.get("connected"):
                    self._write(now, key, ent, "disconnected", ent["battery"], False)
                rt.clear()
                rt["connected"] = False
                ent["disc"] = now
                self._dirty = True
            if live:
                self._record(key, d, ent, rt, now, threshold)
            if (not d.connected or d.battery is None) and ent["battery"] is not None:
                out.append(replace(d, battery=ent["battery"], charging=False, idle=False,
                                   stale=True, last_seen=ent["ts"]))
            else:
                out.append(d)
        # devices that were listed before and have vanished
        for key, rt in self._rt.items():
            if key not in present and rt.get("connected"):
                ent = self._known.get(key)
                if ent is not None:
                    self._write(now, key, ent, "disconnected", ent["battery"], False)
                    ent["disc"] = now
                    self._dirty = True
                rt.clear()
                rt["connected"] = False
        out.extend(self._absent(present, show_disc, hidden))
        return out

    def _entry(self, key: str, d: Device, now: float) -> dict:
        kind = d.kind if d.kind in KINDS else "generic"
        link = d.link if d.link in LINKS else "bluetooth"
        name = _clean_name(d.name)
        ent = self._known.get(key)
        if ent is None:
            ent = self._known[key] = {"name": name, "kind": kind, "link": link, "battery": None,
                                      "charging": False, "ts": None, "disc": None, "seen": now}
            self._dirty = True
            self._adopt_old(key, ent)
        else:
            if (ent["name"], ent["kind"], ent["link"]) != (name, kind, link):
                ent.update(name=name, kind=kind, link=link)
                self._dirty = True
            if now - ent["seen"] >= 1:
                ent["seen"] = now
                self._ts_dirty = True
        return ent

    def _adopt_old(self, key: str, ent: dict) -> None:
        """A new non-Bluetooth key may be an old device under an unstable id (path/slot based):
        drop the old entry and inherit its last value so no ghost is left behind."""
        if ent["link"] == "bluetooth":
            return
        t = (ent["name"], ent["kind"], ent["link"])
        olds = [(k, e) for k, e in self._known.items()
                if k != key and k not in self._pkeys and (e["name"], e["kind"], e["link"]) == t]
        if not olds:
            return
        k, old = max(olds, key=lambda ke: ke[1]["ts"] or 0.0)
        if old["battery"] is not None:
            ent.update(battery=old["battery"], charging=old["charging"], ts=old["ts"])
        del self._known[k]

    def _record(self, key, d: Device, ent: dict, rt: dict, now: float, threshold: int) -> None:
        b, chg = int(d.battery), bool(d.charging)
        idle = bool(getattr(d, "idle", False))
        ts = now
        if idle and d.updated is not None:
            ts = min(now, float(d.updated))
        if (ent["battery"], ent["charging"]) != (b, chg):
            self._dirty = True
        if ent["ts"] is None or ts > ent["ts"]:
            ent["ts"] = ts
            self._ts_dirty = True
        ent["battery"], ent["charging"] = b, chg
        if idle:
            return                          # a sleeping device re-reports an old value: no events
        prev = rt.get("batt")
        low = b <= threshold or (bool(rt.get("low")) and b <= threshold + LOW_REARM_MARGIN)
        if prev is None:                    # baseline (the 'connected' line already carries it)
            rt.update(batt=b, chg=chg, low=low, logged_b=b, logged_ts=now)
            return
        if chg and not rt["chg"]:
            self._write(now, key, ent, "charging_started", b, chg)
        elif rt["chg"] and not chg:
            self._write(now, key, ent, "charging_stopped", b, chg)
        crossed_low = low != rt["low"]
        crossed_full = (b >= 100) != (prev >= 100)
        if low and not rt["low"]:
            self._write(now, key, ent, "low", b, chg)
        if (b != rt["logged_b"]
                and (now - rt["logged_ts"] >= BATTERY_MIN_INTERVAL_S or crossed_low or crossed_full)):
            self._write(now, key, ent, "battery", b, chg)
            rt["logged_b"], rt["logged_ts"] = b, now
        rt.update(batt=b, chg=chg, low=low)

    def _absent(self, present: dict, show_disc: bool, hidden: set) -> list[Device]:
        """Known wireless devices that are not listed any more, as dimmed 'disconnected' entries."""
        if not show_disc:
            return []
        triple = lambda e: (e["name"], e["kind"], e["link"])         # noqa: E731
        # An absent entry that looks like a present device (same name/kind/link) is the same device
        # under another id: never show both.
        shown = {triple(self._known[k]) for k in present if k in self._known}
        cands = [(k, e) for k, e in self._known.items()
                 if k not in present and k not in hidden and e["link"] != "bluetooth"
                 and e["battery"] is not None and e["ts"] is not None]
        cands.sort(key=lambda ke: ke[1]["ts"], reverse=True)
        res: list[Device] = []
        for k, e in cands:
            if triple(e) in shown:
                continue
            res.append(Device(id=k, name=e["name"], kind=e["kind"], battery=e["battery"],
                              connected=False, link=e["link"], stale=True, last_seen=e["ts"]))
        return res
