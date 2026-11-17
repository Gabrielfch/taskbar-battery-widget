"""User-supplied icon images (per device kind or per device).

Images are validated and copied to ``%LOCALAPPDATA%\\TaskbarBatteryWidget\\icons`` under a content-hash
name, so config only stores safe file names (never paths). Every drawing helper fails soft: if the
image is missing or broken the caller falls back to the built-in icon.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap

from .device_history import base_dir

ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".ico", ".gif", ".webp", ".svg")
MAX_BYTES = 2 * 1024 * 1024
MAX_SVG_BYTES = 512 * 1024
MAX_SVG_DIM = 4096          # logical size / viewBox limit of an imported SVG
SVG_OUT_PX = 256            # an SVG is rendered once, at import, to a PNG of this size
MAX_DIM = 2048
# Stored names: "<sha16>.<ext>" for a still image, "<sha16>.anim.png" for an animation (a horizontal strip of
# 64x64 frames) with its timings in the sidecar "<sha16>.anim.json" (never referenced by the config).
_SAFE_NAME = re.compile(r"[0-9a-f]{16}\.(?:png|jpg|jpeg|bmp|ico|gif|anim\.png)")
_SAFE_SIDECAR = re.compile(r"[0-9a-f]{16}\.anim\.json")
_ALLOWED_FORMATS = {"png", "jpg", "jpeg", "bmp", "ico", "gif"}
_TMP_MAX_AGE = 3600
_CACHE_MAX = 128

# Animated icons (GIF / WebP): limits applied while reading the source and on the stored strip.
ANIM_FRAME_PX = 64          # every frame is fitted into a 64x64 transparent square
MAX_ANIM_READ = 200         # more source frames than this: rejected
MAX_ANIM_FRAMES = 48        # frames kept (uniform subsampling above this)
MIN_FRAME_MS = 66           # <= 15 fps
MAX_FRAME_MS = 2000
MAX_ANIM_TOTAL_MS = 10_000  # total duration of the source (after clamping each delay)
MAX_ANIM_PIXELS = 60_000_000  # source width * height * frames: bounds the decoding work
ANIM_READ_TIMEOUT = 6.0     # seconds spent decoding before the import is abandoned
MAX_ANIM_JSON = 2048
_ANIM_FORMATS = {"gif", "webp"}
_ANIM_CACHE_MAX = 8
_ANIM_PX_CACHE_MAX = 24

# Error codes (translate with the i18n keys of the same name).
ERR_EXTENSION = "icon_err_extension"
ERR_TOO_BIG = "icon_err_too_big"
ERR_DIMENSIONS = "icon_err_dimensions"
ERR_UNREADABLE = "icon_err_unreadable"
ERR_IO = "icon_err_io"
ERR_ANIM_LONG = "icon_err_anim_long"


class IconImportError(Exception):
    """``code`` is one of the ERR_* constants (also the exception message)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def icons_dir() -> Path:
    return base_dir() / "icons"


def safe_name(value) -> bool:
    """A file name the config may reference (still image or ``.anim.png`` strip)."""
    return isinstance(value, str) and _SAFE_NAME.fullmatch(value) is not None


def safe_file(value) -> bool:
    """Any file this module creates in the icons folder: an icon name or an ``.anim.json`` sidecar."""
    return safe_name(value) or (isinstance(value, str) and _SAFE_SIDECAR.fullmatch(value) is not None)


def is_animated(value) -> bool:
    """True for the name of an animated icon (a pure name check; the file is not touched)."""
    return safe_name(value) and value.endswith(".anim.png")


def sidecar_name(filename: str) -> str:
    """``<sha16>.anim.png`` -> ``<sha16>.anim.json``."""
    return filename[:-len(".png")] + ".json"


