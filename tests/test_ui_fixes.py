import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent
from PySide6.QtWidgets import QApplication

from taskbar_battery_widget import colors, widget as w
from taskbar_battery_widget.colors import band_color, contrast_ratio, pill_backgrounds
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.popup import HoverPopup, build_popup_content, idle_text, wrap_title
from taskbar_battery_widget.tray import TrayController


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _en(monkeypatch):
    set_language("en")
    monkeypatch.setattr(w.taskbar, "get_taskbar_info", lambda: None)    # keep the real taskbar out
    yield


class Cfg:
    def __init__(self, **kw):
        self.d = {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False,
                  "hidden_devices": [], "color_mode": "levels"}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


# ---------------------------------------------------------------- contrast
@pytest.mark.parametrize("dark", [True, False])
def test_text_contrast_on_composited_pill_backgrounds(dark):
    bgs = pill_backgrounds(dark)
    assert set(bgs) == {"normal", "hover", "low", "low_hover"}
    for band in ("green", "amber", "orange", "red", "grey"):
        c = band_color(band, dark)
        for name, bg in bgs.items():
            assert contrast_ratio(c, bg) >= 4.5, (dark, band, name, contrast_ratio(c, bg))
    fg = QColor(255, 255, 255) if dark else QColor(20, 20, 20)
    for bg in bgs.values():
        assert contrast_ratio(fg, bg) >= 4.5


def test_composite_math():
    assert colors.composite(QColor(255, 255, 255, 0), QColor(10, 20, 30)).getRgb()[:3] == (10, 20, 30)
    assert colors.composite(QColor(255, 255, 255, 255), QColor(10, 20, 30)).getRgb()[:3] == (255, 255, 255)


# ---------------------------------------------------------------- accessibility
def test_widget_accessible_name_and_description(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "Mouse", "mouse", 97, True), Device("b", "Keys", "keyboard", 71, True)])
    assert wd.accessibleName() == "Taskbar Battery Widget"
    assert wd.accessibleDescription() == "Mouse 97 %, Keys 71 %"
    wd.set_devices([Device("a", "Mouse", "mouse", 10, True)])
    assert "Mouse 10 % low battery" == wd.accessibleDescription()
    wd.set_devices([])
    assert wd.accessibleDescription() == "No devices"


def test_popup_accessible(qapp):
    p = HoverPopup()
    p.set_content(build_popup_content(Device("a", "Alpha", "mouse", 50, True), 20))
    assert p.accessibleName() == "Alpha" and "50%" in p.accessibleDescription()


# ---------------------------------------------------------------- non-colour cues
def test_warn_and_alert_flags(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "A", "mouse", 95, True), Device("b", "B", "mouse", 25, True),
                    Device("c", "C", "mouse", 8, True), Device("d", "D", "mouse", 8, True, charging=True)])
    flags = [(p.warn, p.alert) for p in wd._pills]
    assert flags == [(False, False), (True, False), (True, True), (False, False)]
    assert wd.pill_texts() == ["95%", "25%", "8%", "8%"]       # text itself never changes


def test_alert_pill_draws_outline_and_bar_height(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd._light = False
    wd._pill_h = 28
    wd.set_devices([Device("a", "A", "mouse", 8, True)])
    img = wd.grab().toImage()
    x, y, pw, ph = wd._rects[0]
    border = QColor(img.pixel(int(x + pw / 2), int(y)))       # top edge centre
    assert border.alpha() > 0 and border.red() > 200
    assert w.BAR_H == 3.0


def test_bar_hidden_on_short_pills_and_font_min(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd._light = False
    wd._pill_h = 20
    wd.set_devices([Device("a", "A", "mouse", 95, True)])
    assert wd._font().pixelSize() >= 12
    img = wd.grab().toImage()
    x, y, pw, ph = wd._rects[0]
    # where the bar would be: nothing green there
    px = QColor(img.pixel(int(x + w.PAD + 2), int(y + ph - 4)))
    assert not (px.green() > 180 and px.alpha() > 200)


def test_flash_only_on_new_alert(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "A", "mouse", 8, True)])         # first population: no blink
    assert wd._flash_left == 0
    wd.set_devices([Device("a", "A", "mouse", 8, True), Device("b", "B", "mouse", 50, True)])
    assert wd._flash_left == 0
    wd.set_devices([Device("a", "A", "mouse", 8, True), Device("b", "B", "mouse", 5, True)])
    assert wd._flash_left == w.FLASH_TICKS and wd._flash_keys == {visibility_key("b")}
    wd._flash_timer.stop()


# ---------------------------------------------------------------- idle / dim / unknown
def test_idle_has_moon_not_text_and_text_stays_opaque(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("b", "B", "mouse", 95, True, idle=True)])
    assert wd.pill_texts() == ["95%"]
    pill = wd._pills[0]
    assert wd._text_color(pill, QColor(255, 255, 255), True).alpha() == 255
    fm = QFontMetrics(wd._font())
    plain = wd._pill_width(w._Pill("mouse", "95%", ""), fm, wd._icon_size(), 6)
    assert wd._pill_width(pill, fm, wd._icon_size(), 6) > plain      # room for the moon


def test_dim_and_unknown_use_palette_grey(qapp):
    wd = w.TaskbarWidget(Cfg(show_disconnected=True))
    wd.set_devices([Device("a", "A", "mouse", None, False), Device("b", "B", "mouse", None, True)])
    for pill in wd._pills:
        assert wd._text_color(pill, QColor(255, 255, 255), True) == band_color("grey", True)
        assert wd._text_color(pill, QColor(20, 20, 20), False) == band_color("grey", False)
    assert w.DIM_ALPHA >= 170


