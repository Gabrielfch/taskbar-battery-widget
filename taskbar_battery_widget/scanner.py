"""Paired-device discovery + battery lookup via WinRT. `merge` is pure and unit-tested."""
from __future__ import annotations

import re
import warnings

from .models import Device
from .naming import clean_name, kind_from_appearance, kind_from_cod, kind_from_name
from .winrt_props import prop

BATTERY_KEY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"
AEP_PROPS = [
    "System.Devices.Aep.ContainerId",
    "System.Devices.Aep.DeviceAddress",
    "System.Devices.Aep.IsConnected",
    "System.Devices.Aep.Bluetooth.Cod.Major",
    "System.Devices.Aep.Bluetooth.Cod.Minor",
    "System.Devices.Aep.Bluetooth.Le.Appearance",
]
NODE_PROPS = [BATTERY_KEY, "System.Devices.ContainerId", "System.Devices.Present"]
NODE_AQS = 'System.Devices.DeviceInstanceId:~<"BTH"'

_ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_MAC_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{12}(?![0-9a-fA-F])")


def _norm_guid(value) -> str:
    s = str(value or "").strip().strip("{}").lower()
    return "" if s == _ZERO_GUID else s


def _norm_mac(value) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", str(value or "")).lower()


def _valid_battery(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        return None
    return value


def merge(paired: list[dict], battery_nodes: list[dict]) -> list[Device]:
    """Join paired devices with battery nodes (container id, else MAC in instance id)."""
    # Group paired entries describing the same physical device (same container id).
    groups: dict[str, list[dict]] = {}
    for p in paired:
        cid = _norm_guid(p.get("container_id"))
        groups.setdefault(cid or "id:" + str(p.get("id", "")), []).append(p)

    nodes = []
    for n in battery_nodes:
        b = _valid_battery(n.get("battery"))
        if b is None:
            continue
        nodes.append({
            "name": n.get("name", ""), "battery": b,
            "cid": _norm_guid(n.get("container_id")),
            "present": bool(n.get("present", False)),
            "macs": {m.lower() for m in _MAC_RE.findall(n.get("instance_id") or "")},
        })

    result: list[Device] = []
    for key, members in groups.items():
        # primary: connected first, then classic before LE, keep input order otherwise
        primary = sorted(members, key=lambda p: (not p.get("connected"), bool(p.get("is_le"))))[0]
        # stable id: Classic endpoint when present, else LE (independent of who is connected)
        id_src = sorted(members, key=lambda p: bool(p.get("is_le")))[0]
        cid = key if not key.startswith("id:") else ""
        addresses = {_norm_mac(m.get("address")) for m in members} - {""}

        matched = [n for n in nodes
                   if (cid and n["cid"] == cid) or (n["macs"] & addresses)]
        present = [n["battery"] for n in matched if n["present"]]
        if present:
            battery = max(present)
        elif matched:
            battery = max(n["battery"] for n in matched)
        else:
            battery = None

        kind = None
        for m in members:
            kind = kind or kind_from_cod(m.get("major"), m.get("minor"))
        for m in members:
            kind = kind or kind_from_appearance(m.get("appearance"))

        name = clean_name(primary.get("name") or "")
        if not name and matched:
            name = clean_name(matched[0]["name"])
        if kind is None:
            kind = kind_from_name(name)

        result.append(Device(
            id=str(id_src.get("id", "")),
            name=name,
            kind=kind,
            battery=battery,
            connected=any(bool(m.get("connected")) for m in members),
            address=primary.get("address") or next(iter(sorted(addresses)), ""),
        ))
    return result


async def _find_all(aqs, props, kind=None):
    from winrt.windows.devices.enumeration import DeviceInformation

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if kind is None:
            op = DeviceInformation.find_all_async_aqs_filter_and_additional_properties(aqs, props)
        else:
            op = DeviceInformation.find_all_async_with_kind_aqs_filter_and_additional_properties(
                aqs, props, kind)
    return await op


async def _collect_paired() -> list[dict]:
    from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice

    out: list[dict] = []
    for cls, is_le in ((BluetoothDevice, False), (BluetoothLEDevice, True)):
        try:
            aqs = cls.get_device_selector_from_pairing_state(True)
            infos = await _find_all(aqs, AEP_PROPS)
        except Exception:
            continue
        for d in infos:
            p = d.properties
            out.append({
                "id": d.id,
                "name": d.name,
                "container_id": str(prop(p, "System.Devices.Aep.ContainerId", "")),
                "address": prop(p, "System.Devices.Aep.DeviceAddress", ""),
                "connected": bool(prop(p, "System.Devices.Aep.IsConnected", False)),
                "major": prop(p, "System.Devices.Aep.Bluetooth.Cod.Major"),
                "minor": prop(p, "System.Devices.Aep.Bluetooth.Cod.Minor"),
                "appearance": prop(p, "System.Devices.Aep.Bluetooth.Le.Appearance"),
                "is_le": is_le,
            })
    return out


async def _collect_nodes() -> list[dict]:
    from winrt.windows.devices.enumeration import DeviceInformationKind

    infos = await _find_all(NODE_AQS, NODE_PROPS, DeviceInformationKind.DEVICE)
    out: list[dict] = []
    for d in infos:
        p = d.properties
        battery = prop(p, BATTERY_KEY)
        if battery is None:
            continue
        out.append({
            "name": d.name,
            "battery": battery,
            "container_id": str(prop(p, "System.Devices.ContainerId", "")),
            "present": bool(prop(p, "System.Devices.Present", False)),
            "instance_id": d.id,
        })
    return out


async def scan() -> list[Device]:
    paired = await _collect_paired()
    try:
        nodes = await _collect_nodes()
    except Exception:
        nodes = []
    return merge(paired, nodes)


async def is_radio_on() -> bool:
    """False only when a Bluetooth radio exists and all are off. No radio / unknown errors => True
    (the UI then shows "no devices" rather than "OFF")."""
    try:
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState

        radios = await Radio.get_radios_async()
        bt = [r for r in radios if r.kind == RadioKind.BLUETOOTH]
        if not bt:
            return True
        return any(r.state == RadioState.ON for r in bt)
    except Exception:
        return True
