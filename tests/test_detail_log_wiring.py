"""Detail log wiring (minute timer, record toggle, open log), +N pill and settings entries."""
from PySide6.QtCore import QRect

from taskbar_battery_widget import app as app_mod
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language, tr_in
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.tray import TrayController


class FakeLogger:
    def __init__(self, base):
        self.base = base
        self.ticks = []
        self.closed = False

    def tick(self, devices, enabled_keys):
        self.ticks.append(([d.id for d in devices], set(enabled_keys)))

    def path_for(self, device):
        return self.base / f"{device.id}.csv"

    def close(self):
        self.closed = True


DEVS = [Device("a", "A", "mouse", 80, True), Device("b", "B", "keyboard", 50, True)]


def test_ms_to_next_minute():
    assert app_mod.ms_to_next_minute(120.0) == 60000
    assert app_mod.ms_to_next_minute(150.25) == 29750
    assert app_mod.ms_to_next_minute(179.9999) == 1


def test_driver_aligns_and_filters_keys(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    lg = FakeLogger(tmp_path)
    drv = app_mod.DetailLogDriver(lg, cfg, clock=lambda: 1000.0 + 15.5)   # 1015.5 -> 4.5 s to go
    drv.set_devices(DEVS)
    drv.start()
    assert drv._first.isSingleShot() and drv._first.interval() == 4500
    assert drv._periodic.interval() == 60000 and not drv._periodic.isActive()
    drv.tick()
    assert lg.ticks[-1][1] == set()                    # global switch off -> nothing enabled
    cfg.set("detail_log_devices", [visibility_key("a")])
    drv.tick()
    assert lg.ticks[-1][1] == set()
    cfg.set("detail_log_enabled", True)
    drv.tick()
    assert lg.ticks[-1] == (["a", "b"], {visibility_key("a")})
    drv._on_first()
    assert drv._periodic.isActive()
    drv.stop()
    assert not drv._periodic.isActive()


def test_shutdown_closes_logger(qapp, tmp_path):
    class T:
        def stop(self): pass
        def hide(self): pass
        def close(self): pass
        def unlock(self): pass
    lg = FakeLogger(tmp_path)
    drv = app_mod.DetailLogDriver(lg, Config(tmp_path / "c.json"))
    t = T()
    app_mod.run_shutdown(t, t, t, t, t, t, (drv, lg))
    assert lg.closed


def test_record_toggle_persists_and_enables_global(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    tray.set_devices(DEVS)
    tray.open_panel()
    p = tray._panel
    assert p.show_logging
    ka, kb = visibility_key("a"), visibility_key("b")
    p.logging_toggled.emit(ka, True)
    assert cfg.get("detail_log_devices") == [ka] and cfg.get("detail_log_enabled") is True
    p.logging_toggled.emit(kb, True)
    assert cfg.get("detail_log_devices") == [ka, kb]
    p.logging_toggled.emit(ka, False)
    assert cfg.get("detail_log_devices") == [kb]
    p.close()


def test_open_log_opens_device_path_or_folder(tmp_path):
    lg = FakeLogger(tmp_path / "devs")
    opened = []
    key = visibility_key("a")
    app_mod.open_detail_log(lg, DEVS, key, opened.append)
    assert opened == [tmp_path / "devs"]                # file missing -> folder
    (tmp_path / "devs" / "a.csv").write_text("x")
    app_mod.open_detail_log(lg, DEVS, key, opened.append)
    assert opened[-1] == tmp_path / "devs" / "a.csv"
    app_mod.open_detail_log(lg, DEVS, "unknown", opened.append)
    assert len(opened) == 2


def test_open_log_signal_from_panel(qapp, tmp_path):
    tray = TrayController(Config(tmp_path / "c.json"))
    got = []
    tray.open_detail_log_requested.connect(got.append)
    tray.open_panel()
    tray._panel.open_log_requested.emit("k")
    assert got == ["k"]
    tray._panel.close()


def test_settings_logs_entries_bound_to_config(qapp, tmp_path):
    set_language("en")
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    folder = []
    tray.open_detail_folder_requested.connect(lambda: folder.append(1))
    st = [a.menu() for a in tray.menu.actions() if a.text() == "Settings"][0]
    logs = [a.menu() for a in st.actions() if a.text() == "Logs"][0]
    act = [a for a in logs.actions() if a.text() == "Detailed log (every minute)"][0]
    assert act.isCheckable() and not act.isChecked()
    act.setChecked(True)
    assert cfg.get("detail_log_enabled") is True
    [a for a in logs.actions() if a.text() == "Open detailed logs folder"][0].trigger()
    assert folder == [1]
    cfg.set("detail_log_enabled", False)
    tray._sync_menu()
    assert not act.isChecked()


def test_overflow_pill_click_requests_panel(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF

    class Cfg:
        def get(self, k):
            return {"low_threshold": 20}.get(k)
    wd = w.TaskbarWidget(Cfg())
    wd.set_devices([Device(str(i), f"D{i}", "mouse", 50, True) for i in range(3)])
    wd._all_pills = wd._all_pills
    # force an overflow pill by hand
    wd._pills = list(wd._all_pills[:1]) + [w._Pill("overflow", "+2", "", members=wd._all_pills[1:])]
    wd._rects = [(2, 2, 40, 24), (46, 2, 30, 24)]
    got = []
    wd.panel_requested.connect(got.append)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(50, 10), QPointF(50, 10),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    wd.mousePressEvent(ev)
    assert len(got) == 1 and isinstance(got[0], QRect) and got[0].width() == 30


def test_panel_strings_in_i18n():
    for lang in ("es", "en"):
        for k in ("panel_connected", "panel_known", "panel_show_in_bar", "panel_show_known",
                  "panel_record", "panel_open_log", "panel_empty", "menu_devices", "detail_log"):
            assert tr_in(lang, k) != k
    assert tr_in("es", "panel_connected") == "Conectados"
    assert tr_in("en", "panel_record", name="X") == "Record X to the detailed log"


def test_open_detail_log_reveals_existing_file_in_explorer(tmp_path):
    lg = FakeLogger(tmp_path / "devs")
    (tmp_path / "devs").mkdir()
    (tmp_path / "devs" / "a.csv").write_text("x")
    opened, revealed = [], []
    app_mod.open_detail_log(lg, DEVS, visibility_key("a"), opened.append, revealed.append)
    assert revealed == [tmp_path / "devs" / "a.csv"] and opened == []


def test_tray_opens_csv_in_notepad_without_association(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget import tray as tray_mod
    calls = []
    monkeypatch.setattr(tray_mod, "has_file_association", lambda ext: False)
    monkeypatch.setattr(tray_mod.subprocess, "Popen", lambda args, **kw: calls.append(args))
    t = tray_mod.TrayController.__new__(tray_mod.TrayController)
    f = tmp_path / "x.csv"
    tray_mod.TrayController.open_text_file(t, f)
    assert calls == [["notepad.exe", str(f)]]


def test_tray_opens_csv_with_associated_program(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget import tray as tray_mod
    opened = []
    monkeypatch.setattr(tray_mod, "has_file_association", lambda ext: True)
    t = tray_mod.TrayController.__new__(tray_mod.TrayController)
    monkeypatch.setattr(tray_mod.TrayController, "open_path", lambda self, p: opened.append(p))
    f = tmp_path / "x.csv"
    tray_mod.TrayController.open_text_file(t, f)
    assert opened == [f]


def test_recording_on_emits_detail_log_started(qapp, tmp_path):
    from taskbar_battery_widget import tray as tray_mod
    from taskbar_battery_widget.config import Config
    t = tray_mod.TrayController(Config(tmp_path / "c.json"))
    got = []
    t.detail_log_started.connect(got.append)
    t._on_logging("h:abc", True)
    t._on_logging("h:abc", False)
    assert got == ["h:abc"]