def _open_reader(data: bytes):
    """(reader, buffer) over `data`; the buffer must outlive the reader."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setAutoTransform(False)
    return reader, buf


def _format_of(reader) -> str:
    try:
        return bytes(reader.format()).decode("ascii", "ignore").lower()
    except Exception:
        return ""


def _format_ok(reader, allowed=_ALLOWED_FORMATS) -> bool:
    return _format_of(reader) in allowed


def _decode(data: bytes, err: str, allowed=_ALLOWED_FORMATS):
    """Validate `data` (allowed raster format, size limits) and return the decoded QImage."""
    reader, buf = _open_reader(data)
    if not reader.canRead() or not _format_ok(reader, allowed):
        raise IconImportError(err)
    size = reader.size()
    if size.isValid() and (size.width() > MAX_DIM or size.height() > MAX_DIM):
        raise IconImportError(ERR_DIMENSIONS)
    img = reader.read()
    if img.isNull():
        raise IconImportError(ERR_UNREADABLE)
    if img.width() > MAX_DIM or img.height() > MAX_DIM:
        raise IconImportError(ERR_DIMENSIONS)
    return img


# --- SVG: parsed, rebuilt from an allow-list, and THAT rebuilt document is rendered ONCE to a PNG.
# The original bytes never reach Qt (Qt may honour a different encoding declaration than the one we
# validated, e.g. UTF-7), and the SVG itself is never stored nor parsed again.
_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_ELEMENTS = frozenset({
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs",
    "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc"})
_SVG_ATTRS = frozenset({
    "id", "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "width", "height",
    "points", "viewBox", "transform", "version", "preserveAspectRatio", "fill", "fill-opacity", "fill-rule",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity",
    "opacity", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod",
    "clip-path", "clip-rule", "clipPathUnits"})
# Editor metadata namespaces (Inkscape): inert, silently dropped. Any other foreign name is rejected.
_SVG_IGNORED_NS = ("http://www.inkscape.org/namespaces/inkscape",
                   "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd")
_SVG_URL_ATTRS = {"fill": ("linearGradient", "radialGradient"), "stroke": ("linearGradient", "radialGradient"),
                  "clip-path": ("clipPath",)}
_SVG_BULK_ATTRS = ("d", "points")
MAX_SVG_ELEMENTS = 2000
MAX_SVG_DEPTH = 32
MAX_SVG_BULK = 200 * 1024       # total length of all d / points values
MAX_SVG_NUMBER = 1e6
SVG_RENDER_TIMEOUT = 3.0        # seconds
_SVG_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{0,63}")
_SVG_VALUE_CHARS = re.compile(r"[A-Za-z0-9\s.,%#()+\-_'\"]*")
_SVG_URL_REF = re.compile(r"url\(\s*(['\"]?)#([A-Za-z_][A-Za-z0-9_.\-]{0,63})\1\s*\)", re.I)
_SVG_URL_ANY = re.compile(r"url", re.I)
_SVG_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SVG_NONFINITE = re.compile(r"\b(?:nan|inf|infinity)\b", re.I)
_SVG_HEX = re.compile(r"#[0-9A-Fa-f]+")
_SVG_XMLDECL = re.compile(r"<\?xml\b[^>]*\?>")
_SVG_ENCODING = re.compile(r"""encoding\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_SVG_FORBIDDEN = re.compile(r"<!doctype|<!entity", re.I)


def _bad():
    return IconImportError(ERR_UNREADABLE)


