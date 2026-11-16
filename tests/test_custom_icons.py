import os
import time

import pytest
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QFileDialog, QMessageBox

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget.custom_icons_dialog import CustomIconsDialog


@pytest.fixture(autouse=True)
def _icons(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    yield
    ci.clear_cache()


def make_png(path, w=32, h=16, color=QColor("red")):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(color)
    assert img.save(str(path))
    return path


def test_import_valid_and_dedup(tmp_path):
    a = make_png(tmp_path / "a.png")
    b = make_png(tmp_path / "b.png")
    n1 = ci.import_image(a)
    n2 = ci.import_image(b)
    assert n1 == n2 and ci.safe_name(n1) and n1.endswith(".png")
    assert (ci.icons_dir() / n1).is_file()
    assert len(list(ci.icons_dir().iterdir())) == 1


def test_reject_extension(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_EXTENSION


def test_reject_size(tmp_path):
    f = tmp_path / "big.png"
    f.write_bytes(b"\0" * (ci.MAX_BYTES + 1))
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_TOO_BIG


def test_reject_dimensions(tmp_path):
    f = make_png(tmp_path / "wide.png", 2049, 4)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_DIMENSIONS


def test_reject_corrupt(tmp_path):
    f = tmp_path / "bad.png"
    f.write_bytes(b"not an image at all")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_UNREADABLE


def test_reject_missing(tmp_path):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(tmp_path / "nope.png")
    assert e.value.code == ci.ERR_IO


def test_resolve_priority_and_unsafe():
    good1, good2 = "0123456789abcdef.png", "fedcba9876543210.jpg"
    assert ci.resolve("mouse", "k", {"mouse": good1}, {"k": good2}) == good2
    assert ci.resolve("mouse", "k", {"mouse": good1}, {}) == good1
    assert ci.resolve("mouse", "k", {}, {}) is None
    assert ci.resolve("mouse", "k", {"mouse": good1}, {"k": "..\\x.png"}) == good1
    assert ci.resolve("mouse", "k", {"mouse": "C:\\x\\0123456789abcdef.png"}, {}) is None
    assert ci.resolve("mouse", "k", {"mouse": "../0123456789abcdef.png"}, {"k": 5}) is None


def test_pixmap_for_ok_square_and_missing(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png", 40, 20))
    px = ci.pixmap_for(name, 16, 2.0)
    assert px is not None and px.width() == 32 and px.height() == 32
    (ci.icons_dir() / name).unlink()
    assert ci.pixmap_for("0123456789abcdef.png", 16, 1.0) is None
    assert ci.pixmap_for("../evil.png", 16, 1.0) is None


def test_failure_cache_and_mtime_invalidation():
    d = ci.icons_dir()
    d.mkdir(parents=True)
    name = "0123456789abcdef.png"
    f = d / name
    f.write_bytes(b"garbage")
    assert ci.pixmap_for(name, 16, 1.0) is None
    # fix the file without changing mtime -> still cached failure
    st = f.stat()
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor("blue"))
    img.save(str(f))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert ci.pixmap_for(name, 16, 1.0) is None
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert ci.pixmap_for(name, 16, 1.0) is not None


def test_cache_bounded(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png"))
    for s in range(1, ci._CACHE_MAX + 20):
        ci.pixmap_for(name, s, 1.0)
    assert len(ci._cache) <= ci._CACHE_MAX


def test_draw_custom_icon(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    pm = QPixmap(24, 24)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    assert ci.draw_custom_icon(p, QRectF(0, 0, 24, 24), name, 0.5) is True
    assert not p.opacity() < 1.0  # state restored
    assert ci.draw_custom_icon(p, QRectF(0, 0, 24, 24), "0123456789abcdef.png") is False
    p.end()
    assert pm.toImage().pixelColor(12, 12) != QColor("white")


def test_cleanup_unused(tmp_path):
    n1 = ci.import_image(make_png(tmp_path / "a.png", color=QColor("red")))
    n2 = ci.import_image(make_png(tmp_path / "b.png", color=QColor("green")))
    assert n1 != n2
    ci.cleanup_unused({n1})
    assert [f.name for f in ci.icons_dir().iterdir()] == [n1]
    ci.cleanup_unused(set())
    assert list(ci.icons_dir().iterdir()) == []


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'


def test_reject_svg_disguised_as_png(tmp_path):
    f = tmp_path / "evil.png"
    f.write_bytes(_SVG)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code in (ci.ERR_EXTENSION, ci.ERR_UNREADABLE)
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_reject_tiff_disguised_as_png(tmp_path):
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor("red"))
    f = tmp_path / "t.tiff"
    if not img.save(str(f), "TIFF"):
        pytest.skip("no TIFF writer")
    g = tmp_path / "t.png"
    g.write_bytes(f.read_bytes())
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(g)
    assert e.value.code in (ci.ERR_EXTENSION, ci.ERR_UNREADABLE)


def test_pixmap_for_rejects_svg_content():
    d = ci.icons_dir()
    d.mkdir(parents=True)
    name = "0123456789abcdef.png"
    (d / name).write_bytes(_SVG)
    assert ci.pixmap_for(name, 16, 1.0) is None
    assert ci.usable(name) is False


def test_safe_name_rejects_trailing_newline():
    assert ci.safe_name("0123456789abcdef.png")
    assert not ci.safe_name("0123456789abcdef.png\n")


def test_cleanup_leaves_foreign_files(tmp_path):
    n1 = ci.import_image(make_png(tmp_path / "a.png"))
    d = ci.icons_dir()
    (d / "notes.txt").write_text("mine")
    (d / "photo.png").write_bytes(b"x")
    (d / "sub").mkdir()
    fresh = d / "aaaaaaaaaaaaaaaa.png.tmp"
    fresh.write_bytes(b"x")
    old = d / "bbbbbbbbbbbbbbbb.png.tmp"
    old.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    ci.cleanup_unused(set())
    names = {f.name for f in d.iterdir()}
    assert names == {"notes.txt", "photo.png", "sub", fresh.name}
    assert n1 not in names


def _pick(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))


def test_dialog_choose_remove_cancel(tmp_path, monkeypatch):
    png = make_png(tmp_path / "a.png")
    _pick(monkeypatch, png)
    dlg = CustomIconsDialog({}, {}, [("devkey", "My Mouse", "mouse")], tr=lambda c: c)
    kind_rows = [r for r in dlg._rows if r[0] is dlg._by_kind]
    dev_rows = [r for r in dlg._rows if r[0] is dlg._by_device]
    assert len(dev_rows) == 1
    # choose for first kind
    dlg._choose(*kind_rows[0])
    by_kind, by_dev = dlg.result_maps()
    assert list(by_kind) == [kind_rows[0][1]] and by_dev == {}
    name = by_kind[kind_rows[0][1]]
    assert ci.safe_name(name)
    # choose for device
    dlg._choose(*dev_rows[0])
    assert dlg.result_maps()[1] == {"devkey": name}
    # remove
    dlg._remove(*kind_rows[0])
    assert dlg.result_maps()[0] == {}
    # cancel keeps caller's maps untouched
    orig_kind = {"mouse": name}
    dlg2 = CustomIconsDialog(orig_kind, {}, [], tr=lambda c: c)
    r = [r for r in dlg2._rows if r[1] == "mouse"][0]
    dlg2._remove(*r)
    dlg2.reject()
    assert orig_kind == {"mouse": name}


def test_dialog_error_shows_message(tmp_path, monkeypatch):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"xx")
    _pick(monkeypatch, bad)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = CustomIconsDialog({}, {}, [], tr=lambda c: "T:" + c)
    dlg._choose(*dlg._rows[0])
    assert shown == ["T:" + ci.ERR_UNREADABLE]
    assert dlg.result_maps() == ({}, {})


def test_dialog_ignores_unsafe_input():
    dlg = CustomIconsDialog({"mouse": "..\\x.png"}, {"k": "/abs.png"}, [], tr=lambda c: c)
    assert dlg.result_maps() == ({}, {})


# ---------------------------------------------------------------- SVG import (rendered once to PNG)
_NS = 'xmlns="http://www.w3.org/2000/svg"'
_GOOD = f'<svg {_NS} viewBox="0 0 100 50" width="100" height="50"><rect x="0" y="0" width="100" height="50" fill="red"/></svg>'


def _svg(tmp_path, text, name="i.svg"):
    f = tmp_path / name
    f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return f


def test_svg_imports_as_256_png_with_transparency(tmp_path):
    n = ci.import_image(_svg(tmp_path, _GOOD))
    assert ci.safe_name(n) and n.endswith(".png")
    assert [f.name for f in ci.icons_dir().iterdir()] == [n]          # only the PNG is stored
    img = QImage(str(ci.icons_dir() / n))
    assert (img.width(), img.height()) == (256, 256) and img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0                            # 2:1 shape: transparent top margin
    assert img.pixelColor(128, 128).alpha() == 255
    assert ci.usable(n) and ci.pixmap_for(n, 16, 1.0) is not None
    import hashlib
    assert n == hashlib.sha256((ci.icons_dir() / n).read_bytes()).hexdigest()[:16] + ".png"


@pytest.mark.parametrize("text", [
    f'<!DOCTYPE svg [<!ENTITY a "x">]><svg {_NS} width="8" height="8"/>',
    f'<!DOCTYPE svg><svg {_NS} width="8" height="8"/>',
    f'<svg {_NS} width="8" height="8"><!ENTITY a "x"></svg>',
    f'<svg {_NS} width="8" height="8"><script>alert(1)</script></svg>',
    f'<svg {_NS} width="8" height="8"><foreignObject width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8" onload="x()"><rect width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8"><rect onclick = "x()" width="8" height="8"/></svg>',
    f'<svg {_NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><image xlink:href="http://e.com/a.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="file:///C:/x.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="C:\\x.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href=x.png/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="data:image/svg+xml;base64,AAAA"/></svg>',
    f'<svg {_NS} width="8" height="8"><rect style="fill:url(http://e.com/x)" width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8"><style>@import "x.css";</style></svg>',
    "<svg",                                                             # not well formed
    "not xml at all",
    f'<html {_NS}><body/></html>',
    f'<svg {_NS} width="9999" height="9999"><rect width="9" height="9"/></svg>',   # absurd size
    f'<svg {_NS} width="0" height="0"/>',
])
def test_svg_rejected(tmp_path, text):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, text))
    assert e.value.code in (ci.ERR_UNREADABLE,)
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def _wrap(body, attrs='width="16" height="16"'):
    return f'<svg {_NS} {attrs}>{body}</svg>'


