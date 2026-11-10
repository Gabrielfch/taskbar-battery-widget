"""Charging estimation for devices that do not report it (infer from battery rises)."""
from __future__ import annotations

WINDOW_S = 1800.0
MIN_RISE = 2


class _State:
    __slots__ = ("last", "base", "base_time", "charging", "last_rise")

    def __init__(self, level, now):
        self.last = level
        self.base = level
        self.base_time = now
        self.charging = False
        self.last_rise = now


class ChargeEstimator:
    """update() returns True while the device is estimated to be charging."""

    def __init__(self, window_s: float = WINDOW_S, min_rise: int = MIN_RISE):
        self._window = window_s
        self._min_rise = min_rise
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
        st.last = level
        return st.charging
