"""Hidden-device filtering shared by the alerts and the UI (config stores visibility keys only)."""
from __future__ import annotations

from .models import visibility_key


def hidden_keys(cfg) -> set[str]:
    try:
        v = cfg.get("hidden_devices")
    except Exception:
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, (list, tuple, set)) else set()


def visible_devices(devices, cfg) -> list:
    """Devices that the user has not hidden (hidden ones get no alerts and no tray level)."""
    hidden = hidden_keys(cfg)
    if not hidden:
        return list(devices)
    return [d for d in devices if visibility_key(d.id) not in hidden]
