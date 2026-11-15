"""Tests for taskbar_battery_widget.log_stats (pure CSV statistics)."""
from __future__ import annotations

import datetime as dt
import time

import pytest

from taskbar_battery_widget import log_stats as ls
from taskbar_battery_widget.log_stats import Sample

HEADER = "time_iso,battery,charging,connected,link,idle,stale"
BASE = dt.datetime(2026, 11, 10, 12, 0, 0).timestamp()  # local midday


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def line(t, battery="", charging=0, connected=1, idle=0, stale=0):
    return f"{iso(t)},{battery},{charging},{connected},wired,{idle},{stale}"


def write(path, lines, header=True):
    body = ([HEADER] if header else []) + list(lines)
    path.write_bytes(("\r\n".join(body) + "\r\n").encode("utf-8"))


def S(t, b, charging=False, connected=True, idle=False):
    return Sample(t=t, battery=b, charging=charging, connected=connected, idle=idle)


def run(start, pcts, step=60.0, **kw):
    return [S(start + i * step, p, **kw) for i, p in enumerate(pcts)]


# ---------------------------------------------------------------- reading

def test_read_rotated_backups_and_repeated_headers(tmp_path):
    p = tmp_path / "dev.csv"
    write(tmp_path / "dev.csv.2", [line(BASE, 90)])
    write(tmp_path / "dev.csv.1", [line(BASE + 60, 89)])
    write(p, [line(BASE + 120, 88), HEADER, line(BASE + 180, 87)])
    out = ls.read_samples(p)
    assert [s.battery for s in out] == [90, 89, 88, 87]
    assert [s.t for s in out] == sorted(s.t for s in out)


def test_read_garbage_stale_and_duplicates(tmp_path):
    p = tmp_path / "dev.csv"
    write(p, [
        "garbage",
        "",
        "notadate,50,0,1,wired,0,0",
        line(BASE, 50),
        line(BASE, 51),  # duplicate t
        line(BASE + 60, 150),  # out of range
        line(BASE + 120, 40, stale=1),
        line(BASE + 180, ""),
        line(BASE + 240, 30, charging=1, connected=0, idle=1),
        "short,1,2",
    ])
    out = ls.read_samples(p)
    assert len(out) == 5
    assert out[0].battery == 50
    assert out[1].battery is None
    assert out[2].battery is None
    assert out[3].battery is None
    assert out[4].battery == 30 and out[4].charging and not out[4].connected and out[4].idle


def test_read_missing_or_unreadable(tmp_path):
    assert ls.read_samples(tmp_path / "nope.csv") == []
    d = tmp_path / "dir.csv"
    d.mkdir()
    assert ls.read_samples(d) == []


def test_max_rows_keeps_recent(tmp_path, monkeypatch):
    p = tmp_path / "dev.csv"
    write(tmp_path / "dev.csv.1", [line(BASE + i * 60, 80) for i in range(5)])
    write(p, [line(BASE + (10 + i) * 60, 70) for i in range(5)])
    monkeypatch.setattr(ls, "MAX_ROWS", 6)
    out = ls.read_samples(p)
    assert len(out) == 6
    assert out[-1].t == BASE + 14 * 60
    assert sum(1 for s in out if s.battery == 70) == 5


# ---------------------------------------------------------------- stats

def test_empty():
    st = ls.compute_stats([])
    assert st.samples == 0 and st.charge_count == 0 and st.days == ()
    assert st.avg_discharge_rate is None and st.first is None
    assert ls.level_series([]) == []
    assert ls.charging_spans([]) == []


def test_simple_discharge():
    # 100 -> 50 over 10 h, one sample every 5 min
    n = 10 * 12
    samples = [S(BASE + i * 300, 100 - round(50 * i / n)) for i in range(n + 1)]
    st = ls.compute_stats(samples)
    assert len(st.discharges) == 1
    assert st.avg_discharge_rate == pytest.approx(5.0)
    assert st.est_full_runtime_h == pytest.approx(20.0)
    assert st.charge_count == 0
    assert st.hours_logged == pytest.approx(10.0)


