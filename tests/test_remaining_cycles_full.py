"""Time-left estimate, equivalent cycles and the fully-charged notice."""
from __future__ import annotations

import pytest

from taskbar_battery_widget import log_stats as ls
from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.config import Config, DEFAULTS
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.log_stats import Rates, Sample
from taskbar_battery_widget.models import Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.remaining import (RemainingEstimator, compute_rates, format_remaining,
                                              remaining_text)

T0 = 1_700_000_000.0
STEP = 300.0


def discharge(start, hours, from_pct, to_pct):
    n = int(hours * 3600 / STEP)
    return [Sample(start + i * STEP, round(from_pct + (to_pct - from_pct) * i / n), False, True, False)
            for i in range(n + 1)]


def charge(start, hours, from_pct, to_pct):
    n = int(hours * 3600 / STEP)
    return [Sample(start + i * STEP, round(from_pct + (to_pct - from_pct) * i / n), True, True, False)
            for i in range(n + 1)]


def setup_function(_):
    set_language("en")


# ------------------------------------------------------------ rates
def test_estimate_rates_discharge_and_charge():
    s = discharge(T0, 10, 100, 50) + charge(T0 + 10 * 3600 + STEP, 2, 50, 100)
    r = ls.estimate_rates(s, T0 + 12.1 * 3600)
    assert r.discharge_pct_per_h == pytest.approx(5.0, rel=0.05)
    assert r.charge_pct_per_h == pytest.approx(25.0, rel=0.1)


def test_estimate_rates_uses_only_recent_window():
    old = discharge(T0, 10, 100, 50)                      # 5 %/h, 20 days ago
    new = discharge(T0 + 20 * 86400, 10, 100, 80)         # 2 %/h
    r = ls.estimate_rates(old + new, T0 + 20 * 86400 + 10.5 * 3600)
    assert r.discharge_pct_per_h == pytest.approx(2.0, rel=0.1)
    r_all = ls.estimate_rates(old + new, T0 + 20 * 86400 + 10.5 * 3600, window_days=30)
    assert r_all.discharge_pct_per_h > 3.0


def test_estimate_rates_minimums_and_empty():
    assert ls.estimate_rates([], T0) == Rates(None, None)
    short = discharge(T0, 5, 100, 60)                     # under 6 h of discharge
    assert ls.estimate_rates(short, T0 + 5.5 * 3600).discharge_pct_per_h is None
    tiny = discharge(T0, 24, 100, 94)                     # under 10 % dropped
    assert ls.estimate_rates(tiny, T0 + 25 * 3600).discharge_pct_per_h is None
    ok = discharge(T0, 6, 100, 70)
    assert ls.estimate_rates(ok, T0 + 6.5 * 3600).discharge_pct_per_h == pytest.approx(5.0, rel=0.05)
    brief = charge(T0, 5 / 60, 40, 45)                    # charge under 10 min
    assert ls.estimate_rates(brief, T0 + 600).charge_pct_per_h is None


def test_slow_drain_over_a_week_gives_no_estimate():
    s = []
    for day in range(7):                                  # 100 -> 94 % spread over 7 days
        s += discharge(T0 + day * 86400, 3, 100 - day, 99 - day)
    assert ls.estimate_rates(s, T0 + 6 * 86400 + 3.5 * 3600).discharge_pct_per_h is None


def test_old_log_gives_no_estimate():
    s = discharge(T0, 10, 100, 50)
    end = T0 + 10 * 3600
    assert ls.estimate_rates(s, end + 3600).discharge_pct_per_h is not None
    assert ls.estimate_rates(s, end + 2.5 * 3600) == Rates(None, None)


def test_charge_rate_ignores_the_tail_at_full():
    c = charge(T0, 2, 50, 100)
    tail_start = c[-1].t
    tail = [Sample(tail_start + (i + 1) * STEP, 100, True, True, False) for i in range(96)]   # 8 h
    r = ls.estimate_rates(c + tail, tail_start + 8 * 3600 + 600)
    assert r.charge_pct_per_h == pytest.approx(25.0, rel=0.1)


