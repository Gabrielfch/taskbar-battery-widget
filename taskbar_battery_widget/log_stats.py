"""Pure (Qt-free) statistics over the per-device detailed battery CSV log."""
from __future__ import annotations

import csv
import datetime as _dt
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

GAP_S = 600            # samples further apart than this break a run
CHARGE_JUMP_PCT = 10   # rise >= this without charging=1 counts as an inferred charge
FLICKER_MAX = 2        # charging=0 samples tolerated inside one charge
MAX_ROWS = 600_000     # cap on rows read in total (most recent win)
MAX_POINTS = 2000      # cap on points returned by level_series
_MAX_BACKUPS = 9


@dataclass(frozen=True)
class Sample:
    t: float
    battery: int | None
    charging: bool
    connected: bool
    idle: bool


@dataclass(frozen=True)
class ChargeSession:
    start: float
    end: float
    from_pct: int | None
    to_pct: int | None
    observed: bool


@dataclass(frozen=True)
class DischargeSegment:
    start: float
    end: float
    from_pct: int
    to_pct: int


@dataclass(frozen=True)
class DayStat:
    day: _dt.date
    avg_level: float | None
    min_level: int | None
    max_level: int | None
    drop_pct: int
    hours_logged: float
    charges: int


@dataclass(frozen=True)
class Stats:
    samples: int
    first: float | None
    last: float | None
    hours_logged: float
    charges: tuple[ChargeSession, ...]
    discharges: tuple[DischargeSegment, ...]
    charge_count: int
    avg_discharge_rate: float | None
    est_full_runtime_h: float | None
    avg_between_charges_h: float | None
    avg_charge_duration_h: float | None
    avg_charge_gain_pct: float | None
    last_charge_end: float | None
    days: tuple[DayStat, ...]


# ---------------------------------------------------------------- reading

def _parse_row(row: list[str]) -> Sample | None:
    if len(row) < 7:
        return None
    try:
        t = _dt.datetime.fromisoformat(row[0].strip()).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    battery: int | None
    try:
        battery = int(row[1].strip())
    except ValueError:
        battery = None
    if battery is not None and not 0 <= battery <= 100:
        battery = None
    if row[6].strip() == "1":
        battery = None
    return Sample(
        t=t,
        battery=battery,
        charging=row[2].strip() == "1",
        connected=row[3].strip() == "1",
        idle=row[5].strip() == "1",
    )


def _read_file(path: Path) -> list[Sample]:
    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return []
    out: list[Sample] = []
    try:
        for row in csv.reader(text.splitlines()):
            if not row or row[0].strip() == "time_iso":
                continue
            s = _parse_row(row)
            if s is not None:
                out.append(s)
    except csv.Error:
        pass
    return out


def read_samples(csv_path: Path) -> list[Sample]:
    """Read the current CSV plus rotated backups; never raises on bad data."""
    csv_path = Path(csv_path)
    files = [csv_path]
    files += [Path(f"{csv_path}.{n}") for n in range(1, _MAX_BACKUPS + 1)]
    chunks: list[list[Sample]] = []
    total = 0
    for f in files:  # newest first so the row cap keeps recent data
        part = _read_file(f)
        chunks.append(part)
        total += len(part)
        if total >= MAX_ROWS:
            break
    merged = [s for part in chunks for s in part]
    merged.sort(key=lambda s: s.t)
    unique: list[Sample] = []
    last_t: float | None = None
    for s in merged:
        if s.t != last_t:
            unique.append(s)
            last_t = s.t
    if len(unique) > MAX_ROWS:
        unique = unique[-MAX_ROWS:]
    return unique


# ---------------------------------------------------------------- helpers

def _charge_runs(samples: Sequence[Sample], gap_s: float) -> list[tuple[int, int]]:
    """Index ranges (first, last) of consecutive charging=1 samples."""
    runs: list[tuple[int, int]] = []
    first = -1
    last = -1
    for i, s in enumerate(samples):
        if s.charging:
            if first >= 0 and s.t - samples[last].t <= gap_s and last == i - 1:
                last = i
            else:
                if first >= 0:
                    runs.append((first, last))
                first = last = i
        elif first >= 0:
            runs.append((first, last))
            first = last = -1
    if first >= 0:
        runs.append((first, last))
    return runs


