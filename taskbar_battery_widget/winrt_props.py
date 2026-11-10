"""Helpers to read boxed WinRT property values (IPropertyValue) into Python values."""
from __future__ import annotations

_TYPE_GETTERS = {
    1: "get_uint8", 2: "get_int16", 3: "get_uint16", 4: "get_int32", 5: "get_uint32",
    6: "get_int64", 7: "get_uint64", 8: "get_single", 9: "get_double",
    11: "get_boolean", 12: "get_string", 16: "get_guid",
}


def unbox(obj):
    """Unbox an IInspectable holding a scalar. Python scalars pass through; unknown -> None."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    from winrt.windows.foundation import IPropertyValue

    pv = IPropertyValue._from(obj)
    getter = _TYPE_GETTERS.get(int(pv.type))
    if getter is None:
        return None
    return getattr(pv, getter)()


def prop(props, key, default=None):
    """Read `key` from an IMapView<string, object>; return default if missing/unreadable."""
    try:
        if not props.has_key(key):
            return default
        value = unbox(props.lookup(key))
    except Exception:
        return default
    return default if value is None else value
