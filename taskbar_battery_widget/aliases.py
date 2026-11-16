"""User-chosen device aliases. Stored in the config as {visibility_key: alias}.

The alias only changes the *displayed* name (`Device.name`); the original name is kept in
`Device.original_name`, so the detailed-log file name, devices.log and last_known stay untouched.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from .models import is_visibility_key, visibility_key
from .naming import sanitize_name

log = logging.getLogger(__name__)

MAX_ALIAS_LEN = 48


def get_aliases(cfg) -> dict:
    try:
        v = cfg.get("device_aliases")
    except Exception:
        return {}
    return dict(v) if isinstance(v, dict) else {}


def apply_aliases(devices, aliases) -> list:
    """Copy of `devices` with the alias as `name` (and the real name in `original_name`)."""
    if not aliases:
        return list(devices)
    out = []
    for d in devices:
        alias = aliases.get(visibility_key(d.id))
        if alias and alias != d.name:
            d = replace(d, name=alias, original_name=d.original_name or d.name)
        out.append(d)
    return out


def set_alias(cfg, key: str, alias: str) -> bool:
    """Set (or, when empty, remove) the alias of the device with visibility key `key`."""
    if not is_visibility_key(key):
        return False
    aliases = get_aliases(cfg)
    clean = sanitize_name(alias or "", MAX_ALIAS_LEN)
    if clean:
        aliases[key] = clean
    else:
        aliases.pop(key, None)
    try:
        cfg.set("device_aliases", aliases)
    except Exception as e:
        log.warning("could not save the device alias (%s)", type(e).__name__)
        return False
    return True
