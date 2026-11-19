import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import icons, widget as w
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.panel_model import build_panel_model

KINDS = sorted(icons._DRAWERS) + ["unknown_kind"]


@pytest.fixture(autouse=True)
def _reset_style():
    icons.set_icon_style("outline")
    yield
    icons.set_icon_style("outline")


def render(kind, size, style, level=None, charging=False, color=QColor(40, 200, 90)):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    icons.draw_kind_icon(p, kind, QRectF(0, 0, size, size), color, level, charging, style)
    p.end()
    return img


def painted(img):
    return sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixelColor(x, y).alpha() > 0)


def test_default_style_and_invalid_value():
    assert icons.get_icon_style() == "outline"
    icons.set_icon_style("retro")
    assert icons.get_icon_style() == "retro"
    icons.set_icon_style("bogus")
    assert icons.get_icon_style() == "outline"
    icons.set_icon_style("none")
    assert icons.get_icon_style() == "none"


@pytest.mark.parametrize("style", icons.ICON_STYLES)
@pytest.mark.parametrize("size", [16, 20, 32])
def test_every_style_draws_every_kind(qapp, style, size):
    for kind in KINDS:
        for level in (None, 0, 50, 100):
            for charging in (False, True):
                img = render(kind, size, style, level, charging)
                if style == "none":
                    assert painted(img) == 0
                else:
                    assert painted(img) > 0, (style, kind, size, level)


def test_active_style_is_used_when_style_omitted(qapp):
    img = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    icons.set_icon_style("none")
    p = QPainter(img)
    icons.draw_kind_icon(p, "mouse", QRectF(0, 0, 20, 20), QColor(255, 255, 255))
    p.end()
    assert painted(img) == 0


@pytest.mark.parametrize("style", ["filled", "retro", "simple", "battery"])
def test_styles_differ_from_outline(qapp, style):
    for kind in ("mouse", "keyboard", "headset", "gamepad"):
        assert render(kind, 20, style, 60) != render(kind, 20, "outline", 60)


def test_filled_has_more_ink_than_outline(qapp):
    for kind in ("mouse", "keyboard", "gamepad", "phone"):
        assert painted(render(kind, 32, "filled")) > painted(render(kind, 32, "outline"))


def test_retro_bitmaps_are_well_formed():
    for name, rows in icons._RETRO.items():
        assert len(rows) == 16, name
        assert all(len(r) == 16 for r in rows), name
        assert set("".join(rows)) <= set(".kwgda"), name
    for kind in ("mouse", "keyboard", "headset", "gamepad", "speaker", "phone", "generic"):
        assert kind in icons._RETRO


def test_retro_accent_uses_given_colour(qapp):
    img = render("mouse", 16, "retro", color=QColor(10, 20, 250))
    assert any(img.pixelColor(x, y).blue() == 250 and img.pixelColor(x, y).red() == 10
               for y in range(16) for x in range(16))


def test_battery_fill_grows_with_level(qapp):
    assert painted(render("mouse", 32, "battery", 80)) > painted(render("mouse", 32, "battery", 20))
    assert painted(render("mouse", 32, "battery", 20)) > painted(render("mouse", 32, "battery", 0))
    assert render("mouse", 32, "battery", 50) == render("keyboard", 32, "battery", 50)   # kind ignored


def test_battery_charging_adds_bolt(qapp):
    assert render("mouse", 32, "battery", 50, True) != render("mouse", 32, "battery", 50, False)


# ---------------------------------------------------------------- widget
class Cfg:
    def __init__(self):
        self.d = {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False,
                  "hidden_devices": [], "color_mode": "levels", "pill_style": "classic"}

    def get(self, k):
        return self.d[k]


def test_none_style_drops_icon_space(qapp, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_info", lambda: None)
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("b", "B", "mouse", 95, True)])
    with_icon = wd._rects[0][2]
    icons.set_icon_style("none")
    wd.refresh_style()
    assert wd._rects[0][2] == with_icon - wd._icon_size() - w.ICON_GAP
    wd.grab()        # paints without an icon
    icons.set_icon_style("battery")
    wd.set_config(wd._cfg)        # set_config alone is enough to re-measure
    assert wd._rects[0][2] == with_icon
    wd.grab()
    wd.close()


def test_battery_style_charging_pill_has_no_extra_bolt_width(qapp, monkeypatch):
    monkeypatch.setattr(w.taskbar, "get_taskbar_info", lambda: None)
    wd = w.TaskbarWidget(Cfg())
    icons.set_icon_style("battery")
    wd.set_devices([Device("b", "B", "mouse", 95, True, charging=True)])
    fm = QFontMetrics(wd._font())
    assert wd._rects[0][2] == w.pill_width(fm.horizontalAdvance(wd._pills[0].text), wd._icon_size())
    wd.close()


# ---------------------------------------------------------------- panel
def test_panel_rebuilds_when_style_changes(qapp):
    set_language("en")
    m = build_panel_model([Device("a", "Dev", "mouse", 50, True)], (), True, 20, True, lang="en")
    panel = dp.DevicePanel(dark=True)
    panel.set_model(m)
    first = panel._sig
    panel.set_model(m)
    assert panel._sig == first
    icons.set_icon_style("retro")
    panel.set_model(m)
    assert panel._sig != first
    panel.close()


def test_panel_never_uses_none(qapp):
    icons.set_icon_style("none")
    pm = dp._kind_pixmap("mouse", QColor(255, 255, 255), 1.0, 50, False)
    assert painted(pm.toImage()) > 0
