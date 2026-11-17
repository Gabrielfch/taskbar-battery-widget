"""Remembers which receiver id a Logitech unit was last seen under.

When a wireless Logitech device is plugged in to charge it drops off its receiver, so the scan only sees
its USB entry. This map (``unit_links.json`` in ``%LOCALAPPDATA%\\TaskbarBatteryWidget``) lets the scan
give that USB entry the receiver id again (one pill, with its alias / icon / history).

The unit id itself is never stored or logged: the map key is ``sha256(salt + unit)[:16]`` with a random
per-install salt kept next to the app data (``.unit_salt``), outside the logs folder. Values are the
receiver device id and its sanitized name. Writes are atomic; the file is validated strictly on load.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from .device_history import _clean_name, base_dir
from .models import visibility_key

log = logging.getLogger(__name__)

MAX_ENTRIES = 64
MAX_FILE_BYTES = 64_000
SALT_MAX_BYTES = 256
_KEY_RE = re.compile(r"[0-9a-f]{16}")
_ID_RE = re.compile(r"logi:[0-9a-f]{4}:[0-9a-f]{2}(:[0-9a-f]{6})?")
_SALT_RE = re.compile(r"[0-9a-f]{32}")


def valid_receiver_id(device_id) -> bool:
    return isinstance(device_id, str) and _ID_RE.fullmatch(device_id) is not None


class UnitLinks:
    def __init__(self, directory: Path | None = None):
        self._dir = Path(directory) if directory else None
        self._lock = threading.RLock()
        self._salt: str | None = None
        self._map: dict[str, dict] | None = None        # key -> {"id", "name"}, insertion = age order

    # ---- paths ----
    def _base(self) -> Path:
        return self._dir if self._dir else base_dir()

    def _path(self) -> Path:
        return self._base() / "unit_links.json"

    def _salt_path(self) -> Path:
        return self._base() / ".unit_salt"

    # ---- atomic write ----
    def _write(self, p: Path, text: str) -> bool:
        tmp = None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".ul", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, p)
            tmp = None
            return True
        except OSError as e:
            log.warning("unit links: cannot write %s (%s)", p.name, e)
            return False
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ---- salt ----
    def _get_salt(self) -> str:
        if self._salt:
            return self._salt
        s = None
        try:
            with open(self._salt_path(), "rb") as f:
                raw = f.read(SALT_MAX_BYTES + 1)
            if len(raw) <= SALT_MAX_BYTES:
                cand = raw.decode("ascii", "ignore").strip()
                if _SALT_RE.fullmatch(cand):
                    s = cand
        except OSError:
            pass
        if s is None:
            s = secrets.token_hex(16)
            self._write(self._salt_path(), s)
        self._salt = s
        return s

    def link_key(self, unit: str) -> str:
        return hashlib.sha256((self._get_salt() + str(unit)).encode("utf-8", "replace")).hexdigest()[:16]

    # ---- map ----
    def _load(self) -> dict[str, dict]:
        if self._map is not None:
            return self._map
        out: dict[str, dict] = {}
        try:
            p = self._path()
            if p.stat().st_size <= MAX_FILE_BYTES:
                data = json.loads(p.read_text(encoding="utf-8"))
                items = data.get("links") if isinstance(data, dict) else None
                if isinstance(items, dict):
                    for k, v in items.items():
                        if len(out) >= MAX_ENTRIES:
                            break
                        if not (isinstance(k, str) and _KEY_RE.fullmatch(k) and isinstance(v, dict)):
                            continue
                        did = v.get("id")
                        name = v.get("name")
                        if not valid_receiver_id(did) or not isinstance(name, str):
                            continue
                        out[k] = {"id": did, "name": _clean_name(name[:80])}
        except (OSError, ValueError, RecursionError):
            pass
        self._map = out
        return out

    def _save(self) -> None:
        self._write(self._path(), json.dumps({"version": 1, "links": self._map or {}}, indent=1))

    def learn(self, unit: str, device_id: str, name: str) -> None:
        """Record that `unit` is the device `device_id` behind a receiver."""
        if not unit or not valid_receiver_id(device_id):
            return
        try:
            with self._lock:
                m = self._load()
                key = self.link_key(unit)
                entry = {"id": device_id, "name": _clean_name(name or "")}
                changed = m.get(key) != entry
                for k in [k for k, v in m.items() if v["id"] == device_id and k != key]:
                    del m[k]                    # the slot was re-paired with another unit
                    changed = True
                if changed:
                    m.pop(key, None)
                    m[key] = entry
                    while len(m) > MAX_ENTRIES:
                        del m[next(iter(m))]
                    self._save()
        except Exception:
            log.debug("unit links: learn failed", exc_info=True)

    def lookup(self, unit: str) -> tuple[str, str] | None:
        """(receiver device id, remembered name) of `unit`, or None."""
        if not unit:
            return None
        try:
            with self._lock:
                v = self._load().get(self.link_key(unit))
                return (v["id"], v["name"]) if v else None
        except Exception:
            return None

    def forget(self, key_or_id: str) -> int:
        """Drop the relations whose receiver id is this device (given as id or visibility key)."""
        if not isinstance(key_or_id, str) or not key_or_id:
            return 0
        target = visibility_key(key_or_id)
        with self._lock:
            m = self._load()
            gone = [k for k, v in m.items() if visibility_key(v["id"]) == target]
            for k in gone:
                del m[k]
            if gone:
                self._save()
            return len(gone)

    def reset(self, clear_disk: bool = False) -> None:
        with self._lock:
            self._map = None
            self._salt = None
            if clear_disk:
                for p in (self._path(), self._salt_path()):
                    try:
                        p.unlink()
                    except OSError:
                        pass


_default = UnitLinks()


def default() -> UnitLinks:
    return _default
