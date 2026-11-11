import json
import logging
import os
import re
import time

import pytest

from taskbar_battery_widget import app as app_mod
from taskbar_battery_widget import device_history as dh
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.popup import build_popup_content

MAC_ID = "BluetoothDevice#AA:BB:CC:DD:EE:FF"
T0 = time.time() - 86400.0


@pytest.fixture(autouse=True)
def _en():
    set_language("en")
    yield
    set_language("en")


def dev(battery=80, connected=True, id=MAC_ID, name="MX Keys", kind="keyboard", **kw):
    return Device(id=id, name=name, kind=kind, battery=battery, connected=connected, **kw)


def log_lines(tmp_path):
    p = dh.device_log_path(tmp_path)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def events(tmp_path):
    return [ln.split(" | ")[4] for ln in log_lines(tmp_path)]


# ------------------------------------------------------------------ log format / rotation
def test_log_line_format_has_no_mac(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(name="Keys AA:BB:CC:DD:EE:FF")], T0)
    h.close()
    (line,) = log_lines(tmp_path)
    parts = line.split(" | ")
    assert len(parts) == 7
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", parts[0])
    assert parts[1].endswith(f"[{visibility_key(MAC_ID)[2:8]}]")
    assert parts[2:] == ["keyboard", "bluetooth", "connected", "80%", "no"]
    assert "AA:BB" not in line and "DD:EE" not in line
    assert not re.search(r"[0-9A-Fa-f]{12}", line)


def test_log_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(dh, "LOG_MAX_BYTES", 400)
    monkeypatch.setattr(dh, "LOG_BACKUPS", 3)
    h = dh.DeviceHistory(tmp_path)
    for i in range(60):
        h.update([dev(id=f"d{i}")], T0 + i)
    h.close()
    logs = sorted(p.name for p in dh.logs_dir(tmp_path).iterdir())
    assert logs == ["devices.log", "devices.log.1", "devices.log.2", "devices.log.3"]


def test_redact_text():
    s = dh.redact_text("x AA:BB:CC:DD:EE:FF y aabbccddeeff \\\\?\\hid#vid_046d&pid_c52b#7&abc")
    assert "AA:BB" not in s and "aabbccddeeff" not in s and "hid#" not in s


@pytest.fixture
def pkg_logger():
    lg = logging.getLogger(app_mod.PKG_LOGGER)
    old = lg.level
    yield lg
    for h in list(lg.handlers):
        if getattr(h, "_tbw_app_log", False):
            lg.removeHandler(h)
            h.close()
    lg.setLevel(old)


def test_app_log_handler_redacts_and_levels(tmp_path, monkeypatch, pkg_logger):
    monkeypatch.delenv("TBW_DEBUG", raising=False)
    handler = app_mod.setup_logging(tmp_path)
    child = logging.getLogger("taskbar_battery_widget.t")
    child.info("device AA:BB:CC:DD:EE:FF seen\nFORGED line")
    child.debug("hidden-debug")
    logging.getLogger("thirdparty").info("third-party-info")
    handler.flush()
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "seen" in text and "AA:BB" not in text and "hidden-debug" not in text
    assert "third-party-info" not in text
    assert "seen\\nFORGED" in text and len(text.splitlines()) == 1
    assert handler.maxBytes == 1_000_000 and handler.backupCount == 3


def test_app_log_debug_env_and_root_untouched(tmp_path, monkeypatch, pkg_logger):
    monkeypatch.setenv("TBW_DEBUG", "1")
    root_level = logging.getLogger().level
    handler = app_mod.setup_logging(tmp_path)
    assert handler.level == logging.DEBUG
    assert pkg_logger.level == logging.DEBUG
    assert logging.getLogger().level == root_level != logging.DEBUG


def test_setup_logging_twice_one_handler(tmp_path, pkg_logger):
    app_mod.setup_logging(tmp_path)
    app_mod.setup_logging(tmp_path)
    assert sum(1 for h in pkg_logger.handlers if getattr(h, "_tbw_app_log", False)) == 1