# ---------------------------------------------------------------- popup
def test_idle_text_hours():
    d = Device("b", "G", "mouse", 64, True, idle=True, updated=0.0)
    assert idle_text(d, now=59 * 60) == "Asleep · last value 59 min ago"
    assert idle_text(d, now=120 * 60) == "Asleep · last value 2 h ago"


def test_wrap_title_two_lines_and_elide(qapp):
    p = HoverPopup()
    fm = QFontMetrics(p._fonts()[0])
    lines = wrap_title("Razer BlackShark V2 HyperSpeed Wireless Gaming Headset", fm, 150)
    assert len(lines) == 2 and lines[0].startswith("Razer")
    assert all(fm.horizontalAdvance(t) <= 150 for t in lines)
    assert wrap_title("Short", fm, 150) == ["Short"]
    long = wrap_title("x" * 200, fm, 150)
    assert len(long) == 2 and long[1].endswith("…")


def test_popup_dot_and_two_line_title(qapp):
    p = HoverPopup()
    c = build_popup_content(Device("a", "Razer BlackShark V2 HyperSpeed Wireless Headset Pro", "headset", 8, True), 20)
    assert c.dot_band == "red"
    p.set_content(c)
    assert p._dot is not None and len(p._title) == 2
    p.set_content(c, show_dot=False)
    assert p._dot is None
    assert build_popup_content(Device("a", "A", "mouse", None, True), 20).dot_band is None


# ---------------------------------------------------------------- layout / overflow
def test_fit_count():
    assert w.fit_count([50, 50, 50], None, 30) == 3
    assert w.fit_count([50, 50, 50], 300, 30) == 3
    assert w.fit_count([50, 50, 50, 50], 150, 30) == 2         # 2*2+100+30+8
    assert w.fit_count([50, 50], 10, 30) == 1                  # always at least one


def test_overflow_pill_collapses_rest(qapp):
    wd = w.TaskbarWidget(Cfg())
    devs = [Device(str(i), f"Dev{i}", "mouse", 90 - i, True) for i in range(6)]
    wd._max_extent = 220
    wd.set_devices(devs)
    texts = wd.pill_texts()
    assert texts[-1].startswith("+") and len(texts) < 7
    n = int(texts[-1][1:])
    assert n + len(texts) - 1 == 6
    assert wd.width() <= 220 + 4
    ow = wd._pills[-1]
    assert [m.a11y for m in ow.members][0].startswith("Dev")
    assert "Dev5" in wd.accessibleDescription()                 # description lists everything
    wd.show()
    wd._hover = len(wd._pills) - 1
    wd._show_popup()
    assert wd._popup.isVisible() and wd._popup._title[0] == "More devices"
    wd.hide()


def test_center_rect_y():
    assert w.center_rect_y((10, 0, 50, 30), 100) == (10, 85, 50, 115)


# ---------------------------------------------------------------- interaction
def _mouse(widget, pos, button):
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                       button, button, Qt.KeyboardModifier.NoModifier)


def test_left_click_shows_popup_right_click_menu(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "Alpha", "keyboard", 80, True)])
    wd.show()
    x, y, pw, ph = wd._rects[0]
    pos = QPoint(int(x + pw / 2), int(y + ph / 2))
    wd.mousePressEvent(_mouse(wd, pos, Qt.MouseButton.LeftButton))
    assert wd._popup is not None and wd._popup.isVisible()
    got = []
    wd.context_menu_requested.connect(got.append)
    wd._hide_popup()
    wd.mousePressEvent(_mouse(wd, pos, Qt.MouseButton.RightButton))
    assert got and not wd._popup.isVisible()
    wd.hide()


def test_rebuild_resets_and_rearms_hover(qapp):
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device("a", "Alpha", "keyboard", 80, True)])
    wd.show()
    x, y, pw, ph = wd._rects[0]
    inside = wd.mapToGlobal(QPoint(int(x + 3), int(y + 3)))
    wd._cursor_pos = lambda: inside
    wd._hover = 5                       # stale index
    wd._rebuild()
    assert wd._hover == 0 and wd._hover_timer.isActive()
    wd._hover_timer.stop()
    wd._cursor_pos = lambda: QPoint(-500, -500)
    wd._rebuild()
    assert wd._hover == -1 and not wd._hover_timer.isActive()
    wd.hide()


# ---------------------------------------------------------------- tray
def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_tray_menu_structure(qapp, tmp_path):
    set_language("es")
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    tray.set_devices([Device("a", "Raton", "mouse", 97, True, charging=True)])
    tray._sync_menu()
    top = _texts(tray.menu)
    assert top[0] == "Raton: 97 % · Cargando"
    assert top[1:] == ["Actualizar", "Mostrar", "Fuentes", "Alertas", "Iniciar con Windows", "Salir"]
    sub = {a.text(): a.menu() for a in tray.menu.actions() if a.menu()}
    assert _texts(sub["Mostrar"]) == ["Desconectados", "Dispositivos visibles", "Color según el nivel"]
    assert len(_texts(sub["Fuentes"])) == 3
    alerts = sub["Alertas"]
    assert _texts(alerts)[0].startswith("Avisar") and alerts.actions()[1].menu() is not None


def test_tray_hidden_device_not_in_list_or_icon(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    tray.set_devices([Device("a", "Low", "mouse", 5, True), Device("b", "High", "mouse", 90, True)])
    tip = lambda: tray._tray.toolTip()
    assert tip().endswith("5%")
    tray.set_device_visible("a", False)
    assert cfg.get("hidden_devices") == [visibility_key("a")]
    assert tip().endswith("90%")
    tray._sync_menu()
    assert not any(t.startswith("Low") for t in _texts(tray.menu))
    tray.show_all_devices()
    assert tip().endswith("5%")
