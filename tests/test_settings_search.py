"""Settings search, section headers, option previews and the new pill options."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import widget as w
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.settings_model import Choice, Header

from test_settings_model import make
from test_settings_view import Env


# ------------------------------------------------------------------ model search
def test_search_finds_by_label_keyword_and_option():
    _cfg, _calls, m = make(pill_style="transparent")
    assert [h.node.id for h in m.search("theme")][:1] == ["app_theme"]
    assert "app_theme" in [h.node.id for h in m.search("dark mode")]          # keyword
    assert "attach_to_taskbar" in [h.node.id for h in m.search("autohide")]
    assert "palette" in [h.node.id for h in m.search("pastel")]               # an option label
    assert m.search("") == [] and m.search("   ") == []
    assert m.search("zzzz-nothing") == []


def test_search_ranks_label_matches_first_and_gives_the_trail():
    _cfg, _calls, m = make()
    hits = m.search("position")
    assert hits[0].rank == 0
    pos = next(h for h in m.search("stack") if h.node.id == "stack_devices")
    assert pos.trail == ["Personalization", "Position"]
    assert next(h for h in m.search("start with")).trail == []                # on the root page


def test_search_skips_headers_and_separators():
    _cfg, _calls, m = make()
    assert not any(isinstance(h.node, Header) for h in m.search("app"))


def test_palette_and_icon_options_carry_previews():
    _cfg, _calls, m = make(custom_colors={"high": "#00FF00", "medium": "#FFFF00", "low": "#FF0000"})
    pal = m.find("palette")
    assert isinstance(pal, Choice)
    for o in pal.options():
        kind, data = o.preview
        assert kind == "swatch" and len(data) == 3
    assert [o.preview for o in m.find("icon_style").options()][0] == ("icon", "outline")


# ------------------------------------------------------------------ view
def _panel(qapp):
    set_language("en")
    env = Env()
    p = dp.DevicePanel(dark=True)
    p.set_model(build_panel_model([Device(id="a", name="Mouse", kind="mouse", battery=80, connected=True)],
                                  set(), False, 20, True, True))
    p.set_settings_model(env.model)
    p.settings_requested.connect(lambda _r: p.show_settings())
    p.show_at(QPoint(400, 400))
    p._toolbar["settings"].click()
    return p, p.settings_view


def test_search_field_filters_and_reveal_opens_the_setting(qapp):
    p, v = _panel(qapp)
    try:
        assert v.search_field().isVisible()
        QTest.keyClicks(v.search_field(), "stack")
        assert v.query == "stack"
        assert "result:stack_devices" in v.rows()
        v.row("result:stack_devices").click()
        assert v.path_ids == ["root", "personalization", "position"]
        assert v.query == "" and not v.search_field().isVisible()
        assert p.focusWidget() is v.row("stack_devices")
    finally:
        p.close()


def test_search_reveal_unfolds_a_choice(qapp):
    p, v = _panel(qapp)
    try:
        v.search_field().setText("theme")
        v.row("result:app_theme").click()
        assert v.open_choice == "app_theme" and v.path_ids == ["root", "personalization"]
    finally:
        p.close()


def test_search_escape_clears_then_back_leaves_results(qapp):
    p, v = _panel(qapp)
    try:
        v.search_field().setText("zzzz")
        assert v.rows() == {}                       # only the "no matching settings" note
        QTest.keyClick(v.search_field(), Qt.Key.Key_Escape)
        assert v.query == "" and "sources" in v.rows()
        v.search_field().setText("alerts")
        assert v.go_back() is True and v.query == "" and v.path_ids == ["root"]
    finally:
        p.close()


def test_search_hidden_inside_sub_pages(qapp):
    p, v = _panel(qapp)
    try:
        v.row("personalization").click()
        assert not v.search_field().isVisible()
        assert "hdr_pills" not in v.rows()          # headers are labels, not rows
    finally:
        p.close()


# ------------------------------------------------------------------ pill options
def _widget(qapp, tmp_path, **cfg_values):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = Config(tmp_path / "config.json")
    for k, val in cfg_values.items():
        cfg.set(k, val)
    wd = w.TaskbarWidget(cfg)
    wd.set_devices([Device("1", "Super Long Keyboard Name", "keyboard", 80, True)])
    return wd


def test_pill_text_defaults(qapp, tmp_path):
    assert _widget(qapp, tmp_path).pill_texts() == ["80%"]


def test_pill_without_percent_sign_and_with_names(qapp, tmp_path):
    wd = _widget(qapp, tmp_path, pill_percent_sign=False, pill_show_names=True)
    text = wd.pill_texts()[0]
    assert text.endswith(" 80") and "%" not in text
    assert len(text.split(" 80")[0]) <= w.PILL_NAME_MAX and text.startswith("Super Long")


def test_font_weight_option(qapp, tmp_path):
    from PySide6.QtGui import QFont
    assert _widget(qapp, tmp_path / "a", pill_font_weight="bold")._font().weight() == QFont.Weight.Bold
    assert _widget(qapp, tmp_path / "b")._font().weight() == QFont.Weight.DemiBold


def test_config_rejects_bad_pill_values(tmp_path):
    import pytest
    cfg = Config(tmp_path / "c.json")
    for key, bad in (("pill_font_weight", "heavy"), ("pill_show_bar", "yes")):
        with pytest.raises(ValueError):
            cfg.set(key, bad)
    assert cfg.get("pill_font_weight") == "semibold" and cfg.get("pill_show_bar") is True


def test_search_leaves_out_hidden_rows(qapp):
    _cfg, _calls, m = make(pill_style="classic")
    assert "pill_separators" not in [h.node.id for h in m.search("dividers")]   # only for Transparent
    _cfg, _calls, m = make(pill_style="transparent")
    assert "pill_separators" in [h.node.id for h in m.search("dividers")]


def test_search_ignores_accents():
    _cfg, _calls, m = make()
    set_language("es")              # labels are translated when read, so switch after building
    try:
        assert "position" in [h.node.id for h in m.search("posicion")]
        assert "position" in [h.node.id for h in m.search("POSICIÓN")]
    finally:
        set_language("en")


def test_search_field_is_styled_once(qapp):
    p, v = _panel(qapp)
    try:
        field = v.search_field()
        before = len(field.actions())
        for q in ("a", "al", "ale", "", "theme"):
            field.setText(q)
        assert len(field.actions()) == before
    finally:
        p.close()