def _svg_text(data: bytes) -> str:
    """UTF-8 text of the file; any other declared encoding, DOCTYPE/ENTITY or PI is rejected."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad() from None
    if chr(0) in text or _SVG_FORBIDDEN.search(text):
        raise _bad()
    m = _SVG_XMLDECL.match(text)
    if m:
        enc = _SVG_ENCODING.search(m.group(0))
        if enc and enc.group(2).strip().lower() != "utf-8":
            raise _bad()
        text = text[m.end():]
    if "<?" in text:                    # processing instruction (xml-stylesheet...) or malformed declaration
        raise _bad()
    return text


def _svg_check_value(name: str, value: str, refs: list) -> None:
    if len(value) > (MAX_SVG_BULK if name in _SVG_BULK_ATTRS else 2048):
        raise _bad()
    if name == "id":
        if _SVG_ID.fullmatch(value) is None:
            raise _bad()
        return
    if _SVG_VALUE_CHARS.fullmatch(value) is None or _SVG_NONFINITE.search(value):
        raise _bad()
    if name in _SVG_URL_ATTRS:
        found = _SVG_URL_REF.findall(value)
        if len(found) != len(_SVG_URL_ANY.findall(value)):
            raise _bad()                # any url() that is not a plain #fragment
        refs.extend((name, ident) for _q, ident in found)
        value = _SVG_URL_REF.sub(" ", value)
    elif _SVG_URL_ANY.search(value):
        raise _bad()
    for num in _SVG_NUM.findall(_SVG_HEX.sub(" ", value)):
        try:
            if not abs(float(num)) <= MAX_SVG_NUMBER:
                raise _bad()
        except (ValueError, OverflowError):
            raise _bad() from None


def _svg_sanitize(text: str) -> bytes:
    """Parse `text` and rebuild a minimal SVG (allow-listed elements/attributes only) as UTF-8 bytes."""
    try:
        root = ET.fromstring(text)      # DOCTYPE/ENTITY already excluded: no entity is ever expanded
    except (ET.ParseError, RecursionError, ValueError):
        raise _bad() from None
    if root.tag != f"{{{_SVG_NS}}}svg":
        raise _bad()
    new_root = ET.Element("svg", {"xmlns": _SVG_NS})   # plain names + one xmlns attribute
    ids: dict = {}
    refs: list = []
    bulk = 0
    count = 0
    stack = [(root, new_root, 1, False)]        # (source, rebuilt, depth, inside clipPath)
    while stack:
        src, dst, depth, in_clip = stack.pop()
        count += 1
        if count > MAX_SVG_ELEMENTS or depth > MAX_SVG_DEPTH:
            raise _bad()
        local = src.tag.rsplit("}", 1)[-1]
        if in_clip and local == "clipPath":
            raise _bad()                # a clip path inside a clip path: could build reference cycles
        for key, value in src.attrib.items():
            if key.startswith("{"):
                if key[1:].split("}", 1)[0] in _SVG_IGNORED_NS:
                    continue
                raise _bad()            # xlink:href, xml:space, unknown namespaces...
            if key not in _SVG_ATTRS or (key == "clip-path" and (in_clip or local == "clipPath")):
                raise _bad()            # style, on*, href, stroke-dasharray, filter, mask...
            _svg_check_value(key, value, refs)
            if key == "id":
                if value in ids:
                    raise _bad()
                ids[value] = local
            elif key in _SVG_BULK_ATTRS:
                bulk += len(value)
                if bulk > MAX_SVG_BULK:
                    raise _bad()
            dst.set(key, value)
        # children (text and tail are never copied)
        kids = []
        for child in src:
            if not isinstance(child.tag, str):
                continue                # comment / PI nodes are not built by the default parser
            if not child.tag.startswith("{"):
                raise _bad()
            ns, _, name = child.tag[1:].partition("}")
            if ns in _SVG_IGNORED_NS:
                continue
            if ns != _SVG_NS or name not in _SVG_ELEMENTS:
                raise _bad()
            kids.append((child, ET.SubElement(dst, name), depth + 1, in_clip or local == "clipPath"))
        stack.extend(reversed(kids))
    for attr, ident in refs:
        if ids.get(ident) not in _SVG_URL_ATTRS[attr]:
            raise _bad()                # missing id, or it points at the wrong kind of element
    return ET.tostring(new_root, encoding="utf-8", xml_declaration=False)


def _svg_render(clean: bytes) -> bytes:
    """Render the sanitised SVG to a 256x256 PNG (no GUI object is used, so it can run off the GUI thread)."""
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(clean))
    if not renderer.isValid():
        raise _bad()
    size = renderer.defaultSize()
    vb = renderer.viewBoxF()
    w, h = size.width(), size.height()
    if w <= 0 or h <= 0 or w > MAX_SVG_DIM or h > MAX_SVG_DIM or vb.width() > MAX_SVG_DIM or vb.height() > MAX_SVG_DIM:
        raise _bad()
    scale = SVG_OUT_PX / max(w, h)
    tw, th = max(1.0, w * scale), max(1.0, h * scale)
    img = QImage(SVG_OUT_PX, SVG_OUT_PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(p, QRectF((SVG_OUT_PX - tw) / 2, (SVG_OUT_PX - th) / 2, tw, th))
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise _bad()
    return bytes(buf.data())


def _svg_to_png(data: bytes) -> bytes:
    """Validate an SVG and return it rendered as a 256x256 transparent PNG (aspect ratio kept).

    The render runs in a daemon thread (QImage/QPainter/QSvgRenderer are safe off the GUI thread) and is
    abandoned after SVG_RENDER_TIMEOUT: the import then fails, while a stuck thread may keep running in the
    background until it finishes (Python cannot kill it). The complexity limits above already bound the cost.
    """
    if len(data) > MAX_SVG_BYTES:
        raise IconImportError(ERR_TOO_BIG)
    clean = _svg_sanitize(_svg_text(data))
    box: dict = {}

    def work():
        try:
            box["png"] = _svg_render(clean)
        except BaseException as exc:    # noqa: BLE001 - handed over to the caller
            box["err"] = exc

    t = threading.Thread(target=work, name="svg-render", daemon=True)
    t.start()
    t.join(SVG_RENDER_TIMEOUT)
    if t.is_alive() or "png" not in box:
        err = box.get("err")
        raise err if isinstance(err, IconImportError) else _bad()
    return box["png"]


def import_image_busy(src) -> str:
    """`import_image` with the wait cursor shown while the image is decoded."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return import_image(src)
    finally:
        QApplication.restoreOverrideCursor()


