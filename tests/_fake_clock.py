"""Fake clock for the HID reader tests.

The fake handles used to sleep real time inside `read()` so a silent device burned the reader's real
budget. `FakeTime` is a drop-in for the `time` module in the reader module and in the test module: its
`monotonic()` is the real clock plus an offset, and `sleep(s)` only moves the offset forward. Deadlines
(budgets, per-device caps, passive windows) therefore elapse exactly as before, but instantly.
"""
import threading
import time as _real


class FakeTime:
    def __init__(self):
        self._offset = 0.0
        self._lock = threading.Lock()

    @property
    def skipped(self) -> float:
        """Seconds of fake time that were 'slept' without waiting."""
        return self._offset

    def monotonic(self) -> float:
        return _real.monotonic() + self._offset

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._offset += max(0.0, float(seconds))

    def __getattr__(self, name):
        return getattr(_real, name)


def install(monkeypatch, *modules) -> FakeTime:
    """Replace `time` in each given module (reader module, test module) by one shared FakeTime."""
    fake = FakeTime()
    for m in modules:
        monkeypatch.setattr(m, "time", fake)
    return fake
