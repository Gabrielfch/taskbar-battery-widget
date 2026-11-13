"""Opt-in per-device CSV sample log (one row per minute while enabled).

Files live in ``%LOCALAPPDATA%\\TaskbarBatteryWidget\\logs\\devices`` as ``<slug>_<key>.csv``. ``key`` is
a salted hash of the device's visibility key; the random per-install salt is stored outside the logs folder
(``%LOCALAPPDATA%\\TaskbarBatteryWidget\\.detail_salt``) so sharing the logs does not expose it. The file
name does contain a sanitized device name (it may be a name the user gave the device); nothing written
contains a device id, MAC or serial.

``tick`` never blocks or raises: rows are queued and a daemon thread does all the disk work.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Collection, Iterable
from datetime import datetime
from pathlib import Path

from .device_history import _clean_name, logs_dir
from .models import Device, visibility_key

log = logging.getLogger(__name__)

HEADER = "time_iso,battery,charging,connected,link,idle,stale"
QUEUE_SIZE = 1000
WARN_INTERVAL_S = 600
RETENTION_DAYS = 90
SALT_MAX_BYTES = 256
_SLUG_BAD = re.compile(r"[^A-Za-z0-9_-]+")


def _slug(name: str) -> str:
    s = _SLUG_BAD.sub("_", _clean_name(name).encode("ascii", "ignore").decode("ascii"))
    s = re.sub(r"_+", "_", s).strip("_-")[:32].strip("_-")
    return s or "device"


def _flag(v) -> str:
    return "1" if v else "0"


class DetailLogger:
    def __init__(self, base_dir: Path | None = None, clock=time.time, max_bytes: int = 1_000_000,
                 backups: int = 3, max_devices: int = 20, retention_days: float = RETENTION_DAYS):
        self.base = Path(base_dir) if base_dir else logs_dir() / "devices"
        self._clock = clock
        self.max_bytes = max_bytes
        self.backups = backups
        self.max_devices = max_devices
        self.retention_days = retention_days
        self._rotate_warned: set[Path] = set()
        self._salt: str | None = None
        self._last_minute: dict[Path, int] = {}
        self._paths: set[Path] = set()
        self._warned_limit = False
        self._last_full_warn = float("-inf")
        self._failed: set[Path] = set()
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False

    # ---- naming ----
    def _salt_path(self) -> Path:
        # app data dir (parent of ``logs``), NOT the logs folder: the logs can be shared safely
        return self.base.parent.parent / ".detail_salt"

    def _legacy_salt_path(self) -> Path:
        return self.base.parent / ".detail_salt"

    @staticmethod
    def _read_salt(p: Path) -> str | None:
        try:
            with open(p, "rb") as f:
                raw = f.read(SALT_MAX_BYTES + 1)
        except OSError:
            return None
        if len(raw) > SALT_MAX_BYTES:
            log.warning("detail log: salt file %s is oversized; regenerating", p.name)
            return None
        s = raw.decode("ascii", "ignore").strip()
        if re.fullmatch(r"[0-9a-f]{32}", s):
            return s
        log.warning("detail log: salt file %s is invalid; regenerating", p.name)
        return None

    def _persist_salt(self, s: str, p: Path) -> bool:
        tmp = None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".salt", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(s)
            os.replace(tmp, p)
            tmp = None
            return True
        except OSError as e:
            log.warning("detail log: cannot persist salt (%s); using a session-only salt", e)
            return False
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _get_salt(self) -> str:
        if self._salt:
            return self._salt
        p = self._salt_path()
        s = self._read_salt(p) if p.exists() else None
        if s is None:
            legacy = self._legacy_salt_path()
            old = self._read_salt(legacy) if legacy.exists() else None
            if old is not None:                 # move it out of the logs folder: existing files stay valid
                if self._persist_salt(old, p):
                    try:
                        legacy.unlink()
                    except OSError:
                        pass
                s = old
        if s is None:
            s = secrets.token_hex(16)
            self._persist_salt(s, p)
        self._salt = s
        return s

    def path_for(self, device: Device) -> Path:
        """CSV path of a device (creates nothing)."""
        # visibility_key is idempotent: a live device and its rebuilt "absent" twin share one file
        vk = visibility_key(device.id)
        key = hashlib.sha256((self._get_salt() + vk).encode("utf-8", "replace")).hexdigest()[:16]
        return self.base / f"{_slug(device.name)}_{key}.csv"

    # ---- producer side ----
    def tick(self, devices: Iterable[Device], enabled_keys: Collection[str], now: float | None = None) -> None:
        try:
            if self._closed:
                return
            now = self._clock() if now is None else now
            minute = int(now // 60)
            try:
                stamp = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
            except (OSError, OverflowError, ValueError):
                return
            for d in devices:
                try:
                    if visibility_key(d.id) not in enabled_keys:
                        continue
                    path = self.path_for(d)
                    if self._last_minute.get(path) == minute:
                        continue
                    if path not in self._paths:
                        if len(self._paths) >= self.max_devices:
                            if not self._warned_limit:
                                self._warned_limit = True
                                log.warning("detail log: more than %d devices; ignoring extras", self.max_devices)
                            continue
                        self._paths.add(path)
                    self._last_minute[path] = minute
                    stale = bool(getattr(d, "stale", False))
                    row = ",".join((
                        stamp,
                        "" if d.battery is None or stale else str(int(d.battery)),
                        _flag(d.charging), _flag(d.connected),
                        _slug(d.link or ""), _flag(d.idle), _flag(stale),
                    ))
                    self._enqueue((path, row))
                except Exception as e:  # noqa: BLE001 - logging must never break the app
                    log.debug("detail log: skipped a device (%s)", e)
        except Exception as e:  # noqa: BLE001
            log.debug("detail log tick failed: %s", e)

    def _enqueue(self, item) -> None:
        with self._lock:                        # same lock as close(): no row after the sentinel
            if self._closed:
                return
            self._ensure_thread()
            try:
                self._q.put_nowait(item)
            except queue.Full:
                t = self._clock()
                if t - self._last_full_warn >= WARN_INTERVAL_S:
                    self._last_full_warn = t
                    log.warning("detail log: queue full, dropping samples")

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None:
                th = threading.Thread(target=self._run, name="detail-log", daemon=True)
                self._thread = th
                th.start()

    def close(self, timeout: float = 2.0) -> None:
        """Flush queued rows and stop the writer thread. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            th = self._thread
            if th is None:
                return
            for _ in range(3):                  # a full queue drops one row so the sentinel gets in
                try:
                    self._q.put_nowait(None)
                    break
                except queue.Full:
                    try:
                        self._q.get_nowait()
                    except queue.Empty:
                        pass
        th.join(timeout)

    # ---- writer thread ----
    def _purge_old(self) -> None:
        """Delete CSV files (and rotated ``.csv.N`` backups) older than the retention period."""
        try:
            limit = self._clock() - self.retention_days * 86400
            if not self.base.exists():
                return
            for f in self.base.iterdir():
                if not re.search(r"\.csv(\.\d+)?$", f.name):
                    continue
                try:
                    if f.is_file() and f.stat().st_mtime < limit:
                        f.unlink()
                except OSError:
                    pass
        except Exception as e:  # noqa: BLE001
            log.debug("detail log: retention purge failed (%s)", e)

    def _run(self) -> None:
        self._purge_old()
        while True:
            item = self._q.get()
            if item is None:
                return
            path, row = item
            try:
                self._write(path, row)
            except Exception as e:  # noqa: BLE001
                if path not in self._failed:
                    self._failed.add(path)
                    log.warning("detail log: cannot write %s: %s", path.name, e)

    def _write(self, path: Path, row: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size >= self.max_bytes:
                self._rotate(path)
        except FileNotFoundError:
            pass
        except OSError as e:                    # e.g. the file is open in Excel: keep appending
            if path not in self._rotate_warned:
                self._rotate_warned.add(path)
                log.warning("detail log: cannot rotate %s (%s); appending to it", path.name, e)
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(HEADER + "\r\n")
            f.write(row + "\r\n")
        self._failed.discard(path)

    def _rotate(self, path: Path) -> None:
        def bak(i: int) -> Path:
            return path.with_name(f"{path.name}.{i}")
        if self.backups < 1:
            path.unlink()
            return
        bak(self.backups).unlink(missing_ok=True)
        for i in range(self.backups - 1, 0, -1):
            if bak(i).exists():
                os.replace(bak(i), bak(i + 1))
        os.replace(path, bak(1))