def test_traceback_redacted_and_user_path(tmp_path, pkg_logger):
    handler = app_mod.setup_logging(tmp_path)
    try:
        raise ValueError("bad AA:BB:CC:DD:EE:FF")
    except ValueError:
        logging.getLogger("taskbar_battery_widget.t").exception(
            'failed C:\\Users\\bob\\x.py HID#VID_1&PID_2#7')
    handler.flush()
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "AA:BB" not in text and "bob" not in text and "HID#" not in text
    assert "Traceback" in text


def test_excepthook_logs_uncaught(tmp_path, pkg_logger, monkeypatch):
    import sys
    import threading
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    handler = app_mod.setup_logging(tmp_path)
    app_mod.install_excepthooks()
    try:
        raise RuntimeError("boom-main")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    def work():
        raise RuntimeError("boom-thread")
    t = threading.Thread(target=work)
    t.start()
    t.join()
    handler.flush()
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "boom-main" in text and "boom-thread" in text


# ------------------------------------------------------------------ events
def test_connected_disconnected(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev()], T0)
    h.update([dev()], T0 + 10)                 # no repeat
    h.update([dev(battery=None, connected=False)], T0 + 20)
    h.update([dev(battery=None, connected=False)], T0 + 30)
    h.update([dev()], T0 + 40)
    h.close()
    assert events(tmp_path) == ["connected", "disconnected", "connected"]


def test_vanished_device_logs_disconnected(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(link="2.4ghz")], T0)
    h.update([], T0 + 10)
    h.close()
    assert events(tmp_path) == ["connected", "disconnected"]


def test_battery_event_rate_limit_and_threshold(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(80)], T0)
    h.update([dev(79)], T0 + 60)               # < 5 min since baseline: silent
    h.update([dev(78)], T0 + 301)              # >= 5 min: logged
    h.update([dev(77)], T0 + 330)              # too soon again
    h.update([dev(20)], T0 + 340)              # crosses low: immediately
    h.update([dev(19)], T0 + 350)              # no new crossing, too soon
    h.close()
    assert events(tmp_path) == ["connected", "battery", "low", "battery"]
    assert log_lines(tmp_path)[-1].split(" | ")[5] == "20%"


def test_full_crossing_and_charging(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(98)], T0)
    h.update([dev(99, charging=True)], T0 + 5)
    h.update([dev(100, charging=True)], T0 + 10)       # crosses full: battery despite <5 min
    h.update([dev(100, charging=False)], T0 + 15)
    h.close()
    assert events(tmp_path) == ["connected", "charging_started", "battery", "charging_stopped"]


def test_low_hysteresis(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(30)], T0)
    h.update([dev(20)], T0 + 1)
    h.update([dev(21)], T0 + 2)
    h.update([dev(20)], T0 + 3)                # still inside the hysteresis band
    h.close()
    assert events(tmp_path).count("low") == 1


# ------------------------------------------------------------------ last_known.json
def test_persistence_roundtrip_and_no_mac(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(56, charging=True)], T0)
    h.update([dev(None, connected=False)], T0 + 5)
    h.close()
    raw = dh.last_known_path(tmp_path).read_text(encoding="utf-8")
    assert "AA:BB" not in raw and "BluetoothDevice" not in raw
    data = json.loads(raw)
    ent = data["devices"][visibility_key(MAC_ID)]
    assert ent["battery"] == 56 and ent["name"] == "MX Keys" and ent["disc"] == T0 + 5
    assert ent["ts"] == T0 and ent["charging"] is True
    h2 = dh.DeviceHistory(tmp_path)
    out = h2.update([dev(None, connected=False)], T0 + 100)
    assert out[0].battery == 56 and out[0].stale and out[0].last_seen == T0


