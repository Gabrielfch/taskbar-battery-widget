import json

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QDialog

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget import custom_icons_dialog as cid
from taskbar_battery_widget import device_panel as dp
from taskbar_battery_widget import icons
from taskbar_battery_widget import i18n
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.models import KINDS, Device, visibility_key
from taskbar_battery_widget.panel_model import build_panel_model
from taskbar_battery_widget.tray import TrayController, apply_appearance

KEY = visibility_key("dev-1")
KEY2 = visibility_key("dev-2")


@pytest.fixture(autouse=True)
def _icons(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    ci.set_maps({}, {})
    icons.set_icon_style("outline")
    yield
    ci.set_maps({}, {})
    ci.clear_cache()
    icons.set_icon_style("outline")


def make_icon(name_color="red", w=32, h=32):
    d = ci.icons_dir()
    d.mkdir(parents=True, exist_ok=True)
    import taskbar_battery_widget.custom_icons as m
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(name_color))
    src = d.parent / f"src_{name_color}.png"
    assert img.save(str(src))
    return m.import_image(src)


def test_config_defaults_and_validation(tmp_path):
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("custom_icons_kind") == {} and cfg.get("custom_icons_device") == {}
    good = "0123456789abcdef.png"
    cfg.set("custom_icons_kind", {"mouse": good})
    cfg.set("custom_icons_device", {KEY: good})
    cfg.set("custom_icons_kind", {"mouse": "../evil.png"})      # sanitised: bad entry dropped
    assert cfg.get("custom_icons_kind") == {}
    cfg.set("custom_icons_kind", {"mouse": good})
    # set() sanitises: bad kind dropped
    cfg.set("custom_icons_kind", {"mouse": good, "toaster": good})
    assert cfg.get("custom_icons_kind") == {"mouse": good}
    with pytest.raises(ValueError):
        cfg.set("custom_icons_device", "nope")


def test_config_load_sanitises(tmp_path):
    good = "0123456789abcdef.png"
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "custom_icons_kind": {"mouse": good, "bogus": good, "keyboard": "C:/x.png", "tv": 5},
        "custom_icons_device": {KEY: good, "raw-id": good, KEY2: "..\\a.png"},
    }), encoding="utf-8")
    cfg = Config(p)
    assert cfg.get("custom_icons_kind") == {"mouse": good}
    assert cfg.get("custom_icons_device") == {KEY: good}
    p.write_text(json.dumps({"custom_icons_kind": [1], "custom_icons_device": "x"}), encoding="utf-8")
    cfg = Config(p)
    assert cfg.get("custom_icons_kind") == {} and cfg.get("custom_icons_device") == {}


def test_config_limit(tmp_path):
    good = "0123456789abcdef.png"
    cfg = Config(tmp_path / "c.json")
    many = {visibility_key(str(i)): good for i in range(201)}
    with pytest.raises(ValueError):
        cfg.set("custom_icons_device", many)
    assert len(KINDS) <= 200


def test_priority_device_over_kind():
    a, b = make_icon("red"), make_icon("blue")
    ci.set_maps({"mouse": a}, {KEY: b})
    assert ci.file_for("mouse", KEY) == b
    assert ci.file_for("mouse", KEY2) == a
    assert ci.file_for("keyboard", KEY2) is None


def test_set_maps_bumps_version_only_on_change():
    v = ci.maps_version()
    ci.set_maps({}, {})
    assert ci.maps_version() == v
    ci.set_maps({"mouse": "0123456789abcdef.png"}, {})
    assert ci.maps_version() == v + 1


def test_apply_appearance_pushes_maps(tmp_path):
    cfg = Config(tmp_path / "c.json")
    good = "0123456789abcdef.png"
    cfg.set("custom_icons_kind", {"mouse": good})
    apply_appearance(cfg)
    assert ci.file_for("mouse", KEY) == good


def _row_image(kind="mouse", key=KEY):
    pm = dp._kind_pixmap(kind, QColor(40, 200, 90), 1.0, 50, False, key)
    return pm.toImage()


def test_panel_icon_custom_then_fallback_when_file_deleted():
    default = _row_image()
    name = make_icon("red")
    ci.set_maps({"mouse": name}, {})
    custom = _row_image()
    assert custom != default
    assert custom.pixelColor(10, 10).red() > 200 and custom.pixelColor(10, 10).green() < 50
    (ci.icons_dir() / name).unlink()
    ci.clear_cache()
    assert _row_image() == default


def test_panel_icon_corrupt_file_falls_back():
    default = _row_image()
    name = "0123456789abcdef.png"
    ci.icons_dir().mkdir(parents=True, exist_ok=True)
    (ci.icons_dir() / name).write_bytes(b"not an image")
    ci.set_maps({"mouse": name}, {})
    assert _row_image() == default