def import_image(src) -> str:
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in ALLOWED_EXT:
        raise IconImportError(ERR_EXTENSION)
    limit = MAX_SVG_BYTES if ext == ".svg" else MAX_BYTES
    try:
        if src.stat().st_size > limit:
            raise IconImportError(ERR_TOO_BIG)
        data = src.read_bytes()
    except IconImportError:
        raise
    except OSError:
        raise IconImportError(ERR_IO) from None
    if len(data) > limit:
        raise IconImportError(ERR_TOO_BIG)
    if ext == ".svg":
        data = _svg_to_png(data)        # from here on it is an ordinary PNG
        ext = ".png"
    else:
        anim = _animation_from(data)    # GIF / WebP with several frames -> strip + timings
        if anim is not None:
            return _store_animation(*anim)
        reader, _buf = _open_reader(data)       # (the buffer must outlive the reader)
        fmt = _format_of(reader)
        if fmt == "webp":                       # a still WebP is stored as a PNG
            data = _png_bytes(_decode(data, ERR_UNREADABLE, {"webp"}))
            ext = ".png"
        elif fmt in _ALLOWED_FORMATS:
            # The stored extension always follows the real content, not the name the user gave it.
            if not (fmt in ("jpg", "jpeg") and ext in (".jpg", ".jpeg")):
                ext = "." + fmt
    _decode(data, ERR_UNREADABLE)
    name = hashlib.sha256(data).hexdigest()[:16] + ext
    if not safe_name(name):
        raise IconImportError(ERR_UNREADABLE)
    try:
        d = icons_dir()
        d.mkdir(parents=True, exist_ok=True)
        _write_new(d, name, data)
    except OSError:
        raise IconImportError(ERR_IO) from None
    return name