def test_read_samples_since_skips_old_backups(tmp_path):
    import datetime as dt

    def write(path, t0):
        rows = ["time_iso,battery,charging,connected,x,idle,unknown"]
        for i in range(3):
            iso = dt.datetime.fromtimestamp(t0 + i * 60).isoformat()
            rows.append(f"{iso},50,0,1,x,0,0")
        path.write_text("\n".join(rows), encoding="utf-8")
    base = tmp_path / "log.csv"
    write(base, T0 + 10 * 86400)
    write(tmp_path / "log.csv.1", T0 + 5 * 86400)
    write(tmp_path / "log.csv.2", T0)
    assert len(ls.read_samples(base)) == 9
    assert len(ls.read_samples(base, since=T0 + 4 * 86400)) == 6      # .1 read, .2 skipped
    assert len(ls.read_samples(base, since=T0 - 1)) == 9


def test_remaining_hours():
    r = Rates(5.0, 25.0)
    assert ls.remaining_hours(50, False, r) == pytest.approx(10.0)
    assert ls.remaining_hours(50, True, r) == pytest.approx(2.0)
    assert ls.remaining_hours(100, True, r) is None
    assert ls.remaining_hours(None, False, r) is None
    assert ls.remaining_hours(50, False, Rates(None, 25.0)) is None
    assert ls.remaining_hours(50, True, Rates(5.0, None)) is None
    assert ls.remaining_hours(50, False, None) is None
    assert ls.remaining_hours(50, False, Rates(0.0, None)) is None
    assert ls.remaining_hours(50, False, Rates(0.05, None)) is None      # > 30 days
    assert ls.remaining_hours(50, False, Rates(0.1, None)) == pytest.approx(500.0)


@pytest.mark.parametrize("hours,text", [
    (0.01, "≈ 1 min"), (0.66, "≈ 40 min"), (1.0, "≈ 1 h"), (5.5, "≈ 5 h 30 min"),
    (5.7, "≈ 5 h 30 min"), (6.1, "≈ 6 h"), (9.9, "≈ 10 h"), (12.4, "≈ 12 h"),
    (47.0, "≈ 47 h"), (72.0, "≈ 3 d"), (100.0, "≈ 4 d")])
def test_format_remaining(hours, text):
    assert format_remaining(hours) == text


def test_remaining_label_languages():
    from taskbar_battery_widget.remaining import remaining_label
    assert remaining_label(6, False, "en") == "≈ 6 h left"
    assert remaining_label(6, False, "es") == "≈ 6 h restantes"
    assert remaining_label(2 / 3, True, "en") == "full in ≈ 40 min"
    assert remaining_label(2 / 3, True, "es") == "lleno en ≈ 40 min"


def dev(battery=50, charging=False, **kw):
    d = dict(id="d1", name="Mouse", kind="mouse", battery=battery, connected=True, charging=charging)
    d.update(kw)
    return Device(**d)


def test_remaining_text_conditions():
    rates = {visibility_key("d1"): Rates(5.0, 25.0)}
    assert remaining_text(dev(50), rates, "en") == "≈ 10 h left"
    assert remaining_text(dev(50, True), rates, "en") == "full in ≈ 2 h"
    assert remaining_text(dev(50), None, "en") is None
    assert remaining_text(dev(50), {}, "en") is None
    assert remaining_text(dev(50, id="other"), rates, "en") is None
    assert remaining_text(dev(50, idle=True), rates, "en") is None
    assert remaining_text(dev(50, stale=True), rates, "en") is None
    assert remaining_text(dev(50, connected=False), rates, "en") is None
    assert remaining_text(dev(100, True), rates, "en") is None


