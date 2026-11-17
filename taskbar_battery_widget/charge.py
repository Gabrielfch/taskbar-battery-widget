"""Charging estimation for devices that do not report it (infer from battery rises)."""
from __future__ import annotations

WINDOW_S = 1800.0
MIN_RISE = 2
# Right after a device (re)connects Windows may first report a placeholder level and the real one a few
# seconds later (seen with an Xbox controller over Bluetooth: 50 % -> 73 % in 4 s). Rises in this settling
# period only move the baseline.
SETTLE_S = 120.0
# No battery charges this fast: a bigger jump within a minute is a corrected reading, not charging.
MAX_JUMP = 10
JUMP_S = 60.0


class _State:
    __slots__ = ("last", "last_time", "base", "base_time", "charging", "last_rise", "since")

    def __init__(self, level, now):
        self.last = level
        self.last_time = now
        self.base = level
        self.base_time = now
        self.charging = False
        self.last_rise = now
        self.since = now            # first reading of this connection


class ChargeEstimator:
    """update() returns True while the device is estimated to be charging."""

    def __init__(self, window_s: float = WINDOW_S, min_rise: int = MIN_RISE, settle_s: float = SETTLE_S):
        self._window = window_s
        self._min_rise = min_rise
        self._settle = settle_s
        self._states: dict[str, _State] = {}

    def forget(self, dev_id: str) -> None:
        self._states.pop(dev_id, None)

    def retain(self, ids) -> None:
        """Forget state of every device id not in `ids`."""
        keep = set(ids)
        for k in [k for k in self._states if k not in keep]:
            del self._states[k]

    def update(self, dev_id: str, level: int | None, now: float) -> bool:
        if level is None:
            self._states.pop(dev_id, None)
            return False
        st = self._states.get(dev_id)
        if st is None:
            self._states[dev_id] = _State(level, now)
            return False

        if level < st.last:  # dropping: not charging, new low baseline
            st.charging = False
            st.base, st.base_time = level, now
        elif level > st.last and not st.charging and (
                now - st.since < self._settle
                or (level - st.last > MAX_JUMP and now - st.last_time < JUMP_S)):
            # settling after (re)connect, or an impossible jump: a corrected reading, not charging
            st.base, st.base_time = level, now
        elif level > st.last:
            if st.charging:
                st.last_rise = now
            else:
                if now - st.base_time > self._window:
                    # window expired: restart baseline from the previous level
                    st.base, st.base_time = st.last, now
                if level - st.base >= self._min_rise:
                    st.charging, st.last_rise = True, now
        else:
            if st.charging and now - st.last_rise >= self._window:
                st.charging = False
                st.base, st.base_time = level, now
            elif not st.charging and now - st.base_time > self._window:
                st.base, st.base_time = level, now
        st.last, st.last_time = level, now
        return st.charging