def _write_new(d: Path, name: str, data: bytes) -> None:
    """Write `data` as d/name unless it exists: unique temp file in the same directory (no fixed name to
    collide on), flushed to disk, then atomically moved into place. The exact bytes that were validated."""
    dest = d / name
    if dest.exists():
        return
    fd, tmp_name = tempfile.mkstemp(dir=d, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise IconImportError(ERR_UNREADABLE)
    return bytes(buf.data())


# --- animated icons --------------------------------------------------------------------------------------
def _fit_frame(img: QImage) -> QImage:
    """`img` fitted (aspect ratio kept) and centred in a transparent 64x64 square."""
    n = ANIM_FRAME_PX
    scaled = img.scaled(n, n, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    p.drawImage((n - scaled.width()) // 2, (n - scaled.height()) // 2, scaled)
    p.end()
    return out


def _plan_frames(delays: list[int]) -> tuple[list[int], list[int]]:
    """(source index kept, delay) lists for at most MAX_ANIM_FRAMES frames.

    Above the limit the frames are picked uniformly and each one lasts as long as the source frames it
    stands for (so the loop keeps its duration); every delay ends up inside [MIN_FRAME_MS, MAX_FRAME_MS].
    """
    n = len(delays)
    if n <= MAX_ANIM_FRAMES:
        return list(range(n)), list(delays)
    idx = [i * n // MAX_ANIM_FRAMES for i in range(MAX_ANIM_FRAMES)]
    ends = idx[1:] + [n]
    return idx, [max(MIN_FRAME_MS, min(MAX_FRAME_MS, sum(delays[a:b]))) for a, b in zip(idx, ends)]


def _animation_from(data: bytes):
    """(frames, delays) of an animated GIF / WebP, or None when `data` is not an animation (one frame or a
    format without animation). Frames are 64x64 QImages, delays in ms. Limits are checked before decoding
    and while reading; anything over them raises IconImportError."""
    reader, buf = _open_reader(data)
    if not reader.canRead() or _format_of(reader) not in _ANIM_FORMATS or not reader.supportsAnimation():
        return None
    count = reader.imageCount()
    if count == 1:
        return None
    if count > MAX_ANIM_READ:
        raise IconImportError(ERR_ANIM_LONG)
    size = reader.size()
    if size.isValid():
        if size.width() > MAX_DIM or size.height() > MAX_DIM:
            raise IconImportError(ERR_DIMENSIONS)
        if size.width() * size.height() * (count if count > 0 else MAX_ANIM_READ) > MAX_ANIM_PIXELS:
            raise IconImportError(ERR_DIMENSIONS)
    started = time.monotonic()
    frames: list[QImage] = []
    delays: list[int] = []
    while reader.canRead():
        if len(frames) >= MAX_ANIM_READ:
            raise IconImportError(ERR_ANIM_LONG)
        img = reader.read()
        if img.isNull():
            break
        if img.width() > MAX_DIM or img.height() > MAX_DIM:
            raise IconImportError(ERR_DIMENSIONS)
        if not frames:      # the size of a format that does not announce it is known only now
            if img.width() * img.height() * (count if count > 0 else MAX_ANIM_READ) > MAX_ANIM_PIXELS:
                raise IconImportError(ERR_DIMENSIONS)
        if time.monotonic() - started > ANIM_READ_TIMEOUT:
            raise IconImportError(ERR_UNREADABLE)
        delays.append(max(MIN_FRAME_MS, min(MAX_FRAME_MS, int(reader.nextImageDelay()))))
        frames.append(_fit_frame(img))
    if not frames:
        raise IconImportError(ERR_UNREADABLE)
    if len(frames) == 1:
        return None
    if sum(delays) > MAX_ANIM_TOTAL_MS:
        raise IconImportError(ERR_ANIM_LONG)
    keep, kept_delays = _plan_frames(delays)
    return [frames[i] for i in keep], kept_delays


def _store_animation(frames: list[QImage], delays: list[int]) -> str:
    """Write the strip (64*N x 64 PNG) and its timings; returns the ``.anim.png`` name."""
    n = len(frames)
    sheet = QImage(ANIM_FRAME_PX * n, ANIM_FRAME_PX, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(0, 0, 0, 0))
    p = QPainter(sheet)
    for i, fr in enumerate(frames):
        p.drawImage(i * ANIM_FRAME_PX, 0, fr)
    p.end()
    png = _png_bytes(sheet)
    meta = json.dumps({"v": 1, "delays": delays}, separators=(",", ":")).encode("ascii")
    name = hashlib.sha256(png + b"\0" + meta).hexdigest()[:16] + ".anim.png"
    try:
        d = icons_dir()
        d.mkdir(parents=True, exist_ok=True)
        _write_new(d, sidecar_name(name), meta)     # timings first: a strip is never visible without them
        _write_new(d, name, png)
    except OSError:
        raise IconImportError(ERR_IO) from None
    return name


class _Anim:
    """A validated, decoded strip: the sheet plus cumulative end times of its frames."""
    __slots__ = ("sheet", "delays", "cum", "total")

    def __init__(self, sheet: QImage, delays: list[int]):
        self.sheet = sheet
        self.delays = delays
        self.cum = []
        t = 0
        for d in delays:
            t += d
            self.cum.append(t)
        self.total = t

    @property
    def count(self) -> int:
        return len(self.delays)


def _valid_delays(obj) -> list[int] | None:
    if not isinstance(obj, dict) or set(obj) != {"v", "delays"} or obj["v"] != 1 or isinstance(obj["v"], bool):
        return None
    d = obj["delays"]
    if not isinstance(d, list) or not 2 <= len(d) <= MAX_ANIM_FRAMES:
        return None
    for v in d:
        if not isinstance(v, int) or isinstance(v, bool) or not MIN_FRAME_MS <= v <= MAX_FRAME_MS:
            return None
    if sum(d) > MAX_ANIM_TOTAL_MS:
        return None
    return list(d)


def _read_anim(filename: str) -> _Anim | None:
    """Load and validate a strip + sidecar; None when anything is wrong (missing, odd size, bad timings)."""
    d = icons_dir()
    try:
        side = d / sidecar_name(filename)
        if side.stat().st_size > MAX_ANIM_JSON or (d / filename).stat().st_size > MAX_BYTES:
            return None
        delays = _valid_delays(json.loads(side.read_bytes().decode("ascii")))
        if delays is None:
            return None
        reader, buf = _open_reader((d / filename).read_bytes())
        n = len(delays)
        if not reader.canRead() or _format_of(reader) != "png":
            return None
        if reader.size() != QSize(ANIM_FRAME_PX * n, ANIM_FRAME_PX):
            return None
        sheet = reader.read()
        if sheet.isNull() or sheet.width() != ANIM_FRAME_PX * n or sheet.height() != ANIM_FRAME_PX:
            return None
        return _Anim(sheet.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), delays)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# stamp (png mtime, sidecar mtime) -> decoded strip
_anims: OrderedDict = OrderedDict()
# (filename, size_px, dpr, stamp) -> list of scaled frames (None = not built yet)
_anim_px: OrderedDict = OrderedDict()


def _stamp(filename: str):
    """Change marker of an icon file: its mtime, or (png, sidecar) mtimes for an animation."""
    d = icons_dir()
    mt = _mtime(d / filename)
    if mt is None or not is_animated(filename):
        return mt
    side = _mtime(d / sidecar_name(filename))
    return None if side is None else (mt, side)


def _anim_for(filename: str) -> _Anim | None:
    stamp = _stamp(filename)
    if stamp is None:
        return None
    hit = _anims.get(filename)
    if hit is not None and hit[0] == stamp:
        _anims.move_to_end(filename)
        return hit[1]
    if _failed.get(filename) == stamp:
        return None
    anim = _read_anim(filename)
    if anim is None:
        _failed[filename] = stamp
        _anims.pop(filename, None)
        return None
    _failed.pop(filename, None)
    _anims[filename] = (stamp, anim)
    while len(_anims) > _ANIM_CACHE_MAX:
        _anims.popitem(last=False)
    return anim


def frame_index(anim: _Anim, now_ms: int | None) -> int:
    """Index of the frame shown at `now_ms` (None = paused: the first one)."""
    if now_ms is None or anim.total <= 0:
        return 0
    return bisect.bisect_right(anim.cum, int(now_ms) % anim.total)


def ms_to_next_frame(filename: str, now_ms: int) -> int | None:
    """Milliseconds until the frame shown at `now_ms` ends; None when `filename` is not a usable animation."""
    try:
        anim = _anim_for(filename) if is_animated(filename) else None
        if anim is None:
            return None
        t = int(now_ms) % anim.total
        return max(1, anim.cum[bisect.bisect_right(anim.cum, t)] - t)
    except Exception:
        return None


def frame_for(filename: str, now_ms: int | None, size_px: int, dpr: float = 1.0) -> QPixmap | None:
    """The pixmap to draw for `filename` at time `now_ms`. A still image ignores the time; an animation shows
    the frame due at that time, or its first frame when `now_ms` is None (paused). Never raises."""
    try:
        if not is_animated(filename):
            return pixmap_for(filename, size_px, dpr)
        return _anim_frame(filename, now_ms, int(size_px), dpr)
    except Exception:
        return None


def _anim_frame(filename: str, now_ms: int | None, size_px: int, dpr: float) -> QPixmap | None:
    dpr = float(dpr) if dpr and dpr > 0 else 1.0
    if size_px <= 0:
        return None
    anim = _anim_for(filename)
    if anim is None:
        return None
    stamp = _stamp(filename)
    px = max(1, int(round(size_px * dpr)))
    key = (filename, px, round(dpr, 3), stamp)
    frames = _anim_px.get(key)
    if frames is None:
        for k in [k for k in _anim_px if k[0] == filename and k[3] != stamp]:
            del _anim_px[k]
        frames = _anim_px[key] = [None] * anim.count
        while len(_anim_px) > _ANIM_PX_CACHE_MAX:
            _anim_px.popitem(last=False)
    else:
        _anim_px.move_to_end(key)
    i = frame_index(anim, now_ms)
    pm = frames[i]
    if pm is None:
        cell = anim.sheet.copy(i * ANIM_FRAME_PX, 0, ANIM_FRAME_PX, ANIM_FRAME_PX)
        pm = QPixmap.fromImage(cell.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
        frames[i] = pm
    return pm


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


def device_file(key: str) -> str | None:
    """Image file name set for this device itself (not inherited from its kind), or None."""
    try:
        v = _by_device.get(key)
    except TypeError:
        return None
    return v if safe_name(v) else None


def file_for(kind: str, key: str) -> str | None:
    """Image file name to draw for a device (device > kind), or None for the built-in icon."""
    return resolve(kind, key, _by_kind, _by_device)


# (filename, size_px, dpr) -> QPixmap ; failures: filename -> mtime_ns at failure
_cache: OrderedDict = OrderedDict()
_failed: dict = {}
_usable: dict = {}


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def clear_cache() -> None:
    _cache.clear()
    _failed.clear()
    _usable.clear()
    _anims.clear()
    _anim_px.clear()


def usable(filename: str) -> bool:
    """Cheap check (one stat, no pixmap cached) that the file exists and decodes as an allowed image."""
    try:
        if not safe_name(filename):
            return False
        if is_animated(filename):
            return _anim_for(filename) is not None
        path = icons_dir() / filename
        mt = _mtime(path)
        if mt is None:
            return False
        if _failed.get(filename) == mt:
            return False
        if _usable.get(filename) == mt:
            return True
        try:
            if path.stat().st_size > MAX_BYTES:
                raise IconImportError(ERR_TOO_BIG)
            _decode(path.read_bytes(), ERR_UNREADABLE)
        except (IconImportError, OSError):
            _failed[filename] = mt
            return False
        _usable[filename] = mt
        return True
    except Exception:
        return False


def pixmap_for(filename: str, size_px: int, dpr: float) -> QPixmap | None:
    try:
        if not safe_name(filename):
            return None
        size_px = int(size_px)
        dpr = float(dpr) if dpr and dpr > 0 else 1.0
        if size_px <= 0:
            return None
        if is_animated(filename):       # a still request for an animation: its first frame
            return _anim_frame(filename, None, size_px, dpr)
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
        try:
            img = _decode(path.read_bytes() if path.stat().st_size <= MAX_BYTES else b"", ERR_UNREADABLE)
        except (IconImportError, OSError):
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


def draw_custom_icon(painter: QPainter, rect, filename: str, opacity: float = 1.0,
                     now_ms: int | None = None) -> bool:
    """Draw the icon in `rect`. `now_ms` picks the frame of an animation (None = paused: first frame)."""
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
        px = frame_for(filename, now_ms, int(round(side)), dpr)
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
    """Delete our own image files not in `used`, plus stale ``*.tmp`` leftovers; nothing else."""
    try:
        d = icons_dir()
        if not d.is_dir():
            return
        keep = {u for u in used if isinstance(u, str)}
        now = time.time()
        for f in d.iterdir():
            try:
                if not f.is_file():
                    continue
                if safe_name(f.name):
                    doomed = f.name not in keep
                elif _SAFE_SIDECAR.fullmatch(f.name):       # timings go with their strip
                    doomed = f.name[:-len(".json")] + ".png" not in keep
                elif f.suffix == ".tmp":
                    doomed = now - f.stat().st_mtime > _TMP_MAX_AGE
                else:
                    doomed = False
                if doomed:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass
