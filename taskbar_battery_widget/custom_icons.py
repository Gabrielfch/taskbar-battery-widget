"""User-supplied icon images (per device kind or per device).

Images are validated and copied to ``%LOCALAPPDATA%\\TaskbarBatteryWidget\\icons`` under a content-hash
name, so config only stores safe file names (never paths). Every drawing helper fails soft: if the
image is missing or broken the caller falls back to the built-in icon.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImageReader, QPainter, QPixmap

from .device_history import base_dir

ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".ico", ".gif")
MAX_BYTES = 2 * 1024 * 1024
MAX_DIM = 2048
_SAFE_NAME = re.compile(r"^[0-9a-f]{16}\.(png|jpg|jpeg|bmp|ico|gif)$")
_CACHE_MAX = 128

# Error codes (translate with the i18n keys of the same name).
ERR_EXTENSION = "icon_err_extension"
ERR_TOO_BIG = "icon_err_too_big"
ERR_DIMENSIONS = "icon_err_dimensions"
ERR_UNREADABLE = "icon_err_unreadable"
ERR_IO = "icon_err_io"


class IconImportError(Exception):
    """``code`` is one of the ERR_* constants (also the exception message)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def icons_dir() -> Path:
    return base_dir() / "icons"


def safe_name(value) -> bool:
    return isinstance(value, str) and _SAFE_NAME.match(value) is not None


def import_image(src) -> str:
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in ALLOWED_EXT:
        raise IconImportError(ERR_EXTENSION)
    try:
        if src.stat().st_size > MAX_BYTES:
            raise IconImportError(ERR_TOO_BIG)
        data = src.read_bytes()
    except IconImportError:
        raise
    except OSError:
        raise IconImportError(ERR_IO) from None
    if len(data) > MAX_BYTES:
        raise IconImportError(ERR_TOO_BIG)
    reader = QImageReader(str(src))
    reader.setAutoTransform(False)
    if not reader.canRead():
        raise IconImportError(ERR_UNREADABLE)
    size = reader.size()
    if size.isValid() and (size.width() > MAX_DIM or size.height() > MAX_DIM):
        raise IconImportError(ERR_DIMENSIONS)
    img = reader.read()
    if img.isNull():
        raise IconImportError(ERR_UNREADABLE)
    if img.width() > MAX_DIM or img.height() > MAX_DIM:
        raise IconImportError(ERR_DIMENSIONS)
    name = hashlib.sha256(data).hexdigest()[:16] + ext
    try:
        d = icons_dir()
        d.mkdir(parents=True, exist_ok=True)
        dest = d / name
        if not dest.exists():
            tmp = d / (name + ".tmp")
            shutil.copyfile(src, tmp)
            tmp.replace(dest)
    except OSError:
        raise IconImportError(ERR_IO) from None
    return name


def resolve(kind: str, key: str, by_kind: dict, by_device: dict) -> str | None:
    for table, k in ((by_device, key), (by_kind, kind)):
        try:
            v = table.get(k) if isinstance(table, dict) else None
        except TypeError:
            v = None
        if safe_name(v):
            return v
    return None


# Active maps (pushed from the config by tray.apply_appearance), like the palette / icon style.
_by_kind: dict = {}
_by_device: dict = {}
_version = 0


def set_maps(by_kind, by_device) -> None:
    global _by_kind, _by_device, _version
    nk = {k: v for k, v in by_kind.items() if safe_name(v)} if isinstance(by_kind, dict) else {}
    nd = {k: v for k, v in by_device.items() if safe_name(v)} if isinstance(by_device, dict) else {}
    if nk != _by_kind or nd != _by_device:
        _by_kind, _by_device = nk, nd
        _version += 1


def maps_version() -> int:
    """Bumped whenever the active maps change (layout / panel signatures include it)."""
    return _version


def file_for(kind: str, key: str) -> str | None:
    """Image file name to draw for a device (device > kind), or None for the built-in icon."""
    return resolve(kind, key, _by_kind, _by_device)


# (filename, size_px, dpr) -> QPixmap ; failures: filename -> mtime_ns at failure
_cache: OrderedDict = OrderedDict()
_failed: dict = {}


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def clear_cache() -> None:
    _cache.clear()
    _failed.clear()


def pixmap_for(filename: str, size_px: int, dpr: float) -> QPixmap | None:
    try:
        if not safe_name(filename):
            return None
        size_px = int(size_px)
        dpr = float(dpr) if dpr and dpr > 0 else 1.0
        if size_px <= 0:
            return None
        path = icons_dir() / filename
        mt = _mtime(path)
        if mt is None:
            _failed[filename] = None
            return None
        if filename in _failed and _failed[filename] == mt:
            return None
        key = (filename, size_px, round(dpr, 3), mt)
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
        _failed.pop(filename, None)
        reader = QImageReader(str(path))
        sz = reader.size()
        if not reader.canRead() or (sz.isValid() and (sz.width() > MAX_DIM or sz.height() > MAX_DIM)):
            _failed[filename] = mt
            return None
        img = reader.read()
        if img.isNull():
            _failed[filename] = mt
            return None
        px = max(1, int(round(size_px * dpr)))
        scaled = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(px, px)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        p.end()
        out.setDevicePixelRatio(dpr)
        # drop stale entries for the same file (older mtime)
        for k in [k for k in _cache if k[0] == filename and k[3] != mt]:
            del _cache[k]
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
        return out
    except Exception:  # never raises
        return None


def draw_custom_icon(painter: QPainter, rect, filename: str, opacity: float = 1.0) -> bool:
    try:
        r = QRectF(rect)
        side = min(r.width(), r.height())
        if side < 1:
            return False
        dpr = 1.0
        try:
            dpr = float(painter.device().devicePixelRatioF())
        except Exception:
            pass
        px = pixmap_for(filename, int(round(side)), dpr)
        if px is None:
            return False
        painter.save()
        try:
            painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(opacity))))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            target = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
            painter.drawPixmap(target, px, QRectF(0, 0, px.width(), px.height()))
        finally:
            painter.restore()
        return True
    except Exception:
        return False


def cleanup_unused(used: set[str]) -> None:
    try:
        d = icons_dir()
        if not d.is_dir():
            return
        keep = {u for u in used if isinstance(u, str)}
        for f in d.iterdir():
            if f.is_file() and f.name not in keep:
                try:
                    f.unlink()
                except OSError:
                    pass
    except OSError:
        pass
