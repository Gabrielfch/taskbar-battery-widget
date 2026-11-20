"""User-defined device order (config `device_order`): one ordering shared by the panel, the pills and the tray."""
from __future__ import annotations

from .models import visibility_key

MAX_ORDER = 200


def order_keys(cfg) -> list[str]:
    """The valid keys of `device_order` (empty when unset or unreadable)."""
    try:
        v = cfg.get("device_order")
    except Exception:
        return []
    return [x for x in v if isinstance(x, str) and x] if isinstance(v, (list, tuple)) else []


def apply_order(devices, order) -> list:
    """`devices` sorted by `order` (visibility keys): listed devices first, in that order; the others keep
    their incoming order after them. Unknown keys are ignored. Stable, so hiding / showing or connecting /
    disconnecting a device never changes the relative order of the rest."""
    devices = list(devices)
    pos: dict[str, int] = {}
    for k in order or ():
        if isinstance(k, str) and k not in pos:
            pos[k] = len(pos)
    if not pos:
        return devices
    last = len(pos)
    return sorted(devices, key=lambda d: pos.get(visibility_key(d.id), last))


def reorder(order, shown, section, key: str, new_index: int) -> list[str]:
    """New full `device_order` after moving `key` to `new_index` inside `section`.

    `shown`: keys of every device displayed now (all sections, display order); `section`: keys of the section
    that holds `key`, in display order. Keys of other sections (and of devices not shown now) keep their
    place: only the slots taken by the section's keys are reassigned."""
    full: list[str] = []
    for k in list(order or ()) + list(shown):
        if isinstance(k, str) and k and k not in full:
            full.append(k)
    sec = [k for k in section if k in full]
    if key not in sec:
        return full[:MAX_ORDER]
    moved = [k for k in sec if k != key]
    moved.insert(max(0, min(len(moved), int(new_index))), key)
    slots = iter(moved)
    full = [next(slots) if k in sec else k for k in full]
    shown_set = set(shown)
    while len(full) > MAX_ORDER:                # drop keys of devices not shown now first, from the end
        idx = next((i for i in range(len(full) - 1, -1, -1) if full[i] not in shown_set), len(full) - 1)
        del full[idx]
    return full


def forget(order, key: str) -> list[str]:
    return [k for k in order if k != key]