_BAD_SVGS = [
    # declared encodings other than UTF-8 (the UTF-7 bypass: Qt would decode what Python never validated)
    '<?xml version="1.0" encoding="UTF-7"?>' + _wrap(
        '<desc>+ADw-image xlink:href=+ACI-C:/Windows/Web/x.png+ACI-/+AD4-</desc><rect width="8" height="8"/>'),
    '<?xml version="1.0" encoding="ISO-8859-1"?>' + _wrap('<rect width="8" height="8"/>'),
    "<?xml version='1.0' encoding='utf-16'?>" + _wrap('<rect width="8" height="8"/>'),
    '<?xml version="1.0"?><?xml-stylesheet href="x.css"?>' + _wrap('<rect width="8" height="8"/>'),
    # elements outside the allow-list
    _wrap('<defs><rect id="r" width="8" height="8"/></defs><use href="#r"/>'),
    _wrap('<image width="4" height="4"/>'),
    _wrap('<pattern id="p" width="1" height="1"><rect width="1" height="1"/></pattern><rect fill="url(#p)" width="8" height="8"/>'),
    _wrap('<filter id="f"><feGaussianBlur stdDeviation="9999"/></filter><rect filter="url(#f)" width="8" height="8"/>'),
    _wrap('<style>rect{fill:red}</style><rect width="8" height="8"/>'),
    _wrap('<text x="1" y="8">hi</text>'),
    _wrap('<a><rect width="8" height="8"/></a>'),
    _wrap('<mask id="m"><rect width="8" height="8"/></mask>'),
    _wrap('<symbol id="s"><rect width="8" height="8"/></symbol>'),
    _wrap('<switch><rect width="8" height="8"/></switch>'),
    _wrap('<rect width="8" height="8"><animate attributeName="x" to="3"/></rect>'),
    '<svg width="8" height="8"><rect width="8" height="8"/></svg>',                  # no SVG namespace
    _wrap('<rect xmlns="http://example.com/x" width="8" height="8"/>'),            # foreign namespace element
    # attributes outside the allow-list
    _wrap('<rect style="fill:red" width="8" height="8"/>'),
    _wrap('<rect onclick="x()" width="8" height="8"/>'),
    _wrap('<rect onmouseover="x()" width="8" height="8"/>'),
    _wrap('<rect href="#a" width="8" height="8"/>'),
    _wrap('<rect stroke="red" stroke-dasharray="0.001" width="8" height="8"/>'),
    _wrap('<rect filter="url(#f)" width="8" height="8"/>'),
    _wrap('<rect xml:space="preserve" width="8" height="8"/>'),
    f'<svg {_NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><rect xlink:href="C:/a.png" width="8" height="8"/></svg>',
    # url() values
    _wrap('<rect fill="url(#nope)" width="8" height="8"/>'),
    _wrap('<rect fill="url(http://e.com/x)" width="8" height="8"/>'),
    _wrap('<rect fill="url(C:/a.png)" width="8" height="8"/>'),
    _wrap('<linearGradient id="a"/><rect fill="url(#a) url(file:///x)" width="8" height="8"/>'),
    _wrap('<linearGradient id="a"/><rect opacity="url(#a)" width="8" height="8"/>'),
    _wrap('<linearGradient id="g"/><rect fill="url(#g)" clip-path="url(#g)" width="8" height="8"/>'),   # wrong kind
    _wrap('<clipPath id="c"><rect clip-path="url(#c)" width="8" height="8"/></clipPath>'),            # reference cycle
    _wrap('<clipPath id="c" clip-path="url(#c)"/>'),
    _wrap('<clipPath id="c"><clipPath id="d"/></clipPath>'),
    _wrap('<rect id="a" width="1" height="1"/><rect id="a" width="1" height="1"/>'),                  # duplicate id
    # complexity and absurd numbers
    _wrap("<g>" * 40 + '<rect width="8" height="8"/>' + "</g>" * 40),
    _wrap('<rect width="1" height="1"/>' * 2500),
    _wrap('<rect width="1e300" height="8"/>'),
    _wrap('<rect width="8" height="8" transform="scale(1e300)"/>'),
    _wrap('<path d="M0 0 L1e300 5"/>'),
    _wrap('<polygon points="0,0 1e7,5 3,4"/>'),
    _wrap('<rect width="8" height="8" opacity="nan"/>'),
    _wrap('<rect width="inf" height="8"/>'),
    _wrap('<path d="' + "M0 0L1 1" * 30000 + '"/>'),                                                   # > 200 KB of path data
]


