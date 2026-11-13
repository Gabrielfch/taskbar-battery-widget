import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QToolButton

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model


def setup_function(_):
    set_language("en")


def dev(i, name=None, battery=50, connected=True, **kw):
    return Device(id=i, name=name or f"Dev {i}", kind="mouse", battery=battery, connected=connected, **kw)


def model(devs, hidden=(), show_disc=True):
    return build_panel_model(devs, hidden, show_disc, 20, True, lang="en")


def eyes(p):
    bar = p._toolbar_buttons()
    return [b for b in p.findChildren(QToolButton)
            if b.accessibleName().startswith("Show ") and b not in bar]


def row_eye(p, name):
    return next(b for b in eyes(p) if b.accessibleName() == f"Show {name} in the taskbar")


@pytest.fixture
def panel(qapp):
    p = dp.DevicePanel(dark=True)
    yield p
    p.close()
    p.deleteLater()


def test_window_flags(panel):
    f = panel.windowFlags()
    assert f & Qt.WindowType.Popup and f & Qt.WindowType.FramelessWindowHint


def test_sections_and_rows(panel):
    m = model([dev("a"), dev("b"), dev("c", connected=False, battery=None)])
    panel.set_model(m)
    texts = [lab.text() for lab in panel.findChildren(QLabel)]
    assert "Connected" in texts and "Known devices" in texts
    # one eye per row + the Known header eye
    assert len(eyes(panel)) == 4
    assert len(panel._nav) == 4


def test_no_known_section_without_known_devices(panel):
    panel.set_model(model([dev("a")]))
    assert "Known devices" not in [lab.text() for lab in panel.findChildren(QLabel)]
    assert len(eyes(panel)) == 1


def test_accessible_names_and_state(panel):
    panel.set_model(model([dev("a", "Mouse"), dev("b", "Pad")], hidden=[visibility_key("b")]))
    assert row_eye(panel, "Mouse").isChecked()
    assert not row_eye(panel, "Pad").isChecked()
    assert row_eye(panel, "Pad").accessibleName() == "Show Pad in the taskbar"


def test_spanish_accessible_name(qapp):
    set_language("es")
    p = dp.DevicePanel(dark=True)
    p.set_model(build_panel_model([dev("a", "Raton")], (), True, 20, True, lang="es"))
    assert any(b.accessibleName() == "Mostrar Raton en la barra" for b in p.findChildren(QToolButton))
    assert p._toolbar["show_all"].accessibleName() == "Mostrar todos en la barra"


def test_eye_emits_visibility_toggled(panel):
    got = []
    panel.visibility_toggled.connect(lambda k, v: got.append((k, v)))
    panel.set_model(model([dev("a", "Mouse")]))
    row_eye(panel, "Mouse").click()
    assert got == [(visibility_key("a"), False)]
    row_eye(panel, "Mouse").click()
    assert got[-1] == (visibility_key("a"), True)


def test_known_header_eye_emits(panel):
    got = []
    panel.show_disconnected_toggled.connect(got.append)
    panel.set_model(model([dev("a"), dev("c", connected=False)], show_disc=True))
    eye = next(b for b in eyes(panel) if b.accessibleName() == "Show known devices in the taskbar")
    assert eye.isChecked()
    eye.click()
    assert got == [False]


def test_show_all_button(panel):
    got = []
    panel.show_all_requested.connect(lambda: got.append(1))
    panel.set_model(model([dev("a")]))
    assert not panel._toolbar["show_all"].isEnabled()
    panel.set_model(model([dev("a")], hidden=[visibility_key("a")]))
    assert panel._toolbar["show_all"].isEnabled()
    panel._toolbar["show_all"].click()
    assert got == [1]


def test_logging_buttons_hidden_by_default_and_emit(qapp):
    p = dp.DevicePanel(dark=True)
    p.set_model(model([dev("a", "Mouse")]))
    assert not [b for b in p.findChildren(QToolButton) if b.accessibleName().startswith("Record")]
    p2 = dp.DevicePanel(dark=True, show_logging=True)
    got, opened = [], []
    p2.logging_toggled.connect(lambda k, v: got.append((k, v)))
    p2.open_log_requested.connect(opened.append)
    p2.set_model(model([dev("a", "Mouse")]))
    rec = next(b for b in p2.findChildren(QToolButton) if b.accessibleName().startswith("Record"))
    rec.click()
    assert got == [(visibility_key("a"), True)]
    p2.set_model(model([dev("a", "Mouse")]), logging_keys=[visibility_key("a")])
    op = next(b for b in p2.findChildren(QToolButton) if b.accessibleName().startswith("Open the log"))
    op.click()
    assert opened == [visibility_key("a")]


