"""Opt-in per-device CSV sample log (one row per minute while enabled).

Files live in ``%LOCALAPPDATA%\\TaskbarBatteryWidget\\logs\\devices`` as ``<slug>_<key>.csv``. ``key`` is
derived from a random per-install salt (``logs\\.detail_salt``) so file names cannot be brute-forced back to a
Bluetooth address, and nothing written contains a device id, MAC or serial.

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

HEADER = "time_iso,battery,charging,connected,link,idle"
QUEUE_SIZE = 1000
WARN_INTERVAL_S = 600
_SLUG_BAD = re.compile(r"[^A-Za-z0-9_-]+")


def _slug(name: str) -> str:
    s = _SLUG_BAD.sub("_", _clean_name(name).encode("ascii", "ignore").decode("ascii"))
    s = re.sub(r"_+", "_", s).strip("_-")[:32].strip("_-")
    return s or "device"


def _flag(v) -> str:
    return "1" if v else "0"


class DetailLogger:
    def __init__(self, base_dir: Path | None = None, clock=time.time, max_bytes: int = 1_000_000,
                 backups: int = 3, max_devices: int = 20):
        self.base = Path(base_dir) if base_dir else logs_dir() / "devices"
        self._clock = clock
        self.max_bytes = max_bytes
        self.backups = backups
        self.max_devices = max_devices
        self._salt: str | None = None
        self._last_minute: dict[Path, int] = {}
        self._paths: set[Path] = set()
        self._warned_limit = False
        self._last_full_warn = float("-inf")
        self._failed: set[Path] = set()
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    # ---- naming ----
    def _salt_path(self) -> Path:
        return self.base.parent / ".detail_salt"

    def _get_salt(self) -> str:
        if self._salt:
            return self._salt
        p = self._salt_path()
        try:
            s = p.read_text(encoding="ascii").strip()
            if re.fullmatch(r"[0-9a-f]{32}", s):
                self._salt = s
                return s
        except OSError:
            pass
        s = secrets.token_hex(16)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".salt", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(s)
            os.replace(tmp, p)
        except OSError as e:
            log.warning("detail log: cannot persist salt (%s); using a session-only salt", e)
        self._salt = s
        return s

    def path_for(self, device: Device) -> Path:
        """CSV path of a device (creates nothing)."""
        key = hashlib.sha256((self._get_salt() + str(device.id)).encode("utf-8", "replace")).hexdigest()[:16]
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
                    row = ",".join((
                        stamp,
                        "" if d.battery is None else str(int(d.battery)),
                        _flag(d.charging), _flag(d.connected),
                        _slug(d.link or ""), _flag(d.idle),
                    ))
                    self._enqueue((path, row))
                except Exception as e:  # noqa: BLE001 - logging must never break the app
                    log.debug("detail log: skipped a device (%s)", e)
        except Exception as e:  # noqa: BLE001
            log.debug("detail log tick failed: %s", e)

    def _enqueue(self, item) -> None:
        self._ensure_thread()
        try:
            self._q.put_nowait(item)
        except queue.Full:
            t = self._clock()
            if t - self._last_full_warn >= WARN_INTERVAL_S:
                self._last_full_warn = t
                log.warning("detail log: queue full, dropping samples")

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is None:
                th = threading.Thread(target=self._run, name="detail-log", daemon=True)
                self._thread = th
                th.start()

    def close(self, timeout: float = 2.0) -> None:
        """Flush queued rows and stop the writer thread. Safe to call twice."""
        self._closed = True
        th = self._thread
        if th is None:
            return
        try:
            self._q.put(None, timeout=timeout)
        except queue.Full:
            return
        th.join(timeout)

    # ---- writer thread ----
    def _run(self) -> None:
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
