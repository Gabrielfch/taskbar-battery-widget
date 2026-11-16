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


def test_svg_allows_fragment_hrefs_and_embedded_png(tmp_path):
    png = tmp_path / "e.png"
    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(QColor("blue"))
    img.save(str(png))
    import base64
    b64 = base64.b64encode(png.read_bytes()).decode()
    text = (f'<svg {_NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="16" height="16">'
            f'<defs><rect id="r" width="8" height="8" fill="url(#g)"/></defs><use href="#r"/>'
            f'<image xlink:href="data:image/png;base64,{b64}" width="4" height="4"/></svg>')
    assert ci.safe_name(ci.import_image(_svg(tmp_path, text)))


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