def test_discharge_needs_enough_data():
    st = ls.compute_stats(run(BASE, [100, 99, 98]))
    assert st.avg_discharge_rate is None and st.est_full_runtime_h is None


def test_gap_breaks_segment():
    a = run(BASE, [100, 99, 98])
    b = run(BASE + 7200, [60, 59, 58])
    st = ls.compute_stats(a + b)
    assert len(st.discharges) == 2
    assert [(d.from_pct, d.to_pct) for d in st.discharges] == [(100, 98), (60, 58)]
    # 40 % drop across the gap is a discharge, not a charge
    assert st.charge_count == 0


def test_observed_charge():
    s = run(BASE, [50, 50]) + run(BASE + 120, [51, 60, 70, 80], charging=True) + run(BASE + 360, [79])
    st = ls.compute_stats(s)
    assert st.charge_count == 1
    c = st.charges[0]
    assert c.observed and c.from_pct == 50 and c.to_pct == 80
    assert c.end - c.start == 180
    assert st.avg_charge_duration_h == pytest.approx(180 / 3600)
    assert st.avg_charge_gain_pct == 30
    assert st.last_charge_end == c.end
    assert ls.charging_spans(s) == [(c.start, c.end)]
    assert ls.charging_spans(s, since=c.end + 1) == []


def test_inferred_charge_across_gap():
    s = run(BASE, [20, 19]) + run(BASE + 3 * 3600, [80, 80])
    st = ls.compute_stats(s)
    assert st.charge_count == 1
    c = st.charges[0]
    assert not c.observed and (c.from_pct, c.to_pct) == (19, 80)
    assert st.avg_charge_duration_h is None
    assert st.avg_charge_gain_pct == 61


def test_small_jump_ignored():
    st = ls.compute_stats(run(BASE, [50, 55, 54]))
    assert st.charge_count == 0
    assert st.discharges and st.discharges[0].from_pct == 55


def test_between_charges_with_and_without_coverage():
    # charge, 2 h continuous data, charge
    def charge(t0):
        return run(t0, [30, 60], charging=True)

    cont = run(BASE + 120, [60] * 120)  # one sample/min for 2 h, no drops
    s = charge(BASE) + cont + charge(BASE + 120 + 120 * 60)
    st = ls.compute_stats(s)
    assert st.charge_count == 2
    assert st.avg_between_charges_h == pytest.approx(2.0, abs=0.05)

    # same layout but the interval is mostly a gap (data only at the ends)
    s2 = charge(BASE) + run(BASE + 120, [60, 60]) + charge(BASE + 5 * 3600)
    st2 = ls.compute_stats(s2)
    assert st2.charge_count == 2
    assert st2.avg_between_charges_h is None


def test_days_grouping():
    d1 = dt.datetime(2026, 11, 10, 12, 0).timestamp()
    d2 = dt.datetime(2026, 11, 11, 12, 0).timestamp()
    s = run(d1, [90, 85, 80]) + run(d2, [70, 60], charging=True) + run(d2 + 600, [75, 74])
    st = ls.compute_stats(s)
    assert [d.day for d in st.days] == [
        dt.datetime.fromtimestamp(d1).date(),
        dt.datetime.fromtimestamp(d2).date(),
    ]
    a, b = st.days
    assert (a.min_level, a.max_level) == (80, 90)
    assert a.avg_level == pytest.approx(85)
    assert a.drop_pct == 10
    assert a.hours_logged == pytest.approx((60 + 60 + ls.GAP_S) / 3600)  # last sample runs up to the gap cap
    assert a.charges == 0
    assert b.charges == 1
    assert b.drop_pct == 1
    assert st.hours_logged == pytest.approx(sum(d.hours_logged for d in st.days))


# ---------------------------------------------------------------- series

