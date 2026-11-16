"""Languages: key parity, placeholders, detection, per-language formats, rendering without errors."""
import datetime as dt
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

from taskbar_battery_widget import i18n, stats_window as sw
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import EN, set_language
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.remaining import format_remaining, remaining_label
from taskbar_battery_widget.tray import TrayController

ALL = ["en", "es", "pt", "fr", "ru", "zh", "ja"]
NEW = ["pt", "fr", "ru", "zh", "ja"]
_PH = re.compile(r"\{(\w+)\}")


def setup_function(_):
    set_language("en")


def test_languages_registered():
    assert set(i18n.LANGUAGES) == set(ALL)
    assert i18n.LANGUAGES["pt"] == "Português" and i18n.LANGUAGES["zh"] == "简体中文"
    assert i18n.LANGUAGES["ja"] == "日本語" and i18n.LANGUAGES["ru"] == "Русский"
    assert i18n.LANGUAGES["fr"] == "Français"
    for code in ALL:
        assert code in i18n._TABLES


@pytest.mark.parametrize("lang", ALL)
def test_key_parity_placeholders_and_non_empty(lang):
    table = i18n._TABLES[lang]
    assert set(table) == set(EN), (sorted(set(EN) - set(table)), sorted(set(table) - set(EN)))
    for key, text in table.items():
        assert isinstance(text, str) and text.strip(), (lang, key)
        assert set(_PH.findall(text)) == set(_PH.findall(EN[key])), (lang, key, text)


@pytest.mark.parametrize("lang", NEW)
def test_every_text_formats_with_its_placeholders(lang):
    for key in EN:
        names = set(_PH.findall(EN[key]))
        out = i18n.tr_in(lang, key, **{n: "X" for n in names})
        assert "{" not in out, (lang, key, out)


@pytest.mark.parametrize("code,expected", [
    ("pt-BR", "pt"), ("pt_PT", "pt"), ("fr-CA", "fr"), ("ru-RU", "ru"), ("zh-CN", "zh"),
    ("zh-Hans", "zh"), ("zh-SG", "zh"), ("zh_CN", "zh"), ("ja-JP", "ja"), ("es-MX", "es"),
    ("en-GB", "en"), ("de-DE", "en"), ("", "en"), (None, "en")])
def test_detection(code, expected):
    assert i18n.normalize_language(code) == expected


@pytest.mark.parametrize("lang,expected", [
    ("en", "03/07"), ("es", "07/03"), ("pt", "07/03"), ("fr", "07/03"), ("ru", "07.03"),
    ("zh", "03/07"), ("ja", "03/07")])
def test_date_without_year(lang, expected):
    assert sw.format_day(dt.date(2026, 3, 7), lang) == expected


@pytest.mark.parametrize("lang,expected", [
    ("en", "03/07/26"), ("es", "07/03/26"), ("pt", "07/03/26"), ("fr", "07/03/26"), ("ru", "07.03.26"),
    ("zh", "26/03/07"), ("ja", "26/03/07")])
def test_date_with_year(lang, expected):
    assert sw.format_day(dt.date(2026, 3, 7), lang, year=True) == expected


@pytest.mark.parametrize("lang,expected", [
    ("en", "2.1"), ("es", "2,1"), ("pt", "2,1"), ("fr", "2,1"), ("ru", "2,1"), ("zh", "2.1"), ("ja", "2.1")])
def test_decimal_separator(lang, expected):
    assert sw.format_cycles(2.1, 3, lang) == expected
    assert sw.format_rate(2.1, lang).startswith(expected)


@pytest.mark.parametrize("lang,dur,short,rem", [
    ("en", "1 d 4 h", "5 h 30 min", "≈ 6 h left"),
    ("fr", "1 j 4 h", "5 h 30 min", "≈ 6 h restantes"),
    ("ru", "1 д 4 ч", "5 ч 30 мин", "осталось ≈ 6 ч"),
    ("zh", "1 天 4 小时", "5 小时 30 分钟", "剩余 ≈ 6 小时"),
    ("ja", "1 日 4 時間", "5 時間 30 分", "残り ≈ 6 時間")])
def test_duration_units_are_localized(lang, dur, short, rem):
    assert sw.format_duration(28.0, lang) == dur
    assert sw.format_duration(5.5, lang) == short
    assert remaining_label(6, False, lang) == rem
    set_language(lang)
    assert format_remaining(72.0).startswith("≈ 3")


def _dev(i):
    return Device(id=f"d{i}", name=f"Device {i}", kind=["mouse", "keyboard", "headset"][i % 3],
                  battery=40 + i, connected=True)


@pytest.mark.parametrize("lang", ["zh", "ja", "ru", "pt", "fr"])
def test_panel_and_personalization_page_render(qapp, tmp_path, lang):
    set_language(lang)
    t = TrayController(Config(tmp_path / "c.json"))
    t.set_devices([_dev(i) for i in range(3)])
    t.open_panel(QPoint(400, 400))
    p = t._panel
    try:
        assert not p.grab().isNull()
        p.show_settings("personalization")
        assert not p.grab().isNull()
        assert p.settings_view.title_text() == i18n.tr_in(lang, "menu_personalization")
    finally:
        p.close()


def test_no_mojibake_in_sources():
    root = Path(__file__).resolve().parent.parent
    bad = re.compile("Ã[\u0080-¿]|â€|Ð[\u0080-¿]|Ñ[\u0080-¿]|ã€|ã\u0081|ã\u0082|å[\u0080-¿]|�")
    hits = []
    for path in list(root.glob("taskbar_battery_widget/*.py")) + list(root.glob("tests/*.py")):
        if path.name == Path(__file__).name:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                hits.append(f"{path.name}:{n}")
    assert not hits, hits
