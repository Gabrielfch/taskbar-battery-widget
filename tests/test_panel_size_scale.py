"""Fixed panel size (proportional to the screen), UI scale, panel_size / ui_scale / language settings."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import i18n
from taskbar_battery_widget.config import (LANGUAGES, PANEL_SIZE_FRACTION, PANEL_SIZES, UI_SCALES, Config)
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController
from taskbar_battery_widget.widget import TaskbarWidget

SCREENS = (QRect(0, 0, 1920, 1040), QRect(0, 0, 2560, 1400))


def setup_function(_):
    set_language("en")


def devs(n):
    return [Device(id=f"d{i}", name=f"Device number {i}", kind="mouse", battery=50, connected=True)
            for i in range(n)]


def model(n):
    return build_panel_model(devs(n), (), True, 20, True, lang="en")


@pytest.fixture
def tray(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_devices(devs(3))
    yield t
    if t._panel is not None:
        t._panel.close()


def open_panel(t, n=3):
    t.set_devices(devs(n))
    t.open_panel(QPoint(400, 400))
    return t._panel


# ------------------------------------------------------------------ config
def test_config_validates_new_keys(tmp_path):
    c = Config(tmp_path / "c.json")
    assert (c.get("panel_size"), c.get("ui_scale"), c.get("language")) == ("normal", 100, "auto")
    for key, good, bad in (("panel_size", "large", "huge"), ("ui_scale", 115, 120), ("language", "es", "xx")):
        c.set(key, good)
        with pytest.raises(ValueError):
            c.set(key, bad)
    with pytest.raises(ValueError):
        c.set("ui_scale", True)
    (tmp_path / "bad.json").write_text('{"panel_size": "x", "ui_scale": 77, "language": 3}', encoding="utf-8")
    d = Config(tmp_path / "bad.json")
    assert (d.get("panel_size"), d.get("ui_scale"), d.get("language")) == ("normal", 100, "auto")
    assert set(UI_SCALES) == {90, 100, 115, 130} and PANEL_SIZES == ("compact", "normal", "large")


# ------------------------------------------------------------------ proportional, fixed size
@pytest.mark.parametrize("avail", SCREENS)
@pytest.mark.parametrize("size", PANEL_SIZES)
def test_height_is_fraction_of_available_height(qapp, avail, size):
    w, h = dp.panel_dimensions(avail, size, 100)
    assert h == round(avail.height() * PANEL_SIZE_FRACTION[size])
    assert 300 <= w <= avail.width() * 0.4 + 1


def test_width_formula_and_scale():
    avail = QRect(0, 0, 1920, 1040)
    w100, h = dp.panel_dimensions(avail, "normal", 100)
    assert (w100, h) == (322, 520)          # clamp(520 * 0.62, 300, 0.4 * 1920) = 322
    assert dp.panel_dimensions(avail, "normal", 130)[0] == round(322.4 * 1.3)
    assert dp.panel_dimensions(avail, "compact", 100)[0] == 300         # 416 * 0.62 = 258 -> min 300
    assert dp.panel_dimensions(QRect(0, 0, 600, 800), "large", 130)[0] <= 540   # never > 90 % of the screen


@pytest.mark.parametrize("avail", SCREENS)
def test_same_size_for_list_and_settings_pages(qapp, tray, avail):
    p = open_panel(tray, 1)
    p._screen_avail = avail
    p._refit()
    size = p.size()
    assert size.height() == round(avail.height() * 0.5)
    p.set_model(model(30))
    assert p.size() == size                                  # 30 devices: same window
    p.show_settings()
    assert p.size() == size
    v = p.settings_view
    for page in ("sources", "alerts", "logs", "personalization", "position", "root"):
        v.show_page(page)
        assert p.size() == size, page
    p.show_devices()
    assert p.size() == size


def test_scroll_with_many_devices_empty_space_with_one(qapp, tray):
    p = open_panel(tray, 1)
    p.set_model(model(1))
    p.show()
    QTest.qWait(30)
    assert p._scroll.verticalScrollBar().maximum() == 0
    row = p._content.findChildren(QLabel)[-1]
    assert p._content.height() <= p._scroll.viewport().height()
    first_y = min(c.mapTo(p._content, QPoint(0, 0)).y() for c in p._content.findChildren(QLabel))
    assert first_y < 40                                       # content starts at the top
    assert row is not None
    p.set_model(model(30))
    QTest.qWait(30)
    assert p._scroll.verticalScrollBar().maximum() > 0
    assert p._toolbar_widget.isVisible() and p._toolbar_widget.geometry().bottom() <= p.height()


def test_name_uses_available_width(qapp):
    p = dp.DevicePanel(dark=True)       # no recording buttons: eye + pencil only
    p.show_at(QPoint(400, 400))
    p.set_model(build_panel_model([Device(id="a", name="G Pro", kind="mouse", battery=50,
                                          connected=True)], (), True, 20, True, lang="en"))
    QTest.qWait(30)
    lab = next(x for x in p._content.findChildren(dp._ElidedLabel))
    assert lab.text() == "G Pro" and lab.width() > 100      # not elided: it fits
    long = Device(id="b", name="A very long device name that cannot possibly fit in the row", kind="mouse",
                  battery=50, connected=True)
    p.set_model(build_panel_model([long], (), True, 20, True, lang="en"))
    QTest.qWait(30)
    lab = next(x for x in p._content.findChildren(dp._ElidedLabel))
    assert lab.text().endswith("…") and lab.full_text().startswith("A very long")
    p.close()


# ------------------------------------------------------------------ settings entries, live apply
def test_personalization_has_the_choices_and_applies_live(qapp, tray):
    p = open_panel(tray)
    p.show_settings("personalization")
    v = p.settings_view
    assert {"panel_size", "ui_scale", "language"} <= set(v.rows())
    h0 = p.height()
    QTest.mouseClick(v.row("panel_size"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(v.row("panel_size:large"), Qt.MouseButton.LeftButton)
    assert tray._cfg.get("panel_size") == "large" and p.panel_size == "large"
    assert p.height() > h0 and p.height() == dp.panel_dimensions(p._screen_avail, "large", 100)[1]
    w0 = p.width()
    QTest.mouseClick(v.row("ui_scale"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(v.row("ui_scale:130"), Qt.MouseButton.LeftButton)
    assert tray._cfg.get("ui_scale") == 130 and p.ui_scale == 130
    assert p.width() > w0 and v.scale == 1.3 and p.in_settings


def test_scale_grows_fonts_rows_buttons(qapp, tray):
    p = open_panel(tray)
    base_font = p.font().pointSizeF() if p.font().pixelSize() <= 0 else p.font().pixelSize()
    tb = p._toolbar["refresh"].width()
    p.set_metrics("normal", 130)
    f = p.font().pointSizeF() if p.font().pixelSize() <= 0 else p.font().pixelSize()
    assert f > base_font and p._toolbar["refresh"].width() > tb
    p.show_settings()
    assert all(r.height() >= 46 for r in p.settings_view.rows().values())
    p.show_devices()
    eye = next(b for k, b in p._buttons.items() if k[1] == "eye")
    assert eye.width() == round(32 * 1.3) and eye.iconSize().width() == round(18 * 1.3)


def test_touch_targets_stay_32_at_90(qapp, tray):
    p = open_panel(tray)
    p.set_metrics("normal", 90)
    assert all(b.width() >= 32 and b.height() >= 32 for b in p._buttons.values())
    assert all(b.width() >= 32 and b.height() >= 32 for b in p._toolbar.values())
    p.show_settings()
    v = p.settings_view
    assert all(r.height() >= 32 for r in v.rows().values())
    assert v.back_button().width() >= 32 and v.back_button().height() >= 32


def test_live_apply_emits_ui_change_for_the_widget(qapp, tray):
    seen = []
    tray.ui_settings_changed.connect(lambda: seen.append(1))
    open_panel(tray)
    tray._set_ui_scale(115)
    tray._set_panel_size("compact")
    assert len(seen) == 2 and tray._panel.ui_scale == 115 and tray._panel.panel_size == "compact"


# ------------------------------------------------------------------ taskbar pills
class FakeCfg:
    def __init__(self, pct):
        self.pct = pct

    def get(self, key):
        return {"ui_scale": self.pct, "low_threshold": 20, "show_disconnected": False,
                "color_mode": "levels", "hidden_devices": []}.get(key)


def test_scaled_pill_height_limits():
    f = TaskbarWidget.scaled_pill_h
    assert f(32, 100, 48) == 32 and f(32, 90, 48) == round(32 * 0.9)
    assert f(32, 130, 48) == 42 and f(32, 130, 48) <= 48 - 4           # fits the 48 px bar
    assert f(20, 130, 30) == 26 and f(20, 130, 30) <= 30 - 4           # small bar: capped
    assert f(32, 130, 40) == 36                                       # bar 40: 40 - 4
    assert f(28, 130, None) == round(28 * 1.3)                        # floating: no bar to respect


def test_pills_grow_with_scale(qapp):
    def measure(pct):
        wd = TaskbarWidget(FakeCfg(pct))
        wd.set_devices(devs(2))
        wd._pill_h = TaskbarWidget.scaled_pill_h(32, pct, 48)
        wd._pill_scale = wd._pill_h / 32
        wd._relayout()
        return wd.width(), wd.height(), wd._font().pixelSize(), wd._icon_size(), wd._ui_pct()
    w100, h100, f100, i100, p100 = measure(100)
    w130, h130, f130, i130, p130 = measure(130)
    assert (p100, p130) == (100, 130)
    assert w130 > w100 and f130 > f100 and i130 > i100
    assert h130 == 42 + 4 and h130 <= 48                               # pill + margins <= 48 px bar
    assert measure(90)[1] < h100


def test_widget_ignores_bogus_scale(qapp):
    wd = TaskbarWidget(FakeCfg("big"))
    assert wd._ui_pct() == 100


# ------------------------------------------------------------------ language
def test_language_registry_and_fallback():
    assert set(i18n.LANGUAGES) == {"es", "en"} and i18n.LANGUAGES["es"] == "Español"
    assert LANGUAGES == ("auto", "es", "en")
    assert i18n.normalize_language("es-MX") == "es" and i18n.normalize_language("pt-BR") == "en"
    assert i18n.normalize_language(None) == "en"
    assert i18n.tr_in("zz", "quit") == "Quit"
    assert i18n.tr_in("es", "quit") == "Salir"


def test_apply_language_setting_override_and_auto(monkeypatch):
    monkeypatch.setattr(i18n, "detect_language", lambda: "es")
    assert i18n.apply_language_setting("en") == "en" and i18n.tr("quit") == "Quit"
    assert i18n.apply_language_setting("es") == "es" and i18n.tr("quit") == "Salir"
    i18n.apply_language_setting("en")
    assert i18n.apply_language_setting("auto") == "es"                 # back to the system language
    monkeypatch.setattr(i18n, "detect_language", lambda: "en")
    assert i18n.apply_language_setting("auto") == "en"


def test_detect_language_recognises_registered_languages(monkeypatch):
    monkeypatch.setitem(i18n.LANGUAGES, "pt", "Português")
    monkeypatch.setitem(i18n._TABLES, "pt", {"quit": "Sair"})
    assert i18n.normalize_language("pt-BR") == "pt"
    assert i18n.tr_in("pt", "quit") == "Sair" and i18n.tr_in("pt", "refresh") == "Refresh"   # key fallback
    i18n.set_language("pt")
    assert i18n.tr("quit") == "Sair" and i18n.tr("refresh") == "Refresh"


def test_language_choice_in_panel_changes_texts_live(qapp, tray, monkeypatch):
    monkeypatch.setattr(i18n, "detect_language", lambda: "en")
    p = open_panel(tray)
    p.show_settings("personalization")
    v = p.settings_view
    assert v.title_text() == "Personalization" and p._toolbar["quit"].toolTip() == "Quit"
    labels = {o.value: o.label for o in tray._settings.find("language").options()}
    assert labels == {"auto": "Automatic (system)", "es": "Español", "en": "English"}
    QTest.mouseClick(v.row("language"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(v.row("language:es"), Qt.MouseButton.LeftButton)
    assert tray._cfg.get("language") == "es" and i18n.get_language() == "es"
    assert v.title_text() == "Personalización" and v.row("language").accessibleName().startswith("Idioma")
    assert p._toolbar["quit"].toolTip() == "Salir" and p.accessibleName() == "Dispositivos"
    assert [a.text() for a in tray.menu.actions() if a.text()][0] == "Actualizar"
    assert v.row("language:es").isChecked() and p.in_settings
    # own names stay in their own language
    assert v.row("language:en").value_text() == "" and v.row("language:en")._label == "English"
    QTest.mouseClick(v.row("language:auto"), Qt.MouseButton.LeftButton)
    assert tray._cfg.get("language") == "auto" and i18n.get_language() == "en"
    assert v.title_text() == "Personalization" and p._toolbar["quit"].toolTip() == "Quit"
    p.show_devices()
