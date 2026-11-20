"""User-defined device order: config, ordering function, panel / pills, keyboard, menu, drag and drop."""
import json

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent, QGuiApplication
from PySide6.QtTest import QTest

from taskbar_battery_widget import device_order as do
from taskbar_battery_widget import i18n
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import DEFAULTS, Config
from taskbar_battery_widget.device_panel import ROW_MIME
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.placement import ScreenInfo
from taskbar_battery_widget.tray import TrayController

P = ScreenInfo("aaa", "\\\\.\\DISPLAY1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), 1.0, True)


def dev(i, name=None, connected=True, kind="mouse"):
    return Device(i, name or i.upper(), kind, 50, connected)


def K(i):
    return visibility_key(i)


C = [dev("a"), dev("b"), dev("c")]
KNOWN = [dev("x", connected=False), dev("y", connected=False), dev("z", connected=False)]


def keys(rows):
    return [r.key for r in rows]


# ---------------------------------------------------------------- config
def test_config_default_and_validation(tmp_path):
    assert DEFAULTS["device_order"] == []
    p = tmp_path / "c.json"
    cfg = Config(p)
    assert cfg.get("device_order") == []
    cfg.set("device_order", [K("a"), K("b")])
    assert Config(p).get("device_order") == [K("a"), K("b")]
    for bad in ("a", [1], [""], ["x" * 201], ["h:1"] * 201, None, {"a": 1}):
        with pytest.raises(ValueError):
            cfg.set("device_order", bad)
    p.write_text(json.dumps({"device_order": [K("a")] * 201}), encoding="utf-8")
    assert Config(p).get("device_order") == []              # invalid file value: default
    p.write_text(json.dumps({"low_threshold": 20}), encoding="utf-8")
    assert Config(p).get("device_order") == []              # old config without the key


# ---------------------------------------------------------------- ordering function
def test_apply_order_listed_unknown_and_new():
    devs = [dev("a"), dev("b"), dev("c"), dev("d")]
    out = do.apply_order(devs, [K("c"), "h:unknown", K("a")])
    assert [d.id for d in out] == ["c", "a", "b", "d"]       # listed first, the rest in today's order
    assert [d.id for d in do.apply_order(devs, [])] == ["a", "b", "c", "d"]
    assert [d.id for d in do.apply_order(devs, [K("d"), K("d")])] == ["d", "a", "b", "c"]


def test_order_is_stable_on_connect_and_hide():
    order = [K("c"), K("a"), K("b")]
    base = [dev("a"), dev("b"), dev("c")]
    assert [d.id for d in do.apply_order(base, order)] == ["c", "a", "b"]
    gone = [dev("a"), dev("c")]                              # b disconnected / disappeared
    assert [d.id for d in do.apply_order(gone, order)] == ["c", "a"]
    back = [dev("c"), dev("b"), dev("a")]                    # monitor delivers another order: ignored
    assert [d.id for d in do.apply_order(back, order)] == ["c", "a", "b"]


def test_reorder_keeps_other_sections_and_hidden_keys_in_place():
    order = [K("c"), K("q"), K("a"), K("b")]                 # q: a device not shown now
    shown = [K("c"), K("a"), K("b"), K("x"), K("y")]
    new = do.reorder(order, shown, [K("c"), K("a"), K("b")], K("b"), 0)
    assert new == [K("b"), K("q"), K("c"), K("a"), K("x"), K("y")]
    new2 = do.reorder([], shown, [K("x"), K("y")], K("x"), 1)
    assert new2 == [K("c"), K("a"), K("b"), K("y"), K("x")]
    assert len(do.reorder([f"h:{i}" for i in range(250)], shown, [K("c"), K("a")], K("c"), 1)) <= do.MAX_ORDER


# ---------------------------------------------------------------- panel model
def test_panel_model_sections_follow_order_and_hide_does_not_reorder():
    devs = C + KNOWN
    order = [K("c"), K("a"), K("z"), K("x")]
    m = build_panel_model(devs, set(), True, 20, True, order=order)
    assert keys(m.connected) == [K("c"), K("a"), K("b")]
    assert keys(m.known) == [K("z"), K("x"), K("y")]
    hidden = build_panel_model(devs, {K("a"), K("z")}, True, 20, True, order=order)
    assert keys(hidden.connected) == keys(m.connected) and keys(hidden.known) == keys(m.known)
    swapped = build_panel_model(
        [dev("b"), dev("a", connected=False), dev("c")], set(), True, 20, True, order=order)
    assert keys(swapped.connected) == [K("c"), K("b")]       # a disconnected: the others keep their order
    assert keys(swapped.known) == [K("a")]


# ---------------------------------------------------------------- pills
def make_widget(tmp_path, monkeypatch, order):
    monkeypatch.setattr(w.taskbar, "is_fullscreen_on_monitor", lambda name: False)
    monkeypatch.setattr(w.taskbar, "is_fullscreen_foreground", lambda rect: False)
    monkeypatch.setattr(w.taskbar, "get_taskbar_infos", lambda: [])
    cfg = Config(tmp_path / "c.json")
    cfg.set("animations", "off")
    cfg.set("device_order", order)
    wd = w.TaskbarWidget(cfg)
    wd._screens = lambda: [(P, QGuiApplication.primaryScreen())]
    return wd, cfg


def pill_names(wd):
    return [p.device.name for p in wd._pills if p.device is not None]


def test_pills_follow_order_and_apply_live(qapp, tmp_path, monkeypatch):
    wd, cfg = make_widget(tmp_path, monkeypatch, [K("c"), K("a")])
    wd.set_devices(list(C))
    assert pill_names(wd) == ["C", "A", "B"]
    cfg.set("device_order", [K("b")])
    wd.set_devices(list(C))                                  # same scan, only the order changed
    assert pill_names(wd) == ["B", "A", "C"]
    cfg.set("hidden_devices", [K("b")])
    wd.set_devices(list(C))
    assert pill_names(wd) == ["A", "C"]


# ---------------------------------------------------------------- tray + panel
def make_tray(tmp_path, devs=None):
    t = TrayController(Config(tmp_path / "c.json"))
    t.set_devices(list(devs or (C + KNOWN)))
    t._cfg.set("show_disconnected", True)
    t.open_panel()
    return t


def rows(t, section="connected"):
    m = t._panel._model
    return keys(getattr(m, section))


def focused_key(t):
    f = t._panel.focusWidget()
    return t._panel._key_of(f) if f is not None else None


def test_alt_down_and_up_move_row_persist_and_keep_focus(qapp, tmp_path):
    t = make_tray(tmp_path)
    p = t._panel
    redraws = []
    t.ui_settings_changed.connect(lambda: redraws.append(1))
    assert rows(t) == [K("a"), K("b"), K("c")]
    eye = p._buttons[(K("a"), "eye")]
    name_before = eye.accessibleName()
    eye.setFocus()
    QTest.keyClick(eye, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert rows(t) == [K("b"), K("a"), K("c")]
    assert t._cfg.get("device_order")[:3] == [K("b"), K("a"), K("c")]
    assert redraws                                           # taskbar redraw requested, no rescan signal
    assert focused_key(t) == K("a")                          # focus stays on the moved row
    f = p.focusWidget()
    QTest.keyClick(f, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert rows(t) == [K("a"), K("b"), K("c")]
    assert focused_key(t) == K("a")
    f = p.focusWidget()
    QTest.keyClick(f, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)      # already first: nothing happens
    assert rows(t) == [K("a"), K("b"), K("c")]
    assert p._buttons[(K("a"), "eye")].accessibleName() == name_before      # accessible names unchanged


def test_alt_move_in_known_section_leaves_connected_alone(qapp, tmp_path):
    t = make_tray(tmp_path)
    p = t._panel
    btn = p._buttons[(K("z"), "eye")]
    btn.setFocus()
    QTest.keyClick(btn, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert rows(t, "known") == [K("x"), K("z"), K("y")]
    assert rows(t) == [K("a"), K("b"), K("c")]
    QTest.keyClick(p.focusWidget(), Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert rows(t, "known") == [K("z"), K("x"), K("y")]


def test_context_menu_move_up_down_disabled_at_ends(qapp, tmp_path):
    t = make_tray(tmp_path)
    p = t._panel
    menu = p._row_menu(K("a"))
    up, down = menu.actions()
    assert not up.isEnabled() and down.isEnabled()
    last = p._row_menu(K("c")).actions()
    assert last[0].isEnabled() and not last[1].isEnabled()
    menu.actions()[1].trigger()                              # a: Move down
    assert rows(t) == [K("b"), K("a"), K("c")]
    mid = p._row_menu(K("a"))
    assert all(a.isEnabled() for a in mid.actions())
    assert [a.text() for a in mid.actions()] == [i18n.tr("panel_move_up"), i18n.tr("panel_move_down")]
    mid.actions()[0].trigger()
    assert rows(t) == [K("a"), K("b"), K("c")]


def drop(panel, src, target, after):
    row = next(r for r in panel.findChildren(w.QWidget) if getattr(r, "key", None) == target
               and r.objectName() == "row")
    md = QMimeData()
    md.setData(ROW_MIME, src.encode("utf-8"))
    y = row.height() * (0.75 if after else 0.25)
    ev = QDropEvent(QPointF(10, y), Qt.DropAction.MoveAction, md, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
    row.dropEvent(ev)
    return ev


def test_drag_and_drop_reorders_within_section(qapp, tmp_path):
    t = make_tray(tmp_path)
    p = t._panel
    ev = drop(p, K("a"), K("c"), after=True)
    assert ev.isAccepted()
    assert rows(t) == [K("b"), K("c"), K("a")]
    drop(p, K("a"), K("b"), after=False)
    assert rows(t) == [K("a"), K("b"), K("c")]
    ev = drop(p, K("a"), K("x"), after=False)                # other section: refused
    assert not ev.isAccepted()
    assert rows(t) == [K("a"), K("b"), K("c")] and rows(t, "known") == [K("x"), K("y"), K("z")]
    drop(p, K("z"), K("x"), after=False)
    assert rows(t, "known") == [K("z"), K("x"), K("y")]
    assert rows(t) == [K("a"), K("b"), K("c")]


def test_drop_indicator_line_is_shown_and_cleared(qapp, tmp_path):
    t = make_tray(tmp_path)
    p = t._panel
    row = next(r for r in p.findChildren(w.QWidget) if getattr(r, "key", None) == K("b") and r.objectName() == "row")
    md = QMimeData()
    md.setData(ROW_MIME, K("a").encode())
    from PySide6.QtGui import QDragMoveEvent
    ev = QDragMoveEvent(QPointF(10, row.height() - 2).toPoint(), Qt.DropAction.MoveAction, md,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    row.dragMoveEvent(ev)
    assert row._edge == "bottom" and ev.isAccepted()
    row.dragLeaveEvent(None)
    assert row._edge is None


def test_pills_and_panel_share_the_order_from_the_panel(qapp, tmp_path, monkeypatch):
    t = make_tray(tmp_path)
    wd, _ = make_widget(tmp_path, monkeypatch, [])
    wd._cfg = t._cfg
    wd.set_devices(list(C))
    assert pill_names(wd) == ["A", "B", "C"]
    t._panel._buttons[(K("c"), "eye")].setFocus()
    QTest.keyClick(t._panel.focusWidget(), Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    wd.set_devices(list(C))
    assert pill_names(wd) == ["A", "C", "B"] and rows(t) == [K("a"), K("c"), K("b")]


def test_eye_and_connect_do_not_reorder(qapp, tmp_path):
    t = make_tray(tmp_path)
    t._cfg.set("device_order", [K("c"), K("b"), K("a")])
    t._refresh_panel()
    t._panel._buttons[(K("b"), "eye")].click()               # hide
    assert rows(t) == [K("c"), K("b"), K("a")]
    t.set_devices([dev("a"), dev("b", connected=False), dev("c")] + KNOWN)     # b disconnects
    assert rows(t) == [K("c"), K("a")] and K("b") in rows(t, "known")
    t.set_devices(list(C) + KNOWN)                           # b connects again
    assert rows(t) == [K("c"), K("b"), K("a")]


def test_forget_removes_key_from_order(qapp, tmp_path):
    t = make_tray(tmp_path)
    t._cfg.set("device_order", [K("y"), K("a"), K("x")])
    t._on_forget(K("y"), False)
    assert t._cfg.get("device_order") == [K("a"), K("x")]
    t._on_forget(K("x"), False)
    assert t._cfg.get("device_order") == [K("a")]


def test_reorder_redraws_without_rescan(qapp, tmp_path):
    t = make_tray(tmp_path)
    got = []
    t.ui_settings_changed.connect(lambda: got.append("ui"))
    t.settings_changed.connect(lambda: got.append("scan"))
    t._panel.move_row(K("a"), 1)
    assert got == ["ui"]
