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