def test_same_signature_does_not_rebuild(panel):
    panel.set_model(model([dev("a"), dev("b")]))
    content = panel._content
    panel.set_model(model([dev("a"), dev("b")]))
    assert panel._content is content
    panel.set_model(model([dev("a"), dev("b", battery=10)]))
    assert panel._content is not content


def test_rebuild_keeps_scroll_position(panel):
    devs = [dev(f"d{i}") for i in range(40)]
    panel.set_model(model(devs))
    panel.show_at(QPoint(100, 100))
    bar = panel._scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    pos = bar.value()
    assert pos > 0
    devs[3] = dev("d3", battery=5)
    panel.set_model(model(devs))
    assert abs(bar.value() - pos) <= 1


def test_scroll_with_many_rows_and_height_cap(panel):
    panel.set_model(model([dev(f"d{i}") for i in range(40)]))
    panel.show_at(QPoint(500, 500))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    assert panel.height() <= avail.height() * 0.6 + 1
    assert panel._scroll.verticalScrollBar().maximum() > 0


def test_few_rows_no_scroll(panel):
    panel.set_model(model([dev("a")]))
    panel.show_at(QPoint(500, 500))
    assert panel._scroll.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize("anchor", [QPoint(0, 0), QPoint(99999, 99999), QRect(10, 1000, 40, 40),
                                    QRect(1900, 500, 20, 20)])
def test_position_inside_available_geometry(panel, anchor):
    panel.set_model(model([dev(f"d{i}") for i in range(10)]))
    panel.show_at(anchor)
    screen = QGuiApplication.screenAt(panel.geometry().center()) or QGuiApplication.primaryScreen()
    assert screen.availableGeometry().contains(panel.geometry())


def test_escape_closes(panel):
    panel.set_model(model([dev("a")]))
    panel.show_at(QPoint(300, 300))
    assert panel.isVisible()
    QTest.keyClick(panel.focusWidget() or panel, Qt.Key.Key_Escape)
    assert not panel.isVisible()


def test_up_down_moves_between_rows(panel):
    panel.set_model(model([dev("a", "A"), dev("b", "B"), dev("c", "C")]))
    panel.show_at(QPoint(300, 300))
    a, b, c = (row_eye(panel, n) for n in "ABC")
    assert panel.focusWidget() is a
    QTest.keyClick(a, Qt.Key.Key_Down)
    assert panel.focusWidget() is b
    QTest.keyClick(b, Qt.Key.Key_Down)
    QTest.keyClick(c, Qt.Key.Key_Down)         # clamps at the end
    assert panel.focusWidget() is c
    QTest.keyClick(c, Qt.Key.Key_Up)
    assert panel.focusWidget() is b


def test_space_toggles_focused_eye(panel):
    got = []
    panel.visibility_toggled.connect(lambda k, v: got.append(v))
    panel.set_model(model([dev("a", "A")]))
    panel.show_at(QPoint(300, 300))
    QTest.keyClick(panel.focusWidget(), Qt.Key.Key_Space)
    assert got == [False]


def test_tab_order_follows_rows_then_toolbar(panel):
    panel.set_model(model([dev("a", "A"), dev("b", "B")], hidden=[visibility_key("b")]))
    panel.show_at(QPoint(300, 300))
    chain, w = [], row_eye(panel, "A")
    for _ in range(40):
        chain.append(w)
        w = w.nextInFocusChain()
        if w is chain[0]:
            break
    order = [x for x in chain if x in eyes(panel) or x in panel._toolbar_buttons()]
    assert order[:4] == [row_eye(panel, "A"), row_eye(panel, "B"), *panel._toolbar_buttons()[:2]]


def test_hostile_name_is_plain_text(panel):
    panel.set_model(model([dev("a", "<b>x</b>&y")]))
    labs = [lab for lab in panel.findChildren(QLabel) if "x" in lab.text()]
    assert labs and all(lab.textFormat() == Qt.TextFormat.PlainText for lab in labs)


def test_cue_text():
    m = build_panel_model([dev("a", battery=10), dev("b", battery=80, charging=True),
                           dev("c", battery=70, idle=True)], (), True, 20, True, lang="en")
    rows = {r.device_id: r for r in m.connected}
    assert dp.level_text(rows["a"]).startswith("! 10%")
    assert "⚡" in dp.level_text(rows["b"])
    assert "☾" in dp.level_text(rows["c"])


def test_set_dark_rebuilds(panel):
    panel.set_model(model([dev("a")]))
    content = panel._content
    panel.set_dark(False)
    assert panel._content is not content
