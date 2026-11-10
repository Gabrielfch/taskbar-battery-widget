import pytest

from taskbar_battery_widget.models import KINDS
from taskbar_battery_widget.naming import clean_name, kind_from_appearance, kind_from_cod, kind_from_name


@pytest.mark.parametrize("raw,expected", [
    ("Razer BlackShark V2 HS Hands-Free AG", "Razer BlackShark V2 HS"),
    ("Headset Hands-Free", "Headset"),
    ("Speaker Stereo", "Speaker"),
    ("Speaker Stereo Audio", "Speaker"),
    ("AVRCP Transport for Sony WH-1000XM4", "Sony WH-1000XM4"),
    ("Sony WH AVRCP Transport", "Sony WH"),
    ("Auriculares Manos libres AG", "Auriculares"),
    ("Transporte AVRCP para Altavoz", "Altavoz"),
    ("  Xbox Wireless Controller  ", "Xbox Wireless Controller"),
    ("Audio", "Audio"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_clean_name_empty():
    assert clean_name("") == ""


def test_cod_headset_razer():
    assert kind_from_cod(4, 1) == "headset"


@pytest.mark.parametrize("major,minor,kind", [
    (1, 3, "computer"), (1, 4, "tablet"), (2, 3, "phone"), (4, 5, "speaker"),
    (4, 6, "headset"), (4, 4, "microphone"), (5, 0x10, "keyboard"), (5, 0x20, "mouse"),
    (5, 2, "gamepad"), (6, 32, "printer"), (7, 1, "watch"),
])
def test_cod_mapping(major, minor, kind):
    assert kind_from_cod(major, minor) == kind


def test_cod_unknown():
    assert kind_from_cod(None, None) is None
    assert kind_from_cod(31, 0) is None


def test_appearance_xbox_gamepad():
    assert kind_from_appearance(964) == "gamepad"


@pytest.mark.parametrize("app,kind", [
    (961, "keyboard"), (962, "mouse"), (64, "phone"), (192, "watch"), (128, "computer"),
])
def test_appearance_mapping(app, kind):
    assert kind_from_appearance(app) == kind


def test_appearance_unknown():
    assert kind_from_appearance(None) is None
    assert kind_from_appearance(0) is None


@pytest.mark.parametrize("name,kind", [
    ("Xbox Wireless Controller", "gamepad"), ("Logitech MX Master 3", "mouse"),
    ("Galaxy Buds Pro", "headset"), ("JBL Flip 5", "speaker"), ("Something", "generic"),
    ("", "generic"),
])
def test_kind_from_name(name, kind):
    assert kind_from_name(name) == kind
    assert kind in KINDS
