"""Review fixes: stored icon names always valid, detail log delete drops queued rows, panel animates visible rows."""
import pytest
from PySide6.QtGui import QColor, QImage

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget.detail_log import DetailLogger
from taskbar_battery_widget.models import Device, visibility_key

from tests._anim_data import make_gif


@pytest.fixture
def icons(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    yield tmp_path
    ci.clear_cache()
    ci.set_maps({}, {})


def _png():
    img = QImage(16, 16, QImage.Format.Format_ARGB32)
    img.fill(QColor("red"))
    return ci._png_bytes(img)


@pytest.mark.parametrize("fname", ["x.webp", "x.gif", "x.jpg", "x.jpeg", "x.bmp", "x.ico", "x.png"])
def test_png_with_wrong_extension_gets_valid_name(icons, fname):
    f = icons / fname
    f.write_bytes(_png())
    try:
        name = ci.import_image(f)
    except ci.IconImportError as e:
        assert e.code == ci.ERR_UNREADABLE
        return
    assert ci.safe_name(name) and name.endswith(".png")
    assert ci.usable(name)


def test_gif_named_webp_gets_valid_name(icons):
    f = icons / "g.webp"
    f.write_bytes(make_gif(16, 16, [100, 100]))
    try:
        name = ci.import_image(f)
    except ci.IconImportError as e:
        assert e.code == ci.ERR_UNREADABLE
        return
    assert ci.safe_name(name)


def test_anim_budget_lowered():
    assert ci.MAX_ANIM_PIXELS == 60_000_000


def test_import_busy_restores_cursor(icons):
    from PySide6.QtWidgets import QApplication
    f = icons / "x.png"
    f.write_bytes(_png())
    assert ci.safe_name(ci.import_image_busy(f))
    assert QApplication.overrideCursor() is None
    with pytest.raises(ci.IconImportError):
        ci.import_image_busy(icons / "missing.png")
    assert QApplication.overrideCursor() is None


def test_delete_for_key_drops_pending_rows(tmp_path):
    mac = "BTHENUM\\DEV_AABBCCDDEEFF AA:BB:CC:DD:EE:FF"
    lg = DetailLogger(tmp_path / "logs" / "devices")
    p1 = lg.path_for(Device(id=mac, name="Mouse", kind="mouse", battery=80, connected=True))
    p2 = lg.path_for(Device(id="USB\\VID_1 other", name="Other", kind="mouse", battery=80, connected=True))
    for item in ((p1, "r1"), (p2, "r2"), (p1, "r3")):
        lg._q.put_nowait(item)
    lg.delete_for_key(visibility_key(mac))
    left = []
    while not lg._q.empty():
        left.append(lg._q.get_nowait())
    assert left == [(p2, "r2")]


def test_only_rows_in_the_viewport_animate(icons, qapp):
    from taskbar_battery_widget.device_panel import DevicePanel
    from taskbar_battery_widget.panel_model import build_panel_model
    src = icons / "a.gif"
    src.write_bytes(make_gif(16, 16, [100, 200, 300]))
    ci.set_maps({"keyboard": ci.import_image(src)}, {})
    p = DevicePanel(dark=True)
    try:
        devs = [Device(str(i), f"KB{i}", "keyboard", 80, True) for i in range(40)]
        p.set_model(build_panel_model(devs, set(), False, 20, True, True))
        p.resize(p.width(), 300)
        p.show()
        qapp.processEvents()
        first = {k for k, _ in p._animated_rows()}
        assert 0 < len(first) < 40
        bar = p._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        qapp.processEvents()
        last = {k for k, _ in p._animated_rows()}
        assert last and last != first and len(last) < 40
    finally:
        p.hide()
        p.deleteLater()
