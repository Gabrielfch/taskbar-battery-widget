"""Low-battery alerts with per-device cooldown and hysteresis re-arm."""
from __future__ import annotations

from .i18n import tr

REARM_MARGIN = 5


class AlertManager:
    def __init__(self, config):
        self._config = config
        self._last_notified: dict[str, float] = {}

    def check(self, devices, now: float) -> list[tuple[str, str]]:
        if not self._config.get("notifications_enabled"):
            return []
        threshold = self._config.get("low_threshold")
        cooldown = self._config.get("notification_cooldown_min") * 60
        out: list[tuple[str, str]] = []
        seen = {d.id for d in devices}
        for stale in [k for k in self._last_notified if k not in seen]:
            del self._last_notified[stale]
        for d in devices:
            if d.battery is None or not d.connected:
                continue
            if d.battery > threshold + REARM_MARGIN:
                self._last_notified.pop(d.id, None)
                continue
            if d.battery > threshold or d.charging:
                continue
            if getattr(d, "idle", False) and d.id in self._last_notified:
                continue                    # asleep: never re-notify, whatever the cooldown
            last = self._last_notified.get(d.id)
            if last is None or now - last >= cooldown:
                self._last_notified[d.id] = now
                out.append((tr("low_title"), tr("low_body", name=d.name, level=d.battery)))
        return out
