"""Tests for the per-device statistics window (offscreen Qt)."""
from __future__ import annotations

import datetime as dt
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtTest import QTest

from taskbar_battery_widget import stats_window as sw
from taskbar_battery_widget.stats_window import StatsWindow, StatsWindows, format_duration

HEADER = "time_iso,battery,charging,connected,link,idle,stale"


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def _row(t, battery, charging=0, connected=1):
    return f"{_iso(t)},{battery},{charging},{connected},wired,0,0"


def make_csv(path, days=3):
    """Per day: 10 h of 5-min samples draining 100 -> 50, then 1 h charging back to 100, then a gap."""
    now = time.time()
    rows = []
    for d in range(days, 0, -1):
        t = now - d * 86400
        for i in range(120):                       # 10 h
            rows.append(_row(t + i * 300, 100 - i * 50 // 119))
        t2 = t + 120 * 300
        for i in range(12):                        # 1 h charging
            rows.append(_row(t2 + i * 300, 50 + i * 50 // 11, charging=1))
    path.write_text("\n".join([HEADER] + rows) + "\n", encoding="utf-8")


def wait_loaded(qapp, win, timeout=10.0):
    end = time.time() + timeout
    while win._stats is None and time.time() < end:
        qapp.processEvents()
        QTest.qWait(10)
    assert win._stats is not None


@pytest.mark.parametrize("hours,text", [
    (None, "—"), (0, "0 min"), (0.75, "45 min"), (5.5, "5 h 30 min"), (5.0, "5 h"),
    (28.0, "1 d 4 h"), (24.0, "1 d"),
])
def test_format_duration(hours, text):
    assert format_duration(hours) == text


def test_cards_and_painting(qapp, tmp_path):
    csv = tmp_path / "dev.csv"
    make_csv(csv)
    win = StatsWindow("Mouse", csv)
    wait_loaded(qapp, win)
    st = win._stats
    assert st.charge_count == 3
    assert win.has_data()
    win.show()
    assert win.windowTitle().endswith("Mouse")
    assert win.cards["charges"].value.text() == "3"
    assert win.cards["last_charge"].value.text() != "—"
    assert win.cards["rate"].value.text().endswith("%/h")
    assert win.cards["runtime"].value.text() != "—"
    assert win.cards["charge_time"].value.text().endswith("min") or "h" in win.cards["charge_time"].value.text()
    assert win.header_label.text()
    orig = qapp.palette()
    for dark in (False, True):
        pal = qapp.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#202020" if dark else "#f3f3f3"))
        qapp.setPalette(pal)
        for rid in sw.RANGES:
            win.set_range(rid)
            assert not win.grab().isNull()
            for ch in (win.level_chart, win.drop_chart, win.charges_chart):
                assert not ch.grab().isNull()
                assert ch.accessibleDescription()
    qapp.setPalette(orig)
    win.close()


def test_empty_state_missing_csv(qapp, tmp_path):
    win = StatsWindow("Mouse", tmp_path / "nope.csv")
    wait_loaded(qapp, win)
    win.show()
    assert not win.has_data()
    assert not win.empty_label.isHidden()
    assert win.scroll.isHidden()
    assert not win.grab().isNull()
    win.close()


def test_cards_dash_when_none(qapp, tmp_path):
    csv = tmp_path / "few.csv"
    now = time.time()
    csv.write_text("\n".join([HEADER] + [_row(now - 3600 + i * 60, 80) for i in range(30)]) + "\n")
    win = StatsWindow("Mouse", csv)
    wait_loaded(qapp, win)
    assert win.has_data()
    assert win.cards["rate"].value.text() == "—"
    assert win.cards["last_charge"].value.text() == "—"
    win.close()


def test_stale_result_ignored(qapp, tmp_path):
    win = StatsWindow("Mouse", tmp_path / "nope.csv")
    wait_loaded(qapp, win)
    before = win._stats
    win._on_loaded(win._gen - 1, [], object())
    assert win._stats is before
    win.close()


def test_error_state_not_empty_message(qapp, tmp_path, monkeypatch):
    def boom(path):
        raise OSError("cannot read")
    monkeypatch.setattr(sw.log_stats, "read_samples", boom)
    win = StatsWindow("Mouse", tmp_path / "x.csv")
    wait_loaded(qapp, win)
    win.show()
    assert win._error and not win.has_data()
    assert win.empty_label.text() == sw.tr("stats_error")
    assert not win.empty_label.isHidden() and win.scroll.isHidden()
    assert not win.grab().isNull()
    win.close()


def test_empty_message_with_progress(qapp, tmp_path):
    csv = tmp_path / "few.csv"
    now = time.time()
    csv.write_text("\n".join([HEADER] + [_row(now - 300 + i * 60, 80) for i in range(4)]) + "\n")
    win = StatsWindow("Mouse", csv)
    wait_loaded(qapp, win)
    assert not win.has_data()
    assert "4" in win.empty_label.text() and str(sw.MIN_SAMPLES) in win.empty_label.text()
    assert win.header_label.text() != sw.tr("stats_loading")
    win.close()


def test_reopen_updates_path_and_title(qapp, tmp_path):
    mgr = StatsWindows()
    w1 = mgr.open("k", "Mouse", tmp_path / "a.csv")
    wait_loaded(qapp, w1)
    w2 = mgr.open("k", "Mouse Pro", tmp_path / "b.csv")
    assert w2 is w1
    assert w1.csv_path == tmp_path / "b.csv"
    assert w1.windowTitle().endswith("Mouse Pro")
    assert w1.name_label.text() == "Mouse Pro"
    mgr.close_all()


def test_reload_coalesces(qapp, tmp_path, monkeypatch):
    calls = []
    real = sw.log_stats.read_samples

    def counting(path):
        calls.append(path)
        time.sleep(0.15)
        return real(path)
    monkeypatch.setattr(sw.log_stats, "read_samples", counting)
    win = StatsWindow("Mouse", tmp_path / "nope.csv")
    assert not win.refresh_button.isEnabled()
    for _ in range(5):
        win.reload()
    assert win._pending
    end = time.time() + 10
    while (win._loading or win._pending or len(calls) < 2) and time.time() < end:
        qapp.processEvents()
        QTest.qWait(10)
    assert len(calls) == 2                      # initial + one coalesced follow-up
    assert win.refresh_button.isEnabled()
    win.close()


def test_integer_ticks_for_charges():
    for peak in (0, 1, 2, 3, 4, 5, 9, 17):
        ymax, ticks = sw.nice_int_axis(peak)
        assert ymax == int(ymax) and all(t == int(t) for t in ticks)
        assert ymax >= peak and ticks[0] == 0 and ticks[-1] == ymax


def test_date_formats_es_en():
    ts = dt.datetime(2026, 3, 7, 14, 5).timestamp()
    assert sw.format_when(ts, "es") == "07/03/26 14:05"
    assert sw.format_when(ts, "en") == "03/07/26 14:05"
    assert sw.format_axis_time(ts, 6 * 3600, "es") == "14:05"
    assert sw.format_axis_time(ts, 24 * 3600, "es") == "07/03 14:05"
    assert sw.format_axis_time(ts, 7 * 86400, "es") == "07/03"
    assert sw.format_axis_time(ts, 7 * 86400, "en") == "03/07"
    assert sw.format_axis_time(ts, 400 * 86400, "en") == "03/07/26"
    assert sw.format_when(None) == "—"


def test_level_color_differs_from_amber():
    for dark in (False, True):
        assert sw.level_line_color(dark) != sw.band_color("amber", dark)


def test_windows_reused_and_escape(qapp, tmp_path):
    mgr = StatsWindows()
    called = []
    w1 = mgr.open("k", "Mouse", tmp_path / "a.csv", lambda: called.append(1))
    w2 = mgr.open("k", "Mouse", tmp_path / "a.csv")
    assert w1 is w2
    assert w1.isVisible()
    w1.folder_button.click()
    assert called == [1]
    QTest.keyClick(w1, Qt.Key.Key_Escape)
    assert not w1.isVisible()
    w3 = mgr.open("k", "Mouse", tmp_path / "a.csv")
    assert w3 is w1 and w3.isVisible()
    mgr.close_all()
    assert not w1.isVisible()