def test_purge_old_and_cap(tmp_path, monkeypatch):
    now = 2_000_000_000.0
    ents = {f"h:{i:016x}": {"name": f"D{i}", "kind": "mouse", "link": "2.4ghz", "battery": 50,
                            "charging": False, "ts": now - i, "disc": None, "seen": now - i}
            for i in range(250)}
    ents["h:" + "f" * 16] = {"name": "Old", "kind": "mouse", "link": "2.4ghz", "battery": 50,
                             "charging": False, "ts": now - 91 * 86400, "disc": None,
                             "seen": now - 91 * 86400}
    dh.last_known_path(tmp_path).write_text(json.dumps({"version": 1, "devices": ents}), encoding="utf-8")
    monkeypatch.setattr(dh.time, "time", lambda: now)  # also patches time.time globally for this test
    h = dh.DeviceHistory(tmp_path)
    h.update([dev()], now)                      # a change forces a save
    h.flush(now)
    data = json.loads(dh.last_known_path(tmp_path).read_text(encoding="utf-8"))["devices"]
    assert len(data) == dh.MAX_ENTRIES
    assert "h:" + "f" * 16 not in data
    assert "h:" + f"{0:016x}" in data and "h:" + f"{249:016x}" not in data


def test_corrupt_file_ignored(tmp_path):
    dh.last_known_path(tmp_path).write_text("{not json", encoding="utf-8")
    h = dh.DeviceHistory(tmp_path)
    assert h.update([dev()], T0)[0].battery == 80


def test_atomic_save_leaves_no_temp(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev()], T0)
    h.close()
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_timestamp_only_changes_are_throttled(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev()], T0)                       # new entry -> saved
    p = dh.last_known_path(tmp_path)
    before = p.read_text(encoding="utf-8")
    h.update([dev()], T0 + 10)                  # only 'seen'/'ts' move
    assert p.read_text(encoding="utf-8") == before
    h.update([dev()], T0 + 700)
    assert json.loads(p.read_text(encoding="utf-8"))["devices"][visibility_key(MAC_ID)]["ts"] == T0 + 700


# ------------------------------------------------------------------ enrichment
def test_stale_for_disconnected_and_none_battery(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(56)], T0)
    out = h.update([dev(None, connected=False)], T0 + 7200)
    assert (out[0].battery, out[0].stale, out[0].connected, out[0].last_seen) == (56, True, False, T0)
    out = h.update([dev(None, connected=True)], T0 + 7300)
    assert (out[0].battery, out[0].stale, out[0].connected) == (56, True, True)
    out = h.update([dev(60)], T0 + 7400)
    assert out[0].battery == 60 and not out[0].stale


