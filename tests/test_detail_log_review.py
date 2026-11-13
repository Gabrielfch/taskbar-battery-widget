import logging
import os
import queue
import threading

from taskbar_battery_widget.detail_log import DetailLogger
from taskbar_battery_widget.models import Device, visibility_key

T0 = 1_700_000_040.0
MAC_ID = "BTHENUM\\DEV_AABBCCDDEEFF AA:BB:CC:DD:EE:FF"


def dev(id_=MAC_ID, name="Mouse", battery=80, **kw):
    return Device(id=id_, name=name, kind="mouse", battery=battery, connected=True, **kw)


def make(tmp_path, **kw):
    return DetailLogger(tmp_path / "logs" / "devices", **kw)


def csvs(logger):
    return sorted(logger.base.glob("*.csv*")) if logger.base.exists() else []


def test_live_and_absent_device_share_one_file(tmp_path):
    lg = make(tmp_path)
    live = dev()
    absent = dev(id_=visibility_key(MAC_ID))    # device_history rebuilds absent devices with the key as id
    assert lg.path_for(live) == lg.path_for(absent)


def test_legacy_salt_is_moved_out_of_logs(tmp_path):
    legacy = tmp_path / "logs" / ".detail_salt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("0123456789abcdef0123456789abcdef")
    lg = make(tmp_path)
    p = lg.path_for(dev())
    assert not legacy.exists()
    assert (tmp_path / ".detail_salt").read_text() == "0123456789abcdef0123456789abcdef"
    assert make(tmp_path).path_for(dev()) == p


def test_corrupt_or_oversized_salt_regenerated_with_warning(tmp_path, caplog):
    salt = tmp_path / ".detail_salt"
    for content in ("not-a-salt", "a" * 5000):
        salt.write_text(content)
        with caplog.at_level(logging.WARNING):
            make(tmp_path).path_for(dev())
        assert any("salt" in r.message for r in caplog.records)
        assert len(salt.read_text()) == 32
        caplog.clear()


def test_salt_write_failure_cleans_temp(tmp_path, monkeypatch, caplog):
    def boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(os, "replace", boom)
    lg = make(tmp_path)
    with caplog.at_level(logging.WARNING):
        lg.path_for(dev())
    assert list(tmp_path.glob(".salt*.tmp")) == []
    assert any("cannot persist salt" in r.message for r in caplog.records)


def test_stale_row_has_empty_battery_and_flag(tmp_path):
    lg = make(tmp_path)
    d = dev(battery=55)
    d.stale = True
    lg.tick([d], {visibility_key(d.id)}, now=T0)
    lg.close()
    (f,) = csvs(lg)
    lines = f.read_text().splitlines()
    assert lines[0].endswith(",idle,stale")
    cols = lines[1].split(",")
    assert cols[1] == "" and cols[-1] == "1"


def test_close_with_full_queue_still_sends_sentinel_and_flushes(tmp_path, monkeypatch):
    lg = make(tmp_path)
    lg._q = queue.Queue(maxsize=2)
    gate = threading.Event()
    real_write = lg._write
    monkeypatch.setattr(lg, "_write", lambda p, r: (gate.wait(5), real_write(p, r)))
    ds = [dev(id_=f"id{i}", name=f"Dev{i}") for i in range(6)]
    lg.tick(ds, {visibility_key(d.id) for d in ds}, now=T0)
    timer = threading.Timer(0.2, gate.set)
    timer.start()
    lg.close(timeout=3.0)
    timer.join()
    assert not lg._thread.is_alive()
    assert csvs(lg)


def test_no_rows_enqueued_after_close(tmp_path):
    lg = make(tmp_path)
    d = dev()
    lg.tick([d], {visibility_key(d.id)}, now=T0)
    lg.close()
    lg._enqueue((tmp_path / "x.csv", "row"))
    assert lg._q.empty()


def test_rotation_failure_keeps_appending(tmp_path, monkeypatch, caplog):
    lg = make(tmp_path, max_bytes=100, backups=2)

    def locked(*a, **k):
        raise PermissionError("file in use")
    monkeypatch.setattr(os, "replace", locked)
    d = dev()
    keys = {visibility_key(d.id)}
    with caplog.at_level(logging.WARNING):
        for i in range(10):
            lg.tick([d], keys, now=T0 + 60 * i)
        lg.close()
    (f,) = csvs(lg)
    assert len(f.read_text().splitlines()) == 11            # header + 10 rows, none lost
    assert len([r for r in caplog.records if "cannot rotate" in r.message]) == 1


def test_retention_deletes_old_csv_files(tmp_path):
    base = tmp_path / "logs" / "devices"
    base.mkdir(parents=True)
    now = 1_800_000_000.0
    old, fresh = now - 91 * 86400, now - 10 * 86400
    names = {"a_1.csv": old, "a_1.csv.1": old, "b_2.csv": fresh, "b_2.csv.2": fresh, "notes.txt": old}
    for n, mt in names.items():
        f = base / n
        f.write_text("x")
        os.utime(f, (mt, mt))
    lg = make(tmp_path)
    lg._clock = lambda: now
    d = dev()
    lg.tick([d], {visibility_key(d.id)}, now=now)
    lg.close()
    left = {f.name for f in base.iterdir()}
    assert "a_1.csv" not in left and "a_1.csv.1" not in left
    assert {"b_2.csv", "b_2.csv.2", "notes.txt"} <= left