def test_panel_row_shows_remaining():
    rates = {visibility_key("d1"): Rates(5.0, 25.0)}
    m = build_panel_model([dev(50)], (), True, 20, True, lang="en", rates=rates)
    assert m.connected[0].status_text == "OK · ≈ 10 h left"
    m = build_panel_model([dev(50, True)], (), True, 20, True, lang="es", rates=rates)
    assert m.connected[0].status_text == "Cargando · lleno en ≈ 2 h"
    m = build_panel_model([dev(50)], (), True, 20, True, lang="en")
    assert m.connected[0].status_text == "OK"
    m2 = build_panel_model([dev(50)], (), True, 20, True, lang="en", rates=rates)
    assert m2.signature != m.signature


def test_popup_content_includes_remaining():
    from taskbar_battery_widget.popup import build_popup_content
    c = build_popup_content(dev(50), 20, remaining="≈ 10 h left")
    assert "≈ 10 h left" in c.lines
    assert "≈ 10 h left" not in build_popup_content(dev(50), 20).lines


def test_compute_rates_skips_unreadable_logs(tmp_path):
    good = discharge(T0, 10, 100, 50)

    def reader(p, since):
        if p.name == "bad":
            raise OSError("boom")
        return good
    out = compute_rates({"a": tmp_path / "a", "b": tmp_path / "bad"}, T0 + 10.5 * 3600, reader)
    assert set(out) == {"a"}
    assert out["a"].discharge_pct_per_h == pytest.approx(5.0, rel=0.05)


def test_compute_rates_cache_skips_unchanged_files(tmp_path):
    good = discharge(T0, 10, 100, 50)
    f = tmp_path / "a.csv"
    f.write_text("x", encoding="utf-8")
    calls = []

    def reader(p, since):
        calls.append(p)
        return good
    cache: dict = {}
    now = T0 + 10.5 * 3600
    compute_rates({"a": f}, now, reader, cache)
    out = compute_rates({"a": f}, now + 600, reader, cache)
    assert len(calls) == 1 and out["a"].discharge_pct_per_h == pytest.approx(5.0, rel=0.05)
    out = compute_rates({"a": f}, T0 + 10 * 3600 + 3 * 3600, reader, cache)   # log now too old
    assert len(calls) == 1 and out["a"] == Rates(None, None)
    f.write_text("xx", encoding="utf-8")                  # size changed
    compute_rates({"a": f}, now, reader, cache)
    assert len(calls) == 2
    compute_rates({}, now, reader, cache)
    assert cache == {}


def test_compute_rates_logs_no_paths(tmp_path, caplog):
    import logging
    from taskbar_battery_widget import remaining as rm
    rm._warned.clear()

    def reader(p, since):
        raise OSError(f"cannot open {p}")
    with caplog.at_level(logging.DEBUG, logger="taskbar_battery_widget.remaining"):
        compute_rates({"a": tmp_path / "secret_user_dir"}, T0, reader)
        compute_rates({"a": tmp_path / "secret_user_dir"}, T0, reader)
    recs = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(recs) == 1
    text = recs[0].getMessage() + str(recs[0].exc_info)
    assert "OSError" in text and "secret_user_dir" not in text


def test_estimator_stop_prevents_relaunch_and_emit(qapp, tmp_path):
    import threading
    import time as _t
    from PySide6.QtWidgets import QApplication
    est = RemainingEstimator(lambda: {"k": tmp_path / "m.csv"}, clock=lambda: T0)
    hits = []
    est.updated.connect(lambda: hits.append(1))
    est.refresh()                                         # worker in flight
    est._pending = True
    est.stop()
    end = _t.monotonic() + 10                             # generous cap; the worker ends in milliseconds
    while _t.monotonic() < end and (any(t.name == "tbw-remaining" for t in threading.enumerate())
                                    or est._running):
        QApplication.processEvents()                      # delivers the worker's queued `done` signal
        _t.sleep(0.005)
    assert not est._running and hits == [] and est._pending is False
    n = threading.active_count()
    est.refresh()                                         # no new thread after stop
    assert threading.active_count() <= n
    est._on_done(est._gen, {"k": Rates(1.0, None)})
    assert hits == [] and est.rates() == {}