def test_panel_row_dimmed_uses_opacity():
    name = make_icon("red")
    ci.set_maps({"mouse": name}, {})
    full = dp._kind_pixmap("mouse", QColor(40, 200, 90, 255), 1.0, 50, False, KEY).toImage()
    dim = dp._kind_pixmap("mouse", QColor(40, 200, 90, 153), 1.0, 50, False, KEY).toImage()
    assert dim.pixelColor(10, 10).alpha() < full.pixelColor(10, 10).alpha()


def test_panel_rebuilds_when_maps_change():
    panel = dp.DevicePanel(dark=True)
    dev = Device(id="dev-1", name="M", kind="mouse", battery=50, connected=True)
    model = build_panel_model([dev], set(), False, 20, True, lang="en")
    panel.set_model(model)
    sig = panel._sig
    panel.set_model(model)
    assert panel._sig == sig
    ci.set_maps({"mouse": "0123456789abcdef.png"}, {})
    panel.set_model(model)
    assert panel._sig != sig


def test_widget_none_style_draws_custom_icon_only_when_set(tmp_path):
    from taskbar_battery_widget import widget as w
    dev = Device(id="dev-1", name="M", kind="mouse", battery=50, connected=True)
    pill = w._Pill("mouse", "50%", "tip", level=50, device=dev)
    icons.set_icon_style("none")
    assert w.TaskbarWidget._custom_file(pill, 16) is None
    name = make_icon("red")
    ci.set_maps({}, {KEY: name})
    assert w.TaskbarWidget._custom_file(pill, 16) == name
    (ci.icons_dir() / name).unlink()
    ci.clear_cache()
    assert w.TaskbarWidget._custom_file(pill, 16) is None
    assert w.TaskbarWidget._custom_file(w._Pill("bluetooth", "x", "t"), 16) is None


def _tray(tmp_path, monkeypatch):
    i18n.set_language("en")
    cfg = Config(tmp_path / "c.json")
    t = TrayController(cfg)
    t.set_devices([Device(id="dev-1", name="Mouse", kind="mouse", battery=50, connected=True)])
    return cfg, t


def test_menu_entry_calls_dialog(tmp_path, monkeypatch):
    cfg, t = _tray(tmp_path, monkeypatch)
    act = t._settings.find("custom_icons")
    assert act.label() == "Custom icons…"
    seen = []
    monkeypatch.setattr(cid.CustomIconsDialog, "exec", lambda self: seen.append(self) or QDialog.DialogCode.Rejected)
    act.run()
    assert len(seen) == 1


def _patch_dialog(monkeypatch, result, accept):
    def fake_exec(self):
        if accept:
            self._by_kind.update(result[0])
            self._by_device.update(result[1])
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected
    monkeypatch.setattr(cid.CustomIconsDialog, "exec", fake_exec)


def test_accept_saves_applies_refreshes_and_cleans(tmp_path, monkeypatch):
    cfg, t = _tray(tmp_path, monkeypatch)
    keep = make_icon("red")
    orphan = make_icon("blue")
    _patch_dialog(monkeypatch, ({"mouse": keep}, {KEY: keep}), True)
    ui = []
    t.ui_settings_changed.connect(lambda: ui.append(1))
    t.open_custom_icons()
    assert cfg.get("custom_icons_kind") == {"mouse": keep}
    assert cfg.get("custom_icons_device") == {KEY: keep}
    assert ci.file_for("mouse", KEY2) == keep
    assert ui
    assert (ci.icons_dir() / keep).exists() and not (ci.icons_dir() / orphan).exists()


def test_dialog_receives_devices_with_keys(tmp_path, monkeypatch):
    cfg, t = _tray(tmp_path, monkeypatch)
    got = {}
    orig = cid.CustomIconsDialog.__init__

    def spy(self, by_kind, by_device, devices, tr=lambda c: c, parent=None):
        got["devices"] = list(devices)
        orig(self, by_kind, by_device, devices, tr, parent)
    monkeypatch.setattr(cid.CustomIconsDialog, "__init__", spy)
    monkeypatch.setattr(cid.CustomIconsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    t.open_custom_icons()
    assert got["devices"] == [(KEY, "Mouse", "mouse")]


def test_cancel_cleans_orphans_without_touching_config(tmp_path, monkeypatch):
    cfg, t = _tray(tmp_path, monkeypatch)
    kept = make_icon("red")
    orphan = make_icon("blue")
    cfg.set("custom_icons_kind", {"mouse": kept})
    _patch_dialog(monkeypatch, ({"keyboard": orphan}, {}), False)
    ui = []
    t.ui_settings_changed.connect(lambda: ui.append(1))
    t.open_custom_icons()
    assert cfg.get("custom_icons_kind") == {"mouse": kept}
    assert cfg.get("custom_icons_device") == {}
    assert not ui
    assert (ci.icons_dir() / kept).exists() and not (ci.icons_dir() / orphan).exists()