def _merged_charge_runs(samples: Sequence[Sample], gap_s: float) -> list[tuple[int, int]]:
    """Charge runs with the pieces of one physical charge joined.

    Two consecutive runs are one charge when at most FLICKER_MAX samples with
    charging=0 separate them (none for a logging gap), those stay within
    gap_s of each other and the level never dropped below the first run's end.
    """
    merged: list[tuple[int, int]] = []
    for a, b in _charge_runs(samples, gap_s):
        if merged:
            pa, pb = merged[-1]
            between = range(pb + 1, a)
            if len(between) <= FLICKER_MAX and (
                not between or samples[a].t - samples[pb].t <= gap_s
            ):
                ref = next(
                    (samples[i].battery for i in range(pb, pa - 1, -1)
                     if samples[i].battery is not None), None)
                levels = [samples[i].battery for i in (*between, a)
                          if samples[i].battery is not None]
                if ref is None or all(v >= ref for v in levels):
                    merged[-1] = (pa, b)
                    continue
        merged.append((a, b))
    return merged


def _observed_charges(samples: Sequence[Sample], gap_s: float) -> list[ChargeSession]:
    out: list[ChargeSession] = []
    for a, b in _merged_charge_runs(samples, gap_s):
        from_pct = None
        for i in range(a - 1, -1, -1):
            if samples[a].t - samples[i].t > gap_s:
                break
            if samples[i].battery is not None:
                from_pct = samples[i].battery
                break
        if from_pct is None:
            for i in range(a, b + 1):
                if samples[i].battery is not None:
                    from_pct = samples[i].battery
                    break
        to_pct = None
        for i in range(b, a - 1, -1):
            if samples[i].battery is not None:
                to_pct = samples[i].battery
                break
        if to_pct is None:
            for i in range(b + 1, len(samples)):
                if samples[i].battery is not None:
                    to_pct = samples[i].battery
                    break
        out.append(ChargeSession(samples[a].t, samples[b].t, from_pct, to_pct, True))
    return out


def _inferred_charges(samples: Sequence[Sample]) -> list[ChargeSession]:
    out: list[ChargeSession] = []
    prev: Sample | None = None
    flag = False  # charging=1 seen since prev
    for s in samples:
        if s.charging:
            flag = True
        if s.battery is None:
            continue
        if (
            prev is not None
            and not flag
            and not s.charging
            and s.battery - prev.battery >= CHARGE_JUMP_PCT  # type: ignore[operator]
        ):
            out.append(ChargeSession(prev.t, s.t, prev.battery, s.battery, False))
        prev = s
        flag = s.charging
    return out


def _covered(times: list[float], start: float, end: float, gap_s: float) -> float:
    """Seconds in [start, end] covered by consecutive samples <= gap_s apart."""
    lo = max(bisect_right(times, start) - 1, 0)
    hi = min(bisect_left(times, end) + 1, len(times) - 1)
    total = 0.0
    for i in range(lo, hi):
        a, b = times[i], times[i + 1]
        if b - a > gap_s:
            continue
        ov = min(b, end) - max(a, start)
        if ov > 0:
            total += ov
    return total


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------- stats

