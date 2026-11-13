"""UI review fixes: radio off, row layout, a11y, known eyes, tray toggling, ui-only signals, beta filter."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QSystemTrayIcon, QToolButton

from taskbar_battery_widget import app as app_mod
from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import i18n, wireless
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.device_history import DeviceHistory
from taskbar_battery_widget.i18n import set_language, tr_in
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController


def setup_function(_):
    set_language("en")


def dev(i, name=None, battery=50, connected=True, **kw):
    return Device(id=i, name=name or f"Dev {i}", kind="mouse", battery=battery, connected=connected, **kw)


def model(devs, hidden=(), show_disc=True, **kw):
    return build_panel_model(devs, hidden, show_disc, 20, True, lang="en", **kw)


def row_frames(p):
    return [f for f in p._content.findChildren(QFrame) if f.objectName() == "row"]


@pytest.fixture
def panel(qapp):
    p = dp.DevicePanel(dark=True, show_logging=True)
    yield p
    p.close()
    p.deleteLater()


# ---- 1. radio off
def test_radio_off_moves_bluetooth_to_known_and_shows_line(panel):
    devs = [dev("bt", "Pad", 80), dev("usb", "Mouse", 70, link="usb")]
    m = model(devs, radio_on=False)
    assert [r.device_id for r in m.connected] == ["usb"]
    assert [r.device_id for r in m.known] == ["bt"]
    assert m.known[0].status_text == "Bluetooth off" and m.known[0].style.dimmed
    assert m.radio_on is False
    assert model(devs).signature != m.signature
    panel.set_model(m)
    assert "Bluetooth is off" in [lab.text() for lab in panel.findChildren(QLabel)]
    panel.set_model(model(devs))
    assert "Bluetooth is off" not in [lab.text() for lab in panel.findChildren(QLabel)]


def test_radio_off_spanish():
    assert tr_in("es", "panel_radio_off") == "Bluetooth desactivado"
    m_es = build_panel_model([dev("bt")], (), True, 20, True, lang="es", radio_on=False)
    assert m_es.known[0].status_text == "Bluetooth apagado"


def test_tray_set_radio_pushes_to_panel(qapp, tmp_path):
    tray = TrayController(Config(tmp_path / "c.json"))
    tray.set_devices([dev("bt", "Pad")])
    tray.open_panel()
    assert len(tray._panel._model.connected) == 1
    tray.set_radio(False)
    assert tray._panel._model.radio_on is False and len(tray._panel._model.known) == 1
    tray._panel.close()


# ---- 2. row layout
def test_long_status_buttons_inside_row_and_viewport(panel):
    import time
    long_dev = dev("a", "A very long device name that will not fit in the row at all", 55,
                   connected=False, stale=True, last_seen=time.time() - 3 * 86400)
    panel.set_model(model([long_dev, dev("b", "Short")]), logging_keys=[visibility_key("a")])
    panel.show_at(QPoint(300, 300))
    QApplication.processEvents()
    viewport = panel._scroll.viewport()
    rows = row_frames(panel)
    assert len(rows) == 2
    for row in rows:
        for b in row.findChildren(QToolButton):
            assert b.width() >= 32 and b.height() >= 32
            tl = b.mapTo(row, QPoint(0, 0))
            br = b.mapTo(row, QPoint(b.width() - 1, b.height() - 1))
            assert row.rect().contains(tl) and row.rect().contains(br)
            vtl = b.mapTo(viewport, QPoint(0, 0))
            vbr = b.mapTo(viewport, QPoint(b.width() - 1, b.height() - 1))
            assert viewport.rect().contains(vtl) and viewport.rect().contains(vbr)
        assert [lab for lab in row.findChildren(QLabel) if lab.wordWrap()]
    long_row = rows[1]          # connected rows come first, the stale one is under Known
    first_status = [lab for lab in long_row.findChildren(QLabel) if lab.wordWrap()][0]
    assert first_status.height() > first_status.fontMetrics().height() + 2     # wrapped, not overflowing
    name = [lab for lab in long_row.findChildren(QLabel) if not lab.wordWrap() and lab.toolTip()][0]
    assert name.text().endswith("…") and name.toolTip().startswith("A very long")


# ---- 3. accessibility
def test_button_descriptions_and_panel_name(panel):
    panel.set_model(model([dev("a", "Mouse", 10)]), logging_keys=[visibility_key("a")])
    assert panel.accessibleName() == "Devices"
    btns = [b for b in panel.findChildren(QToolButton) if b not in panel._toolbar_buttons()]
    assert len(btns) == 3
    for b in btns:
        assert b.accessibleDescription().startswith("Mouse, ")
    assert all(not f.accessibleName() for f in row_frames(panel))
    set_language("es")
    panel.set_model(build_panel_model([dev("a", "Raton")], (), True, 20, True, lang="es"))
    assert panel.accessibleName() == "Dispositivos"


def test_left_right_moves_inside_row_and_empty_focuses_panel(panel):
    panel.set_model(model([dev("a", "A"), dev("b", "B")]), logging_keys=[visibility_key("a")])
    panel.show_at(QPoint(300, 300))
    row = panel._rows[0]
    assert len(row) == 3        # rec, open, eye
    eye = panel._nav[0]
    assert eye is row[-1] and panel.focusWidget() is eye
    QTest.keyClick(eye, Qt.Key.Key_Left)
    assert panel.focusWidget() is row[1]
    QTest.keyClick(row[1], Qt.Key.Key_Left)
    QTest.keyClick(row[0], Qt.Key.Key_Left)         # clamps
    assert panel.focusWidget() is row[0]
    QTest.keyClick(row[0], Qt.Key.Key_Right)
    assert panel.focusWidget() is row[1]
    QTest.keyClick(row[1], Qt.Key.Key_Down)
    assert panel.focusWidget() is panel._nav[1]
    panel.close()
    panel.set_model(model([]))
    panel.show_at(QPoint(300, 300))
    assert panel.focusWidget() is panel


class _Cfg:
    def get(self, k, d=None):
        return {"low_threshold": 20, "color_mode": "levels"}.get(k, d)


def _overflow(devs):
    wd = w.TaskbarWidget(_Cfg())
    wd._max_extent = 120
    wd.set_devices(devs)
    return wd, wd._pills[-1]


@pytest.mark.parametrize("lang,txt3", [("en", "3 more devices. Open the list"),
                                       ("es", "3 dispositivos más. Abrir la lista")])
def test_overflow_pill_a11y_plural(qapp, lang, txt3):
    set_language(lang)
    devs = [Device(str(i), f"D{i}", "mouse", 90, True) for i in range(8)]
    wd, ow = _overflow(devs)
    assert ow.kind == "overflow"
    n = int(ow.text[1:])
    assert ow.a11y == i18n.tr("panel_more_other", n=n)
    assert i18n.tr("panel_more_other", n=3) == txt3
    assert tr_in("en", "panel_more_one", n=1) == "1 more device. Open the list"


def test_overflow_pill_warns_when_member_is_low(qapp):
    devs = [Device(str(i), f"D{i}", "mouse", 90, True) for i in range(6)] + \
           [Device("low", "Low", "mouse", 5, True)]
    wd, ow = _overflow(devs)
    assert ow.members and any(m.alert for m in ow.members)
    assert ow.warn and ow.alert
    calm = [Device(str(i), f"D{i}", "mouse", 90, True) for i in range(8)]
    _, ow2 = _overflow(calm)
    assert not ow2.warn and not ow2.alert


# ---- 4. known eyes disabled when show-known is off
def test_known_row_eyes_disabled_without_show_known(panel):
    devs = [dev("a", "A"), dev("c", "Gone", None, connected=False)]

    def gone_eye():
        return next(b for b in panel.findChildren(QToolButton)
                    if b.accessibleName() == "Show Gone in the taskbar")
    panel.set_model(model(devs, show_disc=False))
    eye = gone_eye()
    assert not eye.isEnabled()
    assert eye.toolTip() == "Turn on 'Show known devices in the taskbar' first"
    assert tr_in("es", "panel_known_eye_needed") == "Activa «Mostrar conocidos en la barra» primero"
    assert eye not in panel._nav
    panel.set_model(model(devs, show_disc=True))
    eye = gone_eye()
    assert eye.isEnabled() and eye.toolTip() == "Hide Gone from the taskbar"


# ---- 5. sizes / stylesheet
def test_buttons_32_and_stylesheet(panel):
    panel.set_model(model([dev("a"), dev("c", connected=False)]))
    for b in panel.findChildren(QToolButton):
        assert b.width() >= 32 and b.height() >= 32
    for dark in (True, False):
        panel.set_dark(dark)
        ss = panel.styleSheet()
        assert "QScrollBar" in ss and "QToolTip" in ss


# ---- 6. DPI
def test_show_resets_signature_and_rebuilds(panel):
    panel.set_model(model([dev("a")]))
    content = panel._content
    panel.show_at(QPoint(300, 300))
    assert panel._content is not content            # showEvent rebuilt for the screen's dpr
    c2 = panel._content
    panel._on_screen_changed()
    assert panel._content is not c2


# ---- 7. wording
def test_wording_and_orphans():
    for lang, menu, footer, known in (
            ("en", "Devices", "Show all in the taskbar", "Show known devices in the taskbar"),
            ("es", "Dispositivos", "Mostrar todos en la barra", "Mostrar conocidos en la barra")):
        assert tr_in(lang, "menu_devices") == menu
        assert tr_in(lang, "show_all") == footer
        assert tr_in(lang, "panel_show_known") == known
    assert "log" not in tr_in("es", "panel_record", name="X").lower().replace("registro", "")
    assert "log" not in tr_in("es", "panel_open_log", name="X").lower().replace("registro", "")
    for orphan in ("menu_show", "visible_devices", "show_disconnected"):
        assert orphan not in i18n.EN and orphan not in i18n.ES


# ---- 8. tray click toggling
def test_tray_click_closes_open_panel_and_ignores_the_closing_click(qapp, tmp_path, monkeypatch):
    import time
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    tray = TrayController(Config(tmp_path / "c.json"))
    tray.set_devices([dev("a")])
    trig = QSystemTrayIcon.ActivationReason.Trigger
    tray._on_activated(trig)
    p = tray._panel
    assert p.isVisible()
    tray._on_activated(trig)            # still visible: toggles closed
    assert not p.isVisible()
    now[0] = 100.1                      # the popup was just closed by the click itself
    tray._on_activated(trig)
    assert not p.isVisible()
    now[0] = 101.0
    tray._on_activated(trig)
    assert p.isVisible()
    p.hide()
    now[0] = 101.1
    tray._on_activated(trig)
    assert not p.isVisible()
    p.close()


# ---- 9. logging config
def test_on_logging_keeps_global_switch_consistent(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t._on_logging("k1", True)
    assert cfg.get("detail_log_devices") == ["k1"] and cfg.get("detail_log_enabled") is True
    t._on_logging("k2", True)
    t._on_logging("k1", False)
    assert cfg.get("detail_log_devices") == ["k2"] and cfg.get("detail_log_enabled") is True
    t._on_logging("k2", False)
    assert cfg.get("detail_log_devices") == [] and cfg.get("detail_log_enabled") is False


def test_on_logging_second_set_skipped_when_first_fails(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    calls = []

    def bad_set(k, v):
        calls.append(k)
        raise ValueError("no")
    cfg.set = bad_set
    t._on_logging("k1", True)
    assert calls == ["detail_log_devices"]


def test_set_startup_checked_restores_block_state(qapp, tmp_path):
    t = TrayController(Config(tmp_path / "c.json"))
    a = t._act_start
    a.blockSignals(True)
    t._set_start_checked(True)
    assert a.signalsBlocked()
    a.blockSignals(False)
    t._set_start_checked(False)
    assert not a.signalsBlocked()


# ---- 10. ui-only signal
def test_ui_toggles_emit_ui_signal_not_settings(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    ui, real = [], []
    t.ui_settings_changed.connect(lambda: ui.append(1))
    t.settings_changed.connect(lambda: real.append(1))
    t._on_visibility("k", False)
    t._on_show_disconnected(True)
    t._on_show_all()
    t._on_logging("k", True)
    assert len(ui) == 4 and not real
    t._set_bool("notifications_enabled", False)
    assert len(real) == 1 and len(ui) == 4


def test_apply_ui_settings_redraws_without_refresh():
    calls = []
    raw = [dev("a")]
    app_mod.apply_ui_settings(object(), object(), raw, lambda d: calls.append(list(d)))
    assert calls == [raw]

    class W:
        def set_config(self, cfg):
            calls.append("cfg")
    app_mod.apply_ui_settings(W(), object(), [], lambda d: calls.append("dev"))
    assert calls[-1] == "cfg"


# ---- 11. beta filter
def test_full_device_list_drops_beta_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(wireless, "is_beta_device", lambda d: str(d.id).startswith("sony:"))
    app_mod._BETA_KEYS.clear()
    cfg = Config(tmp_path / "c.json")
    cfg.set("beta_readers", True)
    history = DeviceHistory(load=False)
    mouse = dev("usb:1", "Mouse", link="usb")
    both = app_mod.full_device_list(history, [dev("sony:1", "DS4", link="usb"), mouse], cfg, 1000.0)
    assert {d.id for d in both} == {"sony:1", "usb:1"}
    cfg.set("beta_readers", False)
    out = app_mod.full_device_list(history, [mouse], cfg, 2000.0)
    assert [d.id for d in out] == ["usb:1"]
    cfg.set("beta_readers", True)
    out = app_mod.full_device_list(history, [mouse], cfg, 3000.0)
    assert {d.id for d in out} == {"usb:1", visibility_key("sony:1")}
    app_mod._BETA_KEYS.clear()
