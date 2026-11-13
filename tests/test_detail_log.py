import logging
import threading
import time

from taskbar_battery_widget import detail_log
from taskbar_battery_widget.detail_log import DetailLogger
from taskbar_battery_widget.models import Device, visibility_key

T0 = 1_700_000_040.0
MAC_ID = "BTHENUM\\DEV_AABBCCDDEEFF AA:BB:CC:DD:EE:FF"


def dev(id_=MAC_ID, name="Mouse AA:BB:CC:DD:EE:FF", battery=80, **kw):
    return Device(id=id_, name=name, kind="mouse", battery=battery, connected=True, **kw)


def make(tmp_path, **kw):
    return DetailLogger(tmp_path / "logs" / "devices", **kw)


def csvs(logger):
    return sorted(logger.base.glob("*.csv*")) if logger.base.exists() else []


def test_minute_dedupe_and_header_once(tmp_path):
    lg = make(tmp_path)
    d = dev()
    keys = {visibility_key(d.id)}
    t0 = 1_700_000_040.0  # exact minute boundary
    lg.tick([d], keys, now=t0)
    lg.tick([d], keys, now=t0 + 20)       # same minute -> skipped
    lg.tick([d], keys, now=t0 + 61)
    lg.close()
    (f,) = csvs(lg)
    lines = f.read_text().splitlines()
    assert lines[0] == detail_log.HEADER
    assert len(lines) == 3
    assert lines[1].split(",")[1:] == ["80", "0", "1", "bluetooth", "0", "0"]


def test_not_enabled_writes_nothing_and_unknown_fields_empty(tmp_path):
    lg = make(tmp_path)
    d = dev(battery=None)
    lg.tick([d], set(), now=T0)
    lg.close()
    assert csvs(lg) == []
    lg2 = make(tmp_path)
    lg2.tick([d], {visibility_key(d.id)}, now=T0)
    lg2.close()
    (f,) = csvs(lg2)
    assert f.read_text().splitlines()[1].split(",")[1] == ""


def test_rotation_keeps_at_most_backups_plus_one(tmp_path):
    lg = make(tmp_path, max_bytes=200, backups=3)
    d = dev()
    keys = {visibility_key(d.id)}
    for i in range(60):
        lg.tick([d], keys, now=1_700_000_000.0 + 60 * i)
    lg.close()
    files = csvs(lg)
    assert 2 <= len(files) <= 4
    assert not any(f.name.endswith(".4") for f in files)


def test_salt_persisted_and_names_clean(tmp_path):
    lg = make(tmp_path)
    d = dev()
    p1 = lg.path_for(d)
    assert not lg.base.exists()                     # path_for creates nothing... except the salt
    assert (tmp_path / ".detail_salt").exists()     # app data dir, not the logs folder
    assert not (tmp_path / "logs" / ".detail_salt").exists()
    lg2 = make(tmp_path)
    assert lg2.path_for(d) == p1
    assert lg2.path_for(dev(id_="other")) != p1
    name = p1.name.upper()
    assert "AABBCCDDEEFF" not in name and "AA:BB" not in name and "BTHENUM" not in name
    assert p1.name.endswith(".csv") and p1.name.isascii()
    lg.tick([d], {visibility_key(d.id)}, now=T0)
    lg.close()
    text = p1.read_text().upper()
    assert "AABBCCDDEEFF" not in text.replace(":", "") and "BTHENUM" not in text


def test_max_devices(tmp_path):
    lg = make(tmp_path, max_devices=2)
    ds = [dev(id_=f"id{i}", name=f"Dev{i}") for i in range(4)]
    lg.tick(ds, {visibility_key(d.id) for d in ds}, now=T0)
    lg.close()
    assert len([f for f in csvs(lg) if f.suffix == ".csv"]) == 2


def test_queue_full_does_not_raise(tmp_path, caplog, monkeypatch):
    lg = make(tmp_path)
    gate = threading.Event()
    monkeypatch.setattr(lg, "_write", lambda p, r: gate.wait(5))
    monkeypatch.setattr(detail_log, "QUEUE_SIZE", 1)
    lg._q = __import__("queue").Queue(maxsize=2)
    d = dev()
    keys = {visibility_key(d.id)}
    with caplog.at_level(logging.WARNING):
        for i in range(10):
            lg.tick([d], keys, now=T0 + 60 * i)
    assert any("queue full" in r.message for r in caplog.records)
    gate.set()
    lg.close()


def test_tick_does_not_block_on_slow_writer(tmp_path, monkeypatch):
    lg = make(tmp_path)
    gate = threading.Event()
    monkeypatch.setattr(lg, "_write", lambda p, r: gate.wait(5))
    d = dev()
    t = time.perf_counter()
    lg.tick([d], {visibility_key(d.id)}, now=T0)
    assert time.perf_counter() - t < 1.0
    gate.set()
    lg.close()


def test_close_flushes_and_is_idempotent(tmp_path):
    lg = make(tmp_path)
    ds = [dev(id_=f"i{i}", name=f"N{i}") for i in range(5)]
    lg.tick(ds, {visibility_key(d.id) for d in ds}, now=T0)
    lg.close()
    lg.close()
    assert len(csvs(lg)) == 5
    lg.tick(ds, {visibility_key(d.id) for d in ds}, now=T0 + 6000)  # after close: ignored, no error
    lg.close()


def test_io_error_swallowed_and_logged_once(tmp_path, caplog):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")                      # a file where the directory should be
    lg = DetailLogger(blocker / "devices")
    d = dev()
    keys = {visibility_key(d.id)}
    with caplog.at_level(logging.WARNING):
        for i in range(3):
            lg.tick([d], keys, now=T0 + 60 * i)
        lg.close()
    assert len([r for r in caplog.records if "cannot write" in r.message]) == 1
