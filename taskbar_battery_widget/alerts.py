"""Low-battery alerts with per-device cooldown and hysteresis re-arm."""
from __future__ import annotations

from .i18n import tr

REARM_MARGIN = 5
FULL_PCT = 100                   # at this level: charged
STOP_FULL_PCT = 99               # stopped charging at/above this: charged
NEAR_FULL_PCT = 95               # a reading below this ends the previous charge session
FULL_KEEP_S = 12 * 3600          # keep the full-charge state this long after the device was last listed
KEEP_STATE_S = 24 * 3600         # keep per-device state this long after it was last seen


class AlertManager:
    def __init__(self, config):
        self._config = config
        self._last_notified: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        # full-charge state per device id (see _check_full)
        self._full: dict[str, dict] = {}

    def _check_full(self, devices, enabled: bool, now: float) -> list[tuple[str, str]]:
        """One notice per charge session.

        A device is armed when seen charging below 100 % (unless it already got its notice) and fires when
        it reaches 100 %, or stops charging at >= 99 %. After the notice it stays quiet until a real
        discharge below 95 %; that state survives disconnections (FULL_KEEP_S). State always advances, so
        re-enabling a switch never replays an old charge.
        """
        out: list[tuple[str, str]] = []
        for d in devices:
            st = self._full.get(d.id)
            if st is None:
                st = self._full[d.id] = {"armed": False, "notified": False, "was": False, "seen": now}
            st["seen"] = now
            if not d.connected or d.battery is None:
                st["was"] = False                   # a reconnection is not a "stopped charging" edge
                continue
            if getattr(d, "stale", False) or getattr(d, "idle", False):
                continue
            b, charging = d.battery, bool(d.charging)
            if b < NEAR_FULL_PCT:
                st["notified"] = False
            if charging and b < FULL_PCT and not st["notified"]:
                st["armed"] = True
            done = b >= FULL_PCT or (st["was"] and not charging and b >= STOP_FULL_PCT)
            if st["armed"] and done:
                st["armed"] = False
                st["notified"] = True
                if enabled:
                    out.append((tr("full_title"), tr("full_body", name=d.name, pct=b)))
            elif b >= FULL_PCT:
                st["notified"] = True               # already full (e.g. at startup): nothing to announce
            st["was"] = charging
        for k in [k for k, v in self._full.items() if now - v["seen"] > FULL_KEEP_S]:
            del self._full[k]
        return out

    def check(self, devices, now: float) -> list[tuple[str, str]]:
        enabled = bool(self._config.get("notifications_enabled"))
        full = self._check_full(list(devices),
                                enabled and bool(self._config.get("full_charge_alert")), now)
        if not enabled:
            return []
        return full + self._check_low(devices, now)

    def _check_low(self, devices, now: float) -> list[tuple[str, str]]:
        threshold = self._config.get("low_threshold")
        cooldown = self._config.get("notification_cooldown_min") * 60
        out: list[tuple[str, str]] = []
        devices = [d for d in devices if not getattr(d, "stale", False)]   # last known != reading
        for d in devices:
            self._last_seen[d.id] = now
        for stale in [k for k in self._last_notified
                      if now - self._last_seen.get(k, now) > KEEP_STATE_S]:
            del self._last_notified[stale]
            self._last_seen.pop(stale, None)
        for k in [k for k in self._last_seen if k not in self._last_notified
                  and now - self._last_seen[k] > KEEP_STATE_S]:
            del self._last_seen[k]
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