def test_estimator_computes_in_background(qapp, tmp_path):
    import time as _t
    from PySide6.QtWidgets import QApplication
    est = RemainingEstimator(lambda: {})
    assert est.rates() == {}
    est.refresh()                                         # no targets: nothing to run, no crash
    calls = []
    est2 = RemainingEstimator(lambda: {"k": tmp_path / "missing.csv"}, clock=lambda: T0)
    est2.updated.connect(lambda: calls.append(1))
    est2.refresh_if_new()
    end = _t.time() + 5
    while not calls and _t.time() < end:
        QApplication.processEvents()
        _t.sleep(0.01)
    assert calls and est2.rates()["k"] == Rates(None, None)
    n = len(calls)
    est2.refresh_if_new()                                 # already measured: no new work
    QApplication.processEvents()
    assert len(calls) == n


# ------------------------------------------------------------ equivalent cycles
def test_equivalent_cycles():
    s = []
    t = T0
    for _ in range(3):                                    # three charges 30 -> 100
        s += discharge(t, 4, 60, 30)
        t += 4 * 3600 + STEP
        s += charge(t, 3, 30, 100)
        t += 3 * 3600 + STEP
    st = ls.compute_stats(s)
    assert st.equivalent_cycles == pytest.approx(2.1, abs=0.05)
    assert ls.compute_stats([]).equivalent_cycles == 0.0


def test_stats_equivalent_cycles_default_keeps_old_constructions():
    st = ls.Stats(0, None, None, 0.0, (), (), 0, None, None, None, None, None, None, ())
    assert st.equivalent_cycles == 0.0


def test_stats_window_cycles_card(qapp, tmp_path):
    from taskbar_battery_widget.stats_window import CARD_KEYS, StatsWindow
    assert "cycles" in CARD_KEYS
    w = StatsWindow("Mouse", tmp_path / "x.csv")
    try:
        assert w.cards["cycles"].toolTip().startswith("Sum of everything charged")
        set_language("es")
        assert "2,1 ciclos" in __import__("taskbar_battery_widget.i18n", fromlist=["tr"]).tr(
            "stats_card_cycles_tip")
    finally:
        w.shutdown()


# ------------------------------------------------------------ config
def test_full_charge_alert_config(tmp_path):
    assert DEFAULTS["full_charge_alert"] is True
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("full_charge_alert") is True
    cfg.set("full_charge_alert", False)
    assert Config(tmp_path / "c.json").get("full_charge_alert") is False
    for bad in (1, "yes", None):
        with pytest.raises(ValueError):
            cfg.set("full_charge_alert", bad)
    (tmp_path / "d.json").write_text('{"full_charge_alert": "no"}', encoding="utf-8")
    assert Config(tmp_path / "d.json").get("full_charge_alert") is True


# ------------------------------------------------------------ full charge notice
def mk(tmp_path, **settings):
    cfg = Config(tmp_path / "c.json")
    for k, v in settings.items():
        cfg.set(k, v)
    return AlertManager(cfg)


def fulls(am, d, now=0.0):
    return [b for t, b in am.check([d], now) if t == "Fully charged"]


def test_full_notice_once_per_session(tmp_path):
    am = mk(tmp_path)
    assert fulls(am, dev(80, True)) == []
    assert fulls(am, dev(99, True)) == []
    assert fulls(am, dev(100, True)) == ["Mouse is charged (100 %)"]
    assert fulls(am, dev(100, True)) == []
    assert fulls(am, dev(100, False)) == []


def test_full_notice_spanish(tmp_path):
    set_language("es")
    am = mk(tmp_path)
    am.check([dev(90, True)], 0)
    out = am.check([dev(100, True)], 1)
    assert ("Carga completa", "Mouse cargado al 100 %") in out


