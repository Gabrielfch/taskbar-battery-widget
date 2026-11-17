"""One shared, low-cost animation clock plus the app's own two pulses.

Nothing here draws. Widgets register a *demand* callable that answers "how soon do I need to repaint?"
(milliseconds, or None when nothing of theirs is animated and visible). The clock owns the only QTimer:

* it runs only while someone has a demand, animations are enabled in the settings and Windows has its own
  animations on (SPI_GETCLIENTAREAANIMATION, cached and re-read about once a minute);
* its interval is the nearest demand, never below MIN_INTERVAL_MS (<= 15 ticks per second);
* on every tick it emits ``tick(now_ms)``; the receivers repaint only what is animated.

While paused (``allowed()`` false, or a widget with no demand) the callers draw the still version.
"""
from __future__ import annotations

import math
import time
import weakref

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor

from .colors import pill_tint

MIN_INTERVAL_MS = 66            # <= 15 fps whatever an animation asks for
MAX_INTERVAL_MS = 2000
PULSE_INTERVAL_MS = 100         # the built-in pulses are sampled at 10 fps
SYSTEM_RECHECK_S = 60.0         # how long a Windows "animations on/off" answer is trusted

CHARGE_PERIOD_MS = 2000         # charging bolt: opacity 60-100 %
CHARGE_MIN_OPACITY = 0.6
ALERT_PERIOD_MS = 1500          # critical level: border / tint pulse (0.67 Hz, far below 3 flashes/s)
ALERT_BORDER_MIN_ALPHA = 200    # border opacity at the trough (255 = the still look)
ALERT_FILL_SHARE = 1.0          # how far the tint moves from "low" towards "low_hover" at the peak

_SPI_GETCLIENTAREAANIMATION = 0x1042


def system_animations_enabled() -> bool:
    """False when Windows has "Show animations in Windows" turned off (also true if it cannot be read)."""
    try:
        import ctypes
        import sys
        if sys.platform != "win32":
            return True
        flag = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0)
        return bool(flag.value) if ok else True
    except Exception:  # noqa: BLE001 - an unreadable setting must not disable the feature
        return True


def charge_pulse(now_ms: int | None) -> float:
    """Opacity (CHARGE_MIN_OPACITY .. 1) of the charging bolt; 1 when paused."""
    if now_ms is None:
        return 1.0
    wave = 0.5 + 0.5 * math.cos(2 * math.pi * (now_ms % CHARGE_PERIOD_MS) / CHARGE_PERIOD_MS)
    return CHARGE_MIN_OPACITY + (1.0 - CHARGE_MIN_OPACITY) * wave


def alert_pulse(now_ms: int | None) -> float:
    """0 .. 1 swing of the critical-level pill (0 = the still look); 0 when paused."""
    if now_ms is None:
        return 0.0
    return 0.5 - 0.5 * math.cos(2 * math.pi * (now_ms % ALERT_PERIOD_MS) / ALERT_PERIOD_MS)


def alert_fill(dark: bool, pulse: float) -> QColor:
    """Tint of a critical pill: "low" at pulse 0 (the still look) moving towards "low_hover" at 1. Both ends are
    colours the text already has >= 4.5:1 against, and the blend stays between them."""
    lo, hi = pill_tint("low", dark), pill_tint("low_hover", dark)
    t = max(0.0, min(1.0, pulse * ALERT_FILL_SHARE))
    out = QColor(lo)
    out.setAlpha(round(lo.alpha() + (hi.alpha() - lo.alpha()) * t))
    return out


def alert_border_alpha(pulse: float) -> int:
    """Opacity of the 1 px outline: 255 at pulse 0, ALERT_BORDER_MIN_ALPHA at 1 (the outline never disappears)."""
    t = max(0.0, min(1.0, pulse))
    return round(255 - (255 - ALERT_BORDER_MIN_ALPHA) * t)


class AnimationClock(QObject):
    tick = Signal(int)              # frame time in ms (see now_ms())
    state_changed = Signal()        # enabled flag / Windows setting / running state changed: repaint still

    def __init__(self, parent=None, time_fn=time.monotonic, system_fn=None):
        super().__init__(parent)
        self._time_fn = time_fn
        self._system_fn = system_fn          # None: animation.system_animations_enabled (looked up per call)
        self._epoch = time_fn()
        self._enabled = True
        self._system_ok = True
        self._system_at: float | None = None
        self._clients: list = []             # [(weakref to owner, weak method)]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timeout)
        self._last_state: tuple = (True, True, False)

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self._system_at = None              # a settings change also re-reads the Windows value
        self.refresh()

    def system_ok(self) -> bool:
        now = self._time_fn()
        if self._system_at is None or now - self._system_at >= SYSTEM_RECHECK_S:
            fn = self._system_fn or system_animations_enabled
            try:
                self._system_ok = bool(fn())
            except Exception:  # noqa: BLE001
                self._system_ok = True
            self._system_at = now
        return self._system_ok

    def allowed(self) -> bool:
        """Animations may run: enabled in the settings and not switched off in Windows."""
        return self._enabled and self.system_ok()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def interval(self) -> int:
        return self._timer.interval()

    def now_ms(self) -> int:
        """Milliseconds on the clock all animations are drawn against."""
        return int((self._time_fn() - self._epoch) * 1000)

    def set_time_source(self, fn) -> None:
        """Replace the time function (seconds, monotonic); the epoch is re-based so now_ms() stays continuous."""
        cur = self.now_ms()
        self._time_fn = fn
        self._epoch = fn() - cur / 1000.0
        self._system_at = None

    # ------------------------------------------------------------------ clients
    def register(self, owner: QObject, demand) -> None:
        """`demand()` -> ms until `owner` next needs a repaint, or None. `owner` is held weakly; `demand`
        should be one of its bound methods."""
        self.unregister(owner)
        try:
            ref = weakref.WeakMethod(demand)
        except TypeError:
            ref = (lambda d=demand: d)
        self._clients.append((weakref.ref(owner), ref))

    def unregister(self, owner) -> None:
        self._clients = [c for c in self._clients if c[0]() is not None and c[0]() is not owner]

    def _demands(self) -> list[int]:
        out: list[int] = []
        alive = []
        for oref, dref in self._clients:
            fn = dref()
            if oref() is None or fn is None:
                continue
            alive.append((oref, dref))
            try:
                v = fn()
            except RuntimeError:             # owner deleted on the C++ side
                continue
            except Exception:  # noqa: BLE001 - one broken client must not stop the others
                continue
            if v is not None:
                out.append(int(v))
        self._clients = alive
        return out

    def refresh(self) -> None:
        """Re-evaluate the demands: start, stop or retime the timer."""
        want = self._demands() if self.allowed() else []
        if not want:
            if self._timer.isActive():
                self._timer.stop()
        else:
            ms = max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, min(want)))
            if not self._timer.isActive() or self._timer.interval() != ms:
                self._timer.start(ms)
        state = (self._enabled, self._system_ok, self._timer.isActive())
        if state != self._last_state:
            self._last_state = state
            self.state_changed.emit()

    def _on_timeout(self) -> None:
        self.tick.emit(self.now_ms())
        self.refresh()


# ------------------------------------------------------------------ the shared instance
_clock: AnimationClock | None = None


def clock() -> AnimationClock:
    global _clock
    if _clock is None:
        _clock = AnimationClock()
    return _clock


def reset() -> None:
    """Drop the shared clock (tests): its timer is stopped and the next clock() builds a fresh one."""
    global _clock
    c, _clock = _clock, None
    if c is not None:
        try:
            c._timer.stop()
            c._clients = []
        except RuntimeError:
            pass