@pytest.mark.parametrize("text", _BAD_SVGS)
def test_svg_sanitiser_rejects(tmp_path, text):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, text))
    assert e.value.code == ci.ERR_UNREADABLE
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


_GOOD_SVGS = [
    _wrap('<path d="M2 2 L14 2 L14 14 Z" fill="#1e90ff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/>'),
    _wrap('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1" gradientUnits="objectBoundingBox">'
          '<stop offset="0" stop-color="#ff0000"/><stop offset="1" stop-color="rgb(0,0,255)" stop-opacity="0.5"/>'
          '</linearGradient></defs><rect width="16" height="16" rx="3" fill="url(#g)"/>'),
    _wrap('<defs><radialGradient id="r" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="white"/>'
          '<stop offset="1" stop-color="green"/></radialGradient></defs><circle cx="8" cy="8" r="7" fill="url(#r)"/>'),
    _wrap('<defs><clipPath id="c"><circle cx="8" cy="8" r="6"/></clipPath></defs>'
          '<g clip-path="url(#c)" transform="translate(1 1) rotate(10 8 8)"><rect width="16" height="16" fill="red"/>'
          '<ellipse cx="8" cy="8" rx="5" ry="3" fill="none" stroke="blue"/><line x1="0" y1="0" x2="16" y2="16" stroke="black"/>'
          '<polyline points="1,1 5,9 9,1" fill="none" stroke="black"/><polygon points="2,2 8,2 5,8" opacity=".5"/></g>'),
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?><!-- c -->' + _wrap(
        '<title>t</title><desc>d</desc><rect width="16" height="16" fill="red"/>',
        'width="16" height="16" viewBox="0 0 16 16" version="1.1"'),
]


