from taskbar_battery_widget.scanner import merge

CID = "{11111111-2222-3333-4444-555555555555}"


def paired(**kw):
    d = dict(id="dev1", name="Razer BlackShark Hands-Free AG", container_id=CID,
             address="aa:bb:cc:11:22:33", connected=True, major=4, minor=1,
             appearance=None, is_le=False)
    d.update(kw)
    return d


def node(**kw):
    d = dict(name="Node", battery=56, container_id=CID, present=True,
             instance_id="BTHENUM\\DEV_AABBCC112233\\7&1&0")
    d.update(kw)
    return d


def test_join_by_container_id():
    [d] = merge([paired()], [node(instance_id="X")])
    assert d.battery == 56 and d.kind == "headset" and d.name == "Razer BlackShark"
    assert d.connected and d.id == "dev1"


def test_container_id_case_and_braces_insensitive():
    [d] = merge([paired()], [node(container_id=CID.strip("{}").upper(), instance_id="X")])
    assert d.battery == 56


def test_mac_fallback_when_container_differs():
    [d] = merge([paired()], [node(container_id="{99999999-0000-0000-0000-000000000000}")])
    assert d.battery == 56


def test_no_match_gives_none_battery():
    [d] = merge([paired()], [node(container_id="{99999999-0000-0000-0000-000000000000}",
                                  instance_id="BTHENUM\\DEV_AABBCCDDEEFF\\1")])
    assert d.battery is None


def test_zero_container_does_not_join_everything():
    p = [paired(container_id="{00000000-0000-0000-0000-000000000000}", address="")]
    [d] = merge(p, [node(container_id="{00000000-0000-0000-0000-000000000000}", instance_id="X")])
    assert d.battery is None


def test_present_node_preferred_over_stale():
    nodes = [node(battery=90, present=False), node(battery=40, present=True)]
    [d] = merge([paired()], nodes)
    assert d.battery == 40


def test_max_of_all_when_none_present():
    nodes = [node(battery=30, present=False), node(battery=45, present=False)]
    [d] = merge([paired(connected=False)], nodes)
    assert d.battery == 45 and not d.connected


def test_multiple_present_nodes_take_max():
    [d] = merge([paired()], [node(battery=75), node(battery=78)])
    assert d.battery == 78


def test_dedupe_same_container_prefers_connected_classic():
    classic = paired(id="classic", connected=True)
    le = paired(id="le", connected=False, is_le=True, major=None, minor=None, appearance=964)
    result = merge([le, classic], [node()])
    assert len(result) == 1
    assert result[0].id == "classic" and result[0].connected


def test_kind_falls_back_to_appearance_then_name():
    le = paired(id="x", name="Xbox Wireless Controller", major=None, minor=None,
                appearance=964, is_le=True)
    assert merge([le], [])[0].kind == "gamepad"
    le2 = paired(id="y", name="My Mouse", major=None, minor=None, appearance=None,
                 container_id="{aaaaaaaa-0000-0000-0000-000000000000}")
    assert merge([le2], [])[0].kind == "mouse"


def test_invalid_battery_values_ignored():
    [d] = merge([paired()], [node(battery=255), node(battery=None)])
    assert d.battery is None


def test_distinct_devices_stay_separate():
    a = paired(id="a", container_id="{aaaaaaaa-0000-0000-0000-000000000000}", address="")
    b = paired(id="b", container_id="{bbbbbbbb-0000-0000-0000-000000000000}", address="")
    n = node(container_id="{bbbbbbbb-0000-0000-0000-000000000000}", instance_id="X")
    res = {d.id: d.battery for d in merge([a, b], [n])}
    assert res == {"a": None, "b": 56}


def test_is_radio_on_without_radios_is_true(monkeypatch):
    import asyncio
    import sys
    import types

    class FakeRadio:
        @staticmethod
        async def get_radios_async():
            return []

    mod = types.ModuleType("winrt.windows.devices.radios")
    mod.Radio = FakeRadio
    mod.RadioKind = types.SimpleNamespace(BLUETOOTH=1)
    mod.RadioState = types.SimpleNamespace(ON=1)
    monkeypatch.setitem(sys.modules, "winrt.windows.devices.radios", mod)
    from taskbar_battery_widget import scanner
    assert asyncio.run(scanner.is_radio_on()) is True


def test_dual_mode_id_is_stable_regardless_of_connection():
    classic = paired(id="classic", connected=False)
    le = paired(id="le", connected=True, is_le=True, major=None, minor=None, appearance=964)
    only_le = merge([le, classic], [])[0]
    both_off = merge([dict(le, connected=False), classic], [])[0]
    assert only_le.id == both_off.id == "classic"
    assert only_le.connected and not both_off.connected


def test_le_only_device_keeps_le_id():
    le = paired(id="le", is_le=True)
    assert merge([le], [])[0].id == "le"