def test_unknown_device_not_enriched(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    out = h.update([dev(None, connected=False)], T0)
    assert out[0].battery is None and not out[0].stale


def test_absent_wireless_device_added_when_enabled(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    mouse = dev(70, id="hid:mouse1", name="G Pro", kind="mouse", link="2.4ghz")
    h.update([mouse], T0)
    h.close()
    h2 = dh.DeviceHistory(tmp_path)
    assert h2.update([], T0 + 3600) == []
    out = h2.update([], T0 + 3600, show_disconnected=True)
    assert len(out) == 1
    d = out[0]
    assert (d.name, d.battery, d.connected, d.stale, d.link) == ("G Pro", 70, False, True, "2.4ghz")
    assert visibility_key(d.id) == visibility_key("hid:mouse1")


def test_absent_not_added_when_hidden_or_present_or_bluetooth(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    mouse = dev(70, id="hid:mouse1", name="G Pro", kind="mouse", link="2.4ghz")
    bt = dev(50)
    h.update([mouse, bt], T0)
    key = visibility_key("hid:mouse1")
    assert h.update([], T0 + 5, show_disconnected=True, hidden={key}) == []     # hidden; BT not added
    out = h.update([mouse], T0 + 6, show_disconnected=True)                      # present: no duplicate
    assert len(out) == 1
    assert len(h.update([bt], T0 + 7, show_disconnected=True)) == 2


def test_new_id_for_same_device_does_not_duplicate(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(70, id="hid:old", name="G Pro", kind="mouse", link="2.4ghz")], T0)
    h.close()
    h2 = dh.DeviceHistory(tmp_path)
    new = dev(65, id="hid:new", name="G Pro", kind="mouse", link="2.4ghz")
    for i in range(3):
        out = h2.update([new], T0 + 100 + i, show_disconnected=True)
        assert len(out) == 1 and out[0].battery == 65


# ------------------------------------------------------------------ alerts / UI
def test_stale_never_alerts(tmp_path):
    cfg = Config(tmp_path / "c.json")
    am = AlertManager(cfg)
    low = dev(5, stale=True, last_seen=T0)
    assert am.check([low], T0 + 10) == []
    assert am.check([dev(5, connected=False, stale=True)], T0 + 20) == []
    assert len(am.check([dev(5)], T0 + 30)) == 1       # a real reading still alerts


class FakeCfg:
    def __init__(self, **kw):
        self.d = {"low_threshold": 20, "show_disconnected": True, "show_when_empty": False}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


def test_pill_shows_dimmed_last_value(qapp):
    wd = w.TaskbarWidget(FakeCfg())
    d = dev(8, connected=False, stale=True, last_seen=T0)      # 8 % would be 'low' if it were live
    wd.set_devices([d])
    assert wd.pill_texts() == ["8%"]
    p = wd._all_pills[0]
    assert p.dim and not p.alert and not p.low and p.level is None
    assert "last value 8%" in p.tooltip


def test_pill_without_history_still_dashes(qapp):
    wd = w.TaskbarWidget(FakeCfg())
    wd.set_devices([dev(None, connected=False)])
    assert wd.pill_texts() == ["--"]


def test_popup_line_en_and_es():
    d = dev(56, connected=False, stale=True, last_seen=T0)
    c = build_popup_content(d, 20, now=T0 + 2 * 3600)
    assert c.lines[1] == "Disconnected \u00b7 last value 56% 2 h ago"
    assert c.dot_band is None and len(c.lines) == 2
    assert build_popup_content(d, 20, now=T0 + 5 * 60).lines[1].endswith("5 min ago")
    assert build_popup_content(d, 20, now=T0 + 3 * 86400).lines[1].endswith("3 days ago")
    set_language("es")
    assert build_popup_content(d, 20, now=T0 + 2 * 3600).lines[1] == \
        "Desconectado \u00b7 \u00faltimo valor 56 % hace 2 h"
    assert build_popup_content(d, 20, now=T0 + 3 * 86400).lines[1].endswith("hace 3 d\u00edas")


def test_popup_connected_unknown_stale():
    d = dev(40, connected=True, stale=True, last_seen=T0)
    assert build_popup_content(d, 20, now=T0 + 3600).lines[1] == \
        "Battery unknown \u00b7 last value 40% 1 h ago"


def test_tray_menu_entries(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget import tray as tray_mod
    opened = []
    monkeypatch.setattr(tray_mod.os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    t = tray_mod.TrayController(Config(tmp_path / "c.json"))
    texts = [a.text() for a in t.menu.actions()]
    assert "Open device log" in texts and "Open logs folder" in texts
    t.open_device_log()
    t.open_logs_folder()
    assert opened[0].endswith("devices.log") and opened[1].endswith("logs")
    assert os.path.isdir(opened[1])


# ------------------------------------------------------------------ hardening (review round)
def test_three_sessions_id_change_no_ghost(tmp_path):
    a = dev(70, id="hid:A", name="G Pro", kind="mouse", link="2.4ghz")
    b = dev(None, id="hid:B", name="G Pro", kind="mouse", link="2.4ghz", connected=False)
    c = dev(55, id="hid:C", name="G Pro", kind="mouse", link="2.4ghz")
    h = dh.DeviceHistory(tmp_path)
    h.update([a], T0)
    h.close()
    h = dh.DeviceHistory(tmp_path)
    out = h.update([b], T0 + 100, show_disconnected=True)
    assert len(out) == 1 and out[0].battery == 70 and out[0].stale      # inherits the last value
    h.close()
    h = dh.DeviceHistory(tmp_path)
    out = h.update([c], T0 + 200, show_disconnected=True)
    assert [d.battery for d in out] == [55]
    h.close()
    h = dh.DeviceHistory(tmp_path)
    assert len(h._known) == 1
    out = h.update([], T0 + 300, show_disconnected=True)                 # only one dimmed entry
    assert len(out) == 1 and out[0].battery == 55


def test_absent_suppressed_when_same_triple_present(tmp_path):
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(70, id="hid:A", name="G", kind="mouse", link="2.4ghz")], T0)
    h.close()
    h = dh.DeviceHistory(tmp_path)
    for i in range(2):
        out = h.update([dev(60, id="hid:B", name="G", kind="mouse", link="2.4ghz")],
                       T0 + 10 + i, show_disconnected=True)
        assert len(out) == 1


def _write_known(tmp_path, text):
    p = dh.last_known_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


KEY = visibility_key("x")


@pytest.mark.parametrize("payload", [
    "[" * 100000,
    '{"devices": ' + "[" * 100000,
    '{"version":1,"devices":{"%s":{"name":"a","ts":1e400}}}' % KEY,
    '{"version":1,"devices":{"%s":{"name":"a","ts":NaN}}}' % KEY,
    '{"version":1,"devices":{"%s":{"name":"a","ts":Infinity}}}' % KEY,
    '{"version":1,"devices":{"%s":{"name":"a","ts":1%s}}}' % (KEY, "0" * 400),
    '{"version":1,"devices":{"not-a-key":{"name":"a","battery":50}}}',
    '{"version":1,"devices":{"%s":{"name":"a","battery":101}}}' % KEY,
    '{"version":1,"devices":{"%s":{"name":"a","battery":50.5}}}' % KEY,
    '{"version":1,"devices":{"%s":{"name":"a","ts":-5}}}' % KEY,
])
def test_hostile_last_known_gives_empty_store(tmp_path, payload):
    _write_known(tmp_path, payload)
    h = dh.DeviceHistory(tmp_path)
    assert h._known == {}
    assert h.update([dev()], T0) != []


def test_huge_last_known_ignored(tmp_path):
    entry = '"%s":{"name":"a","battery":50,"ts":%s}' % (KEY, T0)
    _write_known(tmp_path, '{"devices":{' + entry + "," + " " * (dh.MAX_FILE_BYTES + 1) + "}}")
    assert dh.DeviceHistory(tmp_path)._known == {}


def test_valid_entry_survives_and_invalid_filtered(tmp_path):
    _write_known(tmp_path, json.dumps({"devices": {
        KEY: {"name": "ok", "kind": "mouse", "link": "usb", "battery": 40, "ts": T0, "seen": T0},
        "bad": {"name": "no", "battery": 10, "ts": T0}}}))
    h = dh.DeviceHistory(tmp_path)
    assert list(h._known) == [KEY]


def test_orphan_tmp_files_cleaned(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    orphan = tmp_path / "last_known.abc123.tmp"
    orphan.write_text("x")
    dh.DeviceHistory(tmp_path)
    assert not orphan.exists()


@pytest.mark.parametrize("raw", [
    "AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "aabb.ccdd.eeff", "aabbccddeeff", "AABBCCDDEEFF",
    "MouseAABBCCDDEEFF", "KeysAA:BB:CC:DD:EE:FFx",
])
def test_redact_mac_formats(raw):
    out = dh.redact_text("dev " + raw + " end")
    assert "[mac]" in out and not re.search(r"[0-9A-Fa-f]{4}", out.replace("dev", "").replace("end", ""))


@pytest.mark.parametrize("raw", [
    "HID#VID_046D&PID_C52B#7&2a&0&0000", "\\\\?\\hid#vid_046d&pid_c52b", "BTHENUM#Dev_AABB#x",
    "USB\\VID_1&PID_2\\5&1", "SWD#X#Y",
])
def test_redact_paths(raw):
    out = dh.redact_text("see " + raw)
    assert out == "see [hid-path]"


def test_redact_user_path():
    assert dh.redact_text('File "C:\\Users\\bob\\app.py", line 3') == 'File "C:\\Users\\[user]\\app.py", line 3'


def test_name_redacted_before_truncation_and_marker_stable(tmp_path):
    long_name = "x" * 40 + " AA:BB:CC:DD:EE:FF"             # the MAC straddles the 48-char cut
    h = dh.DeviceHistory(tmp_path)
    h.update([dev(name=long_name)], T0)
    name = h._known[visibility_key(MAC_ID)]["name"]
    assert "AA" not in name and "BB:CC" not in name
    h.close()
    h2 = dh.DeviceHistory(tmp_path)
    assert h2._known[visibility_key(MAC_ID)]["name"] == name
    h2.update([dev(name=long_name)], T0 + 1)
    assert not h2._dirty
    h3 = dh.DeviceHistory(tmp_path)
    h3.update([dev(name="Keys AA:BB:CC:DD:EE:FF")], T0)
    h3.close()
    h4 = dh.DeviceHistory(tmp_path)
    k = visibility_key(MAC_ID)
    before = h4._known[k]["name"]
    h4.update([dev(name="Keys AA:BB:CC:DD:EE:FF")], T0 + 1)
    assert h4._known[k]["name"] == before and "[mac]" in before and not h4._dirty


def test_failed_save_backs_off_and_warns_rarely(tmp_path, monkeypatch, caplog):
    h = dh.DeviceHistory(tmp_path)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("disk full")
    monkeypatch.setattr(dh.tempfile, "mkstemp", boom)
    with caplog.at_level(logging.WARNING, logger=dh.log.name):
        h.update([dev(70)], T0)                     # dirty -> tries once and fails
        for i in range(1, 20):
            h.update([dev(70 + i % 2)], T0 + i)     # keeps changing, but within the back-off
        assert len(calls) == 1
        h.update([dev(50)], T0 + dh.SAVE_RETRY_S + 1)
        assert len(calls) == 2
    assert sum("could not save" in r.message for r in caplog.records) == 1


def test_update_exception_warning_rate_limited(tmp_path, monkeypatch, caplog):
    h = dh.DeviceHistory(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(h, "_update", boom)
    with caplog.at_level(logging.ERROR, logger=dh.log.name):
        for i in range(5):
            assert h.update([dev()], T0 + i)[0].battery == 80
    assert len(caplog.records) == 1


def test_no_fsync_in_save(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.os, "fsync", lambda fd: pytest.fail("fsync on the UI thread"))
    h = dh.DeviceHistory(tmp_path)
    h.update([dev()], T0)
    assert dh.last_known_path(tmp_path).exists()


def test_format_ago_days():
    assert dh.format_ago(30 * 3600) == "1 day ago"
    assert dh.format_ago(23 * 3600) == "23 h ago"
    assert dh.format_ago(3 * 86400) == "3 days ago"
    set_language("es")
    assert dh.format_ago(30 * 3600) == "hace 1 d\u00eda"


def test_visibility_key_idempotent():
    k = visibility_key("some id")
    assert visibility_key(k) == k


def test_tray_device_line_and_icon_with_stale(qapp, tmp_path):
    from taskbar_battery_widget import tray as tray_mod
    stale = dev(30, connected=False, stale=True, last_seen=T0)
    assert tray_mod.TrayController._device_line(stale) == "MX Keys: Disconnected \u00b7 30 %" \
        or "30 %" in tray_mod.TrayController._device_line(stale)
    t = tray_mod.TrayController(Config(tmp_path / "c.json"))
    t._devices = [stale, dev(55, id="other")]
    t._update_icon()                                  # stale must not count as the lowest level
    assert "55%" in t._tray.toolTip()
    t._devices = [stale]
    t._update_icon()
    assert "%" not in t._tray.toolTip()


def test_tray_open_failure_notifies(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget import tray as tray_mod
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def fail(p):
        raise OSError("no handler")
    monkeypatch.setattr(tray_mod.os, "startfile", fail, raising=False)
    t = tray_mod.TrayController(Config(tmp_path / "c.json"))
    msgs = []
    monkeypatch.setattr(t, "notify", lambda title, body: msgs.append(body))
    t.open_device_log()
    assert msgs and msgs[0].endswith("devices.log")
