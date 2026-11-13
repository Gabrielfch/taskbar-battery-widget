import dataclasses

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from taskbar_battery_widget import icons
from taskbar_battery_widget.colors import (TASKBAR_BASE, band_color, contrast_ratio, pill_backgrounds,
                                           row_style, section_header_color)
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model


def dev(i, name="Dev", battery=50, connected=True, **kw):
    return Device(id=i, name=name, kind="mouse", battery=battery, connected=connected, **kw)


def build(devs, hidden=(), **kw):
    kw.setdefault("show_disconnected", True)
    kw.setdefault("threshold", 20)
    kw.setdefault("dark", True)
    return build_panel_model(devs, hidden, lang="en", **kw)


def test_grouping_and_order():
    a, b, c, d = dev("a"), dev("b", connected=False), dev("c", battery=40, stale=True, connected=False), dev("d")
    m = build([a, b, c, d])
    assert [r.device_id for r in m.connected] == ["a", "d"]
    assert [r.device_id for r in m.known] == ["b", "c"]


def test_visible_flag_and_key():
    m = build([dev("a"), dev("b")], hidden=[visibility_key("b")])
    assert [(r.device_id, r.visible) for r in m.connected] == [("a", True), ("b", False)]
    assert m.connected[0].key == visibility_key("a")


def test_status_text_en_es():
    assert build([dev("a", battery=10)]).connected[0].status_text == "Low"
    assert build([dev("a", charging=True)]).connected[0].status_text == "Charging"
    assert build([dev("a", connected=False, battery=None)]).known[0].status_text == "Disconnected"
    m = build_panel_model([dev("a", battery=None)], [], True, 20, True, lang="es")
    assert m.connected[0].status_text == "Batería desconocida"


def test_signature_changes_only_with_relevant_data():
    base = build([dev("a"), dev("b", connected=False)])
    assert build([dev("a"), dev("b", connected=False)]).signature == base.signature
    assert build([dev("a", address="zz"), dev("b", connected=False)]).signature == base.signature
    assert build([dev("a", battery=51), dev("b", connected=False)]).signature != base.signature
    assert build([dev("a"), dev("b", connected=False)], hidden=[visibility_key("a")]).signature != base.signature
    assert build([dev("a"), dev("b", connected=False)], show_disconnected=False).signature != base.signature
    assert build([dev("a"), dev("b")]).signature != base.signature


def test_row_style_states():
    s = row_style(dev("a", battery=80), 20, True)
    assert (s.band, s.cues, s.dimmed, s.alert) == ("green", (), False, False)
    assert row_style(dev("a", battery=45), 20, True).band == "amber"
    o = row_style(dev("a", battery=25), 20, True)
    assert (o.band, o.cues) == ("orange", ("warn",))
    r = row_style(dev("a", battery=10), 20, False)
    assert (r.band, r.cues, r.alert) == ("red", ("warn", "alert"), True)
    c = row_style(dev("a", battery=5, charging=True), 20, True)
    assert (c.band, c.cues, c.alert) == ("green", ("charging",), False)
    i = row_style(dev("a", battery=70, idle=True), 20, True)
    assert i.cues == ("idle",) and i.band == "green"
    u = row_style(dev("a", battery=None), 20, True)
    assert (u.band, u.cues, u.dimmed) == ("grey", ("unknown",), False)
    st = row_style(dev("a", battery=60, connected=False, stale=True), 20, True)
    assert (st.band, st.dimmed, "stale" in st.cues) == ("grey", True, True)
    dc = row_style(dev("a", battery=None, connected=False), 20, True)
    assert (dc.band, dc.dimmed, dc.cues) == ("grey", True, ("disconnected",))


def test_row_style_mono():
    s = row_style(dev("a", battery=80), 20, True, color_by_level=False)
    assert s.accent is None and s.fg == QColor(255, 255, 255)
    r = row_style(dev("a", battery=10), 20, False, color_by_level=False)
    assert r.fg == band_color("red", False) and r.alert


def test_section_header_colors():
    assert section_header_color("connected", True) == band_color("green", True)
    assert section_header_color("known", False) == band_color("grey", False)


@pytest.mark.parametrize("dark", [True, False])
def test_contrast_all_bands(dark):
    bases = [QColor(*TASKBAR_BASE[dark])] + list(pill_backgrounds(dark).values())
    for band in ("green", "amber", "orange", "red", "grey"):
        for bg in bases:
            assert contrast_ratio(band_color(band, dark), bg) >= 4.5, (band, bg.name())
    states = [dev("a", battery=b, charging=c) for b in (None, 5, 25, 45, 90) for c in (False, True)]
    states += [dev("x", connected=False), dev("y", connected=False, stale=True)]
    for d in states:
        for lv in (True, False):
            st = row_style(d, 20, dark, lv)
            for bg in bases + [st.bg]:
                assert contrast_ratio(st.fg, bg) >= 4.5, (d, lv, st.fg.name(), bg.name())


def test_rowstyle_frozen():
    s = row_style(dev("a"), 20, True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.band = "x"


@pytest.mark.parametrize("size", [16, 24])
@pytest.mark.parametrize("flag", [True, False])
def test_icons_render(qapp, size, flag):
    for make in (icons.eye_icon, icons.record_icon):
        ic = make(size, flag, QColor(200, 200, 200))
        assert not ic.isNull()
        for scale in (1, 2):
            img = ic.pixmap(size * scale).toImage()
            assert any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))
    img = QImage(48, 48, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    icons.draw_eye(p, QRectF(0, 0, 48, 48), QColor("white"), True)
    p.end()