@pytest.mark.parametrize("text", _GOOD_SVGS)
def test_svg_sanitiser_accepts_typical_icons(tmp_path, text):
    n = ci.import_image(_svg(tmp_path, text))
    img = QImage(str(ci.icons_dir() / n))
    assert (img.width(), img.height()) == (256, 256)
    assert any(img.pixelColor(x, y).alpha() for x in range(0, 256, 16) for y in range(0, 256, 16))   # not empty


def test_svg_sanitiser_output_is_rebuilt():
    text = ('\ufeff<?xml version="1.0" encoding="utf-8"?><svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" inkscape:version="1" width="8" height="8">'
            '<!-- note --><desc>+ADw-script+AD4-</desc><rect id="a" width="8" height="8" inkscape:label="x"/></svg>')
    out = ci._svg_sanitize(ci._svg_text(text.encode("utf-8")))
    assert out == b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><desc /><rect id="a" width="8" height="8" /></svg>'


def test_svg_render_timeout_is_an_import_error(tmp_path, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(ci, "_svg_render", lambda clean: gate.wait(5) or b"")
    monkeypatch.setattr(ci, "SVG_RENDER_TIMEOUT", 0.1)
    try:
        with pytest.raises(ci.IconImportError) as e:
            ci.import_image(_svg(tmp_path, _GOOD))
        assert e.value.code == ci.ERR_UNREADABLE
    finally:
        gate.set()


def test_import_uses_unique_temp_files(tmp_path):
    import hashlib
    d = ci.icons_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "a.png"
    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(QColor("blue"))
    img.save(str(f))
    name = hashlib.sha256(f.read_bytes()).hexdigest()[:16] + ".png"
    (d / (name + ".tmp")).write_bytes(b"stale")          # the old fixed temp name must not matter
    assert ci.import_image(f) == name
    assert sorted(p.name for p in d.iterdir()) == [name, name + ".tmp"]      # no leftover of our own


def test_svg_too_big_and_svgz(tmp_path):
    big = _GOOD.replace("</svg>", "<!--" + "a" * (ci.MAX_SVG_BYTES + 10) + "--></svg>")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, big))
    assert e.value.code == ci.ERR_TOO_BIG
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, _GOOD, "i.svgz"))
    assert e.value.code == ci.ERR_EXTENSION


def test_svg_binary_garbage_and_bad_encoding(tmp_path):
    for blob in (b"\xff\xfe\x00\x01garbage", b"\x00" * 100, b""):
        with pytest.raises(ci.IconImportError):
            ci.import_image(_svg(tmp_path, blob))


def test_image_filter_lists_svg():
    from taskbar_battery_widget.custom_icons_dialog import IMAGE_FILTER
    assert "*.svg" in IMAGE_FILTER