def compute_stats(samples: Sequence[Sample], gap_s: float = GAP_S) -> Stats:
    """Compute discharge/charge statistics and per-day aggregates."""
    data = sorted(samples, key=lambda s: s.t)
    n = len(data)
    if n == 0:
        return Stats(0, None, None, 0.0, (), (), 0, None, None, None, None, None, None, ())

    # per-day accumulators: [sum, count, min, max, drop, seconds, charges]
    day_of = [_dt.datetime.fromtimestamp(s.t).date() for s in data]
    acc: dict[_dt.date, list] = {}

    def day(d: _dt.date) -> list:
        a = acc.get(d)
        if a is None:
            a = acc[d] = [0, 0, None, None, 0, 0.0, 0]
        return a

    discharges: list[DischargeSegment] = []
    seg_first: Sample | None = None
    seg_last: Sample | None = None
    dis_secs = 0.0  # time of every eligible discharge segment, even with 0 % drop

    def close() -> None:
        nonlocal dis_secs
        if seg_first is not None and seg_last is not None:
            dis_secs += seg_last.t - seg_first.t
            if seg_first.battery - seg_last.battery > 0:  # type: ignore[operator]
                discharges.append(DischargeSegment(
                    seg_first.t, seg_last.t, seg_first.battery, seg_last.battery))  # type: ignore[arg-type]

    for i, s in enumerate(data):
        a = day(day_of[i])
        if s.battery is not None:
            a[0] += s.battery
            a[1] += 1
            a[2] = s.battery if a[2] is None else min(a[2], s.battery)
            a[3] = s.battery if a[3] is None else max(a[3], s.battery)
        if s.connected and i + 1 < n:
            a[5] += min(data[i + 1].t - s.t, gap_s)

        eligible = s.connected and s.battery is not None and not s.charging
        if not eligible:
            close()
            seg_first = seg_last = None
            continue
        if seg_last is not None and (s.t - seg_last.t > gap_s or s.battery > seg_last.battery):  # type: ignore[operator]
            close()
            seg_first = seg_last = None
        if seg_first is None:
            seg_first = seg_last = s
        else:
            drop = seg_last.battery - s.battery  # type: ignore[operator]
            if drop > 0:
                a[4] += drop
            seg_last = s
    close()

    charges = _observed_charges(data, gap_s) + _inferred_charges(data)
    charges.sort(key=lambda c: (c.start, c.end))
    for c in charges:
        day(_dt.datetime.fromtimestamp(c.start).date())[6] += 1

    drop_total = sum(d.from_pct - d.to_pct for d in discharges)
    dis_hours = dis_secs / 3600.0
    rate: float | None = None
    if dis_hours >= 1.0 and drop_total >= 5:
        rate = drop_total / dis_hours
    est = 100.0 / rate if rate else None

    times = [s.t for s in data]
    gaps: list[float] = []
    for c1, c2 in zip(charges, charges[1:]):
        length = c2.start - c1.end
        if length <= 0:
            continue
        if _covered(times, c1.end, c2.start, gap_s) >= 0.8 * length:
            gaps.append(length / 3600.0)

    durations = [(c.end - c.start) / 3600.0 for c in charges if c.observed and c.start < c.end]
    gains = [
        float(c.to_pct - c.from_pct)
        for c in charges
        if c.from_pct is not None and c.to_pct is not None
    ]

    days = []
    for d in sorted(acc):
        a = acc[d]
        days.append(DayStat(
            day=d,
            avg_level=(a[0] / a[1]) if a[1] else None,
            min_level=a[2],
            max_level=a[3],
            drop_pct=a[4],
            hours_logged=a[5] / 3600.0,
            charges=a[6],
        ))

    return Stats(
        samples=n,
        first=data[0].t,
        last=data[-1].t,
        hours_logged=sum(x.hours_logged for x in days),
        charges=tuple(charges),
        discharges=tuple(discharges),
        charge_count=len(charges),
        avg_discharge_rate=rate,
        est_full_runtime_h=est,
        avg_between_charges_h=_mean(gaps),
        avg_charge_duration_h=_mean(durations),
        avg_charge_gain_pct=_mean(gains),
        last_charge_end=max((c.end for c in charges), default=None),
        days=tuple(days),
    )


# ---------------------------------------------------------------- series

def level_series(
    samples: Sequence[Sample], since: float | None = None, gap_s: float = GAP_S
) -> list[tuple[float, int | None]]:
    """Points (t, battery) for a line chart; None entries cut the line."""
    runs: list[list[tuple[float, int]]] = []
    cur: list[tuple[float, int]] = []
    prev_t: float | None = None
    for s in samples:
        if since is not None and s.t < since:
            continue
        if prev_t is not None and s.t - prev_t > gap_s and cur:
            runs.append(cur)
            cur = []
        prev_t = s.t
        if s.connected and s.battery is not None:
            cur.append((s.t, s.battery))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    total = sum(len(r) for r in runs)
    if total > MAX_POINTS:
        t0 = runs[0][0][0]
        t1 = runs[-1][-1][0]
        width = (t1 - t0) / (MAX_POINTS - 1) or 1.0
        reduced: list[list[tuple[float, int]]] = []
        for r in runs:
            buckets: dict[int, list[tuple[float, int]]] = {}
            for p in r:
                buckets.setdefault(int((p[0] - t0) // width), []).append(p)
            reduced.append([
                (sum(q[0] for q in b) / len(b), round(sum(q[1] for q in b) / len(b)))
                for _, b in sorted(buckets.items())
            ])
        runs = reduced

    out: list[tuple[float, int | None]] = []
    for r in runs:
        if out:
            out.append((r[0][0] - 1e-3 if r[0][0] > out[-1][0] else out[-1][0], None))
        out.extend(r)
    return out


def charging_spans(
    samples: Sequence[Sample], since: float | None = None, gap_s: float = GAP_S
) -> list[tuple[float, float]]:
    """Intervals with charging=1, for shading under a chart."""
    data = [s for s in samples if since is None or s.t >= since]
    return [(data[a].t, data[b].t) for a, b in _charge_runs(data, gap_s)]