def test_full_notice_when_charging_stops_near_full(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert fulls(am, dev(99, True)) == []
    assert fulls(am, dev(99, False)) == ["Mouse is charged (99 %)"]
    assert fulls(am, dev(99, False)) == []


def test_no_notice_when_unplugged_at_95_to_98(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert fulls(am, dev(97, True)) == []
    assert fulls(am, dev(97, False)) == []


def test_notice_when_plugged_at_96_with_app_open(tmp_path):
    am = mk(tmp_path)
    assert fulls(am, dev(80, False)) == []
    assert fulls(am, dev(96, True)) == []
    assert len(fulls(am, dev(100, True))) == 1


def test_no_second_notice_after_reconnecting_at_99_charging(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert len(fulls(am, dev(100, True))) == 1
    assert fulls(am, dev(99, connected=False)) == []
    assert am.check([], 10.0) == []                       # vanished from the list
    assert fulls(am, dev(99, True), 20.0) == []
    assert fulls(am, dev(100, True), 30.0) == []


def test_full_state_expires_after_ttl(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert len(fulls(am, dev(100, True))) == 1
    assert am.check([], 13 * 3600.0) == []
    assert fulls(am, dev(99, True), 13 * 3600.0 + 1) == []
    assert len(fulls(am, dev(100, True), 13 * 3600.0 + 2)) == 1


def test_no_notice_when_charging_stops_low(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(50, True))
    assert fulls(am, dev(60, False)) == []


def test_no_notice_at_startup_already_full(tmp_path):
    am = mk(tmp_path)
    assert fulls(am, dev(100, True)) == []
    assert fulls(am, dev(100, True)) == []
    assert fulls(am, dev(100, False)) == []


def test_rearm_after_dropping_below_95(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert len(fulls(am, dev(100, True))) == 1
    assert fulls(am, dev(98, False)) == []
    assert fulls(am, dev(94, False)) == []                # rearmed
    assert fulls(am, dev(95, True)) == []
    assert len(fulls(am, dev(100, True))) == 1


def test_rearm_after_disconnect(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert len(fulls(am, dev(100, True))) == 1
    assert fulls(am, dev(100, connected=False)) == []
    assert fulls(am, dev(70, True)) == []                 # new session
    assert len(fulls(am, dev(100, True))) == 1


def test_no_notice_after_reconnecting_already_full(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert len(fulls(am, dev(100, True))) == 1
    fulls(am, dev(100, connected=False))
    assert fulls(am, dev(100, True)) == []


def test_full_notice_respects_switches(tmp_path):
    for opts in ({"full_charge_alert": False}, {"notifications_enabled": False}):
        am = mk(tmp_path, **opts)
        fulls(am, dev(90, True))
        assert fulls(am, dev(100, True)) == []
        # state advanced silently: turning the switch back on does not replay it
        am._config.set(next(iter(opts)), True)
        assert fulls(am, dev(100, True)) == []


def test_full_notice_ignores_stale_and_idle(tmp_path):
    am = mk(tmp_path)
    fulls(am, dev(90, True))
    assert fulls(am, dev(100, True, stale=True)) == []
    assert fulls(am, dev(100, True, idle=True)) == []
    assert len(fulls(am, dev(100, True))) == 1


def test_full_notice_independent_per_device(tmp_path):
    am = mk(tmp_path)
    a, b = dev(90, True, id="a", name="A"), dev(90, True, id="b", name="B")
    am.check([a, b], 0)
    out = am.check([dev(100, True, id="a", name="A"), dev(95, True, id="b", name="B")], 1)
    assert [x for t, x in out if t == "Fully charged"] == ["A is charged (100 %)"]


# ------------------------------------------------------------ menu
def test_alerts_menu_has_full_charge_toggle(qapp, tmp_path):
    from taskbar_battery_widget.tray import TrayController
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    alerts = t._settings.find("alerts")
    act = t._settings.find("full_charge_alert")
    assert act in alerts.children and act.label() == "Notify when fully charged"
    assert act.get() is True
    act.set(False)
    assert cfg.get("full_charge_alert") is False
    cfg.set("full_charge_alert", True)
    assert act.get() is True


def test_format_cycles_locale_and_no_charges():
    from taskbar_battery_widget.stats_window import DASH, format_cycles
    assert format_cycles(2.1, 3, "en") == "2.1"
    assert format_cycles(2.1, 3, "es") == "2,1"
    assert format_cycles(0.0, 0, "es") == DASH
