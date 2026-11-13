"""Hidden-device filtering shared by the alerts and the UI (config stores visibility keys only)."""
from __future__ import annotations

import logging

from .models import visibility_key

log = logging.getLogger(__name__)


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


HIDDEN_MAX = 200


def is_hidden(cfg, key: str) -> bool:
    return key in hidden_keys(cfg)


def _save_hidden(cfg, hidden: list[str]) -> bool:
    try:
        cfg.set("hidden_devices", hidden[-HIDDEN_MAX:])
    except Exception:
        log.exception("could not save hidden devices")
        return False
    return True


def set_hidden(cfg, key: str, hidden: bool) -> bool:
    """Hide/show the device with visibility key `key`; returns False when the config could not be saved."""
    if not isinstance(key, str) or not key:
        return False
    try:
        current = cfg.get("hidden_devices")
    except Exception:
        current = None
    keys = [x for x in current if isinstance(x, str)] if isinstance(current, list) else []
    keys = [x for x in keys if x != key]
    if hidden:
        keys.append(key)
    return _save_hidden(cfg, keys)


def show_all(cfg) -> bool:
    """Un-hide every device."""
    return _save_hidden(cfg, [])