def test_level_series_cuts_on_gaps_and_disconnect():
    s = run(BASE, [90, 89]) + run(BASE + 7200, [80, 79])
    s.append(S(BASE + 7200 + 120, None))
    s.append(S(BASE + 7200 + 180, 70))
    out = ls.level_series(s)
    values = [v for _, v in out]
    assert values == [90, 89, None, 80, 79, None, 70]
    assert [t for t, _ in out] == sorted(t for t, _ in out)
    assert ls.level_series(s, since=BASE + 7200)[0] == (BASE + 7200, 80)


def test_level_series_reduces_large():
    n = 10_000
    s = [S(BASE + i * 60, 100 - (i * 100) // n) for i in range(n)]
    out = ls.level_series(s)
    assert 100 < len(out) <= 2100
    assert all(v is not None for _, v in out)
    assert [t for t, _ in out] == sorted(t for t, _ in out)


def test_performance_130k():
    n = 130_000
    s = [S(BASE + i * 60, 100 - (i // 700) % 100) for i in range(n)]
    t0 = time.perf_counter()
    st = ls.compute_stats(s)
    assert time.perf_counter() - t0 < 3.0
    assert st.samples == n


# ---------------------------------------------------------------- review fixes

def test_charge_split_by_gap_is_one():
    s = (run(BASE, [30, 39], charging=True)
         + run(BASE + 7200, [60, 69], charging=True))
    st = ls.compute_stats(s)
    assert st.charge_count == 1
    c = st.charges[0]
    assert c.start == BASE and c.end == BASE + 7200 + 60
    assert c.to_pct == 69


def test_charge_flicker_is_one():
    s = (run(BASE, [30, 35], charging=True)
         + run(BASE + 120, [36, 36])
         + run(BASE + 240, [37, 40], charging=True))
    st = ls.compute_stats(s)
    assert st.charge_count == 1
    assert (st.charges[0].from_pct, st.charges[0].to_pct) == (30, 40)


def test_real_discharge_between_charges_splits():
    s = (run(BASE, [30, 60], charging=True)
         + run(BASE + 120, [59, 50, 40], step=60)
         + run(BASE + 300, [41, 50], charging=True))
    assert ls.compute_stats(s).charge_count == 2


def test_many_short_sessions_rate_close_to_real():
    # real drain 2 %/h quantised to 1 %, 45 sessions of 15 min, 3 h apart
    samples = []
    for k in range(45):
        t0 = BASE + k * 3 * 3600
        for j in range(16):
            t = t0 + j * 60
            samples.append(S(t, 100 - int(2 * (k * 0.25 + j / 60))))
    st = ls.compute_stats(samples)
    assert st.avg_discharge_rate == pytest.approx(2.0, rel=0.15)


def test_charge_from_pct_ignores_old_battery():
    s = [S(BASE, 20)] + [S(BASE + 7200, None, charging=True), S(BASE + 7260, 50, charging=True)]
    c = ls.compute_stats(s).charges[0]
    assert c.from_pct == 50


def test_inferred_jump_after_reconnect_still_counts():
    # documents current behaviour: a >=10 % rise after reconnect is an inferred charge
    s = run(BASE, [20, 19]) + [S(BASE + 120, None, connected=False)] + run(BASE + 180, [60, 60])
    assert ls.compute_stats(s).charge_count == 1


def test_bom_header(tmp_path):
    p = tmp_path / "dev.csv"
    body = HEADER + "\r\n" + line(BASE, 77) + "\r\n"
    p.write_bytes(b"\xef\xbb\xbf" + body.encode())
    out = ls.read_samples(p)
    assert [x.battery for x in out] == [77]


def test_duplicates_between_csv_and_backup(tmp_path):
    p = tmp_path / "dev.csv"
    write(tmp_path / "dev.csv.1", [line(BASE, 90), line(BASE + 60, 89)])
    write(p, [line(BASE + 60, 89), line(BASE + 120, 88)])
    assert [x.battery for x in ls.read_samples(p)] == [90, 89, 88]
