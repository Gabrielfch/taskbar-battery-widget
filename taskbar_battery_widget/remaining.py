"""Estimated time left (empty / full) from the recent speed measured in each device's detailed log.

Only devices being recorded have a log, so only they get an estimate. The CSVs are read in a
background thread every REFRESH_MS and the result is handed to the UI thread with a Qt signal.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from . import i18n, log_stats
from .log_stats import Rates

log = logging.getLogger(__name__)

REFRESH_MS = 10 * 60 * 1000


def format_remaining(hours: float, lang: str | None = None) -> str:
    """'≈ 40 min' (< 1 h), '≈ 5 h 30 min' / '≈ 6 h' (< 48 h), '≈ 3 d' (>= 48 h); units localized."""
    lg = i18n.normalize_language(lang or i18n.get_language())

    def u(key: str, n: int) -> str:
        return i18n.tr_in(lg, key, n=n)

    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"≈ {u('dur_min', minutes)}"
    if hours < 10:
        halves = round(hours * 2)
        h, half = divmod(halves, 2)
        return f"≈ {u('dur_h', h)} {u('dur_min', 30)}" if half else f"≈ {u('dur_h', h)}"
    if hours < 48:
        return f"≈ {u('dur_h', round(hours))}"
    return f"≈ {u('dur_d', round(hours / 24))}"


def remaining_label(hours: float, charging: bool, lang: str | None = None) -> str:
    """'≈ 6 h left' / '≈ 6 h restantes' (discharging); 'full in ≈ 40 min' (charging)."""
    lg = lang or i18n.get_language()
    t = format_remaining(hours, lg)
    return i18n.tr_in(lg, "remaining_full" if charging else "remaining_left", t=t)


def remaining_text(device, rates: dict | None, lang: str | None = None) -> str | None:
    """Label for a device row/tooltip, or None when there is nothing trustworthy to show."""
    if not rates or not getattr(device, "connected", False) or getattr(device, "stale", False):
        return None
    if getattr(device, "idle", False) or device.battery is None:
        return None
    from .models import visibility_key
    r = rates.get(visibility_key(device.id))
    if r is None:
        return None
    charging = bool(device.charging)
    h = log_stats.remaining_hours(device.battery, charging, r)
    return None if h is None else remaining_label(h, charging, lang)


_warned: set[str] = set()


def _warn_once(what: str, exc: BaseException) -> None:
    """One warning per kind of failure, with the exception type only (paths can hold the user name)."""
    tag = f"{what}:{type(exc).__name__}"
    if tag not in _warned:
        _warned.add(tag)
        log.warning("%s failed (%s)", what, type(exc).__name__)


def _signature(path: Path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read(reader, path: Path, since: float):
    return reader(path, since)


def compute_rates(targets: dict[str, Path], now: float, reader=log_stats.read_samples,
                  cache: dict | None = None) -> dict[str, Rates]:
    """key -> Rates for every recorded device; a log that cannot be read is skipped.

    `cache` (key -> (path, signature, rates, last sample time)) skips logs whose (mtime, size) did not
    change; the log-age check is re-applied on every call.
    """
    out: dict[str, Rates] = {}
    since = now - log_stats.RATE_WINDOW_DAYS * 86400.0
    for key, path in targets.items():
        try:
            sig = _signature(path)
            hit = cache.get(key) if cache is not None else None
            if hit is not None and sig is not None and hit[0] == path and hit[1] == sig:
                rates, last_t = hit[2], hit[3]
            else:
                samples = _read(reader, path, since)
                rates = log_stats.estimate_rates(samples, now, max_age_s=None)
                last_t = samples[-1].t if samples else None
                if cache is not None and sig is not None:
                    cache[key] = (path, sig, rates, last_t)
            if last_t is None or now - last_t > log_stats.EST_MAX_LOG_AGE_S:
                rates = Rates(None, None)
            out[key] = rates
        except Exception as exc:  # noqa: BLE001 - one bad log must not hide the others
            _warn_once("rate estimate", exc)
    if cache is not None:
        for k in [k for k in cache if k not in targets]:
            del cache[k]
    return out


class RemainingEstimator(QObject):
    """Keeps the latest Rates per visibility key; recomputes off the UI thread."""

    updated = Signal()
    _done = Signal(int, object)

    def __init__(self, targets: Callable[[], dict[str, Path]], clock=time.time, parent=None):
        super().__init__(parent)
        self._targets = targets
        self._clock = clock
        self._rates: dict[str, Rates] = {}
        self._attempted: set[str] = set()
        self._running = False
        self._pending = False
        self._gen = 0
        self._stopped = False
        self._cache: dict = {}
        self._done.connect(self._on_done)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self._stopped = False
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True
        self._pending = False
        self._gen += 1
        self._timer.stop()

    def rates(self) -> dict[str, Rates]:
        return dict(self._rates)

    def refresh_if_new(self) -> None:
        """Compute right away when a recorded device has not been measured yet."""
        try:
            keys = set(self._targets())
            self._attempted &= keys             # recording stopped: measure again when it restarts
            if keys - self._attempted:
                self.refresh()
        except Exception as exc:  # noqa: BLE001
            _warn_once("remaining refresh", exc)

    def refresh(self) -> None:
        if self._stopped:
            return
        if self._running:
            self._pending = True
            return
        try:
            targets = dict(self._targets())
        except Exception as exc:  # noqa: BLE001
            _warn_once("remaining targets", exc)
            return
        self._attempted |= set(targets)
        if not targets:
            if self._rates:
                self._rates = {}
                self.updated.emit()
            return
        self._running = True
        self._gen += 1
        gen, now, done, cache = self._gen, self._clock(), self._done, self._cache

        def work() -> None:
            try:
                res = compute_rates(targets, now, cache=cache)
            except Exception as exc:  # noqa: BLE001
                _warn_once("remaining estimate", exc)
                res = {}
            try:
                done.emit(gen, res)
            except RuntimeError:        # object already destroyed
                pass

        threading.Thread(target=work, daemon=True, name="tbw-remaining").start()

    def _on_done(self, gen: int, res) -> None:
        self._running = False
        if self._stopped:
            return
        if gen == self._gen:
            self._rates = dict(res)
            self.updated.emit()
        if self._pending:
            self._pending = False
            self.refresh()
