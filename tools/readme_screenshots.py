"""Regenerate the README screenshots (docs/images/*.png and *.gif).

    .venv\\Scripts\\python.exe tools\\readme_screenshots.py [output_dir]

The real widgets (taskbar pills, device panel, Settings pages, statistics window) are built with fake
devices and rendered with QWidget.grab(); nothing is shown on screen. The app's own settings are never
touched: %APPDATA% / %LOCALAPPDATA% point at a throw-away folder and no hardware is read. GIFs are encoded
by the small pure-Python writer below (no Pillow needed). Dev tool only: it is not part of the app.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "images"

# --- isolation: must happen before the package is imported ---------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="tbw-shots-"))
os.environ["APPDATA"] = str(_TMP / "Roaming")
os.environ["LOCALAPPDATA"] = str(_TMP / "Local")
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"       # 1 logical px = 1 device px, whatever the monitor
os.environ.pop("QT_SCALE_FACTOR", None)
SCALE = 2                                           # every image is rendered at 2x for crisp text
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication.instance() or QApplication([])

from taskbar_battery_widget import (animation, colors, custom_icons, device_panel as dp, i18n, icons,  # noqa: E402
                                    taskbar, widget as widget_mod)
from taskbar_battery_widget.config import Config  # noqa: E402
from taskbar_battery_widget.models import Device, visibility_key  # noqa: E402
from taskbar_battery_widget.panel_model import build_panel_model  # noqa: E402
from taskbar_battery_widget.settings_model import SettingsCallbacks, build_settings_model  # noqa: E402

# the real taskbar / hardware must not influence anything
taskbar.get_taskbar_info = lambda: None
taskbar.get_taskbar_infos = lambda: []
taskbar.monitor_devices = lambda: []
taskbar.monitor_stable_id = lambda name: ""
taskbar.is_fullscreen_foreground = lambda *a, **k: False
animation.system_animations_enabled = lambda: True

i18n.set_language("en")
custom_icons.icons_dir = lambda: _TMP / "icons"
(_TMP / "icons").mkdir(parents=True, exist_ok=True)
ASSETS = ROOT / "tools" / "readme_assets"       # the author's own custom icons (static + animated strip)
STATIC_ICON = "4db4f58583c4663b.png"
ANIM_ICON = "55adb6979d8c8e96.anim.png"
for _f in (STATIC_ICON, ANIM_ICON, "55adb6979d8c8e96.anim.json"):
    shutil.copyfile(ASSETS / _f, _TMP / "icons" / _f)

# ------------------------------------------------------------------------------------------------ helpers
TASKBAR_BG = {False: QColor(32, 32, 32), True: QColor(243, 243, 243)}     # keyed by `light`
_cfg_n = 0


def make_cfg(**kw) -> Config:
    global _cfg_n
    _cfg_n += 1
    cfg = Config(_TMP / f"config{_cfg_n}.json")
    cfg.set("animations", "always")
    for k, v in kw.items():
        cfg.set(k, v)
    return cfg


def apply_cfg(cfg: Config) -> None:
    colors.set_palette("mono" if cfg.get("color_mode") == "mono" else cfg.get("color_palette"),
                       cfg.get("custom_colors"))
    icons.set_icon_style(cfg.get("icon_style"))
    custom_icons.set_maps(cfg.get("custom_icons_kind"), cfg.get("custom_icons_device"))
    animation.clock().set_mode("always")


def dev(i, name, kind, battery, charging=False, connected=True, link="bluetooth", idle=False, stale=False):
    return Device(id=i, name=name, kind=kind, battery=battery, connected=connected, charging=charging,
                  link=link, idle=idle, stale=stale)


DEVICES = [
    dev("mouse", "Logitech MX Master 3S", "mouse", 82, link="2.4ghz"),
    dev("headset", "SteelSeries Arctis Nova 7", "headset", 56, charging=True, link="2.4ghz"),
    dev("keyboard", "Keychron K3", "keyboard", 8),
    dev("pad", "Xbox Wireless Controller", "gamepad", 64),
]
PANEL_DEVICES = DEVICES + [
    dev("phone", "Pixel 8", "phone", 71),
    dev("pen", "Surface Pen", "pen", 35, idle=True),
    dev("speaker", "JBL Flip 6", "speaker", 40, connected=False, stale=True),
    dev("watch", "Galaxy Watch", "watch", None, connected=False),
]


class _Clock:
    """Fixed animation time for the pills: drives the widget's own painting code."""
    t = 0


def make_pills(devs, cfg, light: bool, now: int | None = None) -> widget_mod.TaskbarWidget:
    apply_cfg(cfg)
    w = widget_mod.TaskbarWidget(cfg)
    w._light = light
    w._flash_left = 0
    w.set_devices(devs)
    w._devices_seen = True
    w._flash_left = 0
    w._flash_keys = set()
    if now is not None:
        w._anim_live = lambda: True
        w._clock.now_ms = lambda: _Clock.t
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    return w


def grab(w) -> QImage:
    """The widget as it is painted (transparent background kept), at SCALE x."""
    w.ensurePolished()
    size = w.size()
    img = QImage(size * SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(Qt.GlobalColor.transparent)
    w.render(img)
    return img


def new_canvas(w: int, h: int, color: QColor) -> QImage:
    img = QImage(w * SCALE, h * SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(color)
    return img


def label(p: QPainter, x, y, text, light: bool, size=9.5, bold=False, align=Qt.AlignmentFlag.AlignLeft, w=400):
    f = QFont(app.font())
    f.setPointSizeF(size)
    f.setBold(bold)
    p.setFont(f)
    p.setPen(QColor(30, 30, 30) if light else QColor(225, 225, 225))
    p.drawText(QRectF(x, y, w, 20), int(align | Qt.AlignmentFlag.AlignVCenter), text)


def rounded_clip(img: QImage, radius: float) -> QImage:
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(img.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, img.width() / img.devicePixelRatio(), img.height() / img.devicePixelRatio()),
                        radius, radius)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()
    return out


def save_png(img: QImage, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    assert img.save(str(path), "PNG"), path
    print(f"  {path.name}: {path.stat().st_size // 1024} KB, {img.width()}x{img.height()}")
    return path


def draw_on_taskbar(p: QPainter, pills_img: QImage, x, y, bar_w, bar_h, light: bool):
    """A slice of a Windows 11 taskbar with the pills at its right end."""
    bar = QRectF(x, y, bar_w, bar_h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(TASKBAR_BG[light])
    p.drawRoundedRect(bar, 8, 8)
    pw = pills_img.width() / SCALE
    ph = pills_img.height() / SCALE
    p.drawImage(QPointF(x + bar_w - pw - 12, y + (bar_h - ph) / 2), pills_img)


# ------------------------------------------------------------------------------------------------ GIF writer
def _lzw(indices: bytes, min_bits: int) -> bytes:
    clear, end = 1 << min_bits, (1 << min_bits) + 1
    out = bytearray()
    acc = nacc = 0

    def emit(code, width):
        nonlocal acc, nacc
        acc |= code << nacc
        nacc += width
        while nacc >= 8:
            out.append(acc & 255)
            acc >>= 8
            nacc -= 8

    table = {bytes([i]): i for i in range(clear)}
    nxt, width = end + 1, min_bits + 1
    emit(clear, width)
    cur = b""
    for b in indices:
        cand = cur + bytes([b])
        if cand in table:
            cur = cand
            continue
        emit(table[cur], width)
        table[cand] = nxt
        nxt += 1
        if nxt > (1 << width) and width < 12:
            width += 1
        if nxt >= 4095:
            emit(clear, width)
            table = {bytes([i]): i for i in range(clear)}
            nxt, width = end + 1, min_bits + 1
        cur = bytes([b])
    emit(table[cur], width)
    emit(end, width)
    if nacc:
        out.append(acc & 255)
    return bytes(out)


def _blocks(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def _median_cut(counts: dict, n: int) -> list:
    """Adaptive palette: weighted median cut over the (r, g, b) -> pixel-count table."""
    boxes = [list(counts.items())]
    while len(boxes) < n:
        best, bi, bch = -1, -1, 0
        for k, box in enumerate(boxes):
            if len(box) < 2:
                continue
            for ch in range(3):
                vals = [c[ch] for c, _ in box]
                rng = max(vals) - min(vals)
                if rng > best:
                    best, bi, bch = rng, k, ch
        if bi < 0:
            break
        box = sorted(boxes.pop(bi), key=lambda it: it[0][bch])
        half, acc, cut = sum(c for _, c in box) / 2, 0, 1
        for m, (_, c) in enumerate(box):
            acc += c
            if acc >= half:
                cut = min(max(m + 1, 1), len(box) - 1)
                break
        boxes += [box[:cut], box[cut:]]
    pal = []
    for box in boxes:
        tot = sum(c for _, c in box)
        pal.append(tuple(round(sum(col[ch] * c for col, c in box) / tot) for ch in range(3)))
    return pal


def write_gif(frames: list[QImage], delay_ms: int, path: Path) -> None:
    """Looping GIF with one adaptive 256-colour palette for the whole animation (median cut, no dithering)."""
    from collections import Counter
    frames = [f.convertToFormat(QImage.Format.Format_RGB888) for f in frames]
    w, h = frames[0].width(), frames[0].height()
    chans = []
    counts: Counter = Counter()
    for fr in frames:
        raw = bytes(fr.constBits())
        stride = fr.bytesPerLine()
        rows = b"".join(raw[y * stride: y * stride + w * 3] for y in range(h))
        trip = list(zip(rows[0::3], rows[1::3], rows[2::3]))
        chans.append(trip)
        counts.update(trip)
    pal = list(counts) if len(counts) <= 256 else _median_cut(counts, 256)
    lut = {}
    for col in counts:
        r, g, b = col
        lut[col] = min(range(len(pal)), key=lambda i: (pal[i][0] - r) ** 2 + (pal[i][1] - g) ** 2
                       + (pal[i][2] - b) ** 2)
    pal = pal + [(0, 0, 0)] * (256 - len(pal))
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", w, h, 0xF7, 0, 0)
    for r, g, b in pal:
        out += bytes((r, g, b))
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    for trip in chans:
        idx = bytes(map(lut.__getitem__, trip))
        out += b"\x21\xf9\x04" + struct.pack("<BHB", 0x04, max(2, round(delay_ms / 10)), 0) + b"\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0)
        out += b"\x08" + _blocks(_lzw(idx, 8))
    out += b"\x3b"
    path.write_bytes(bytes(out))
    print(f"  {path.name}: {path.stat().st_size // 1024} KB, {w}x{h}, {len(frames)} frames")


# ------------------------------------------------------------------------------------------------ images
KEY = visibility_key
CUSTOM = {"custom_icons_device": {KEY("mouse"): STATIC_ICON, KEY("headset"): ANIM_ICON}}
STYLE_DEVICES = [
    dev("s1", "Mouse", "mouse", 88),
    dev("s2", "Headset", "headset", 62, charging=True),
    dev("s3", "Keyboard", "keyboard", 34),
    dev("s4", "Controller", "gamepad", 15),
    dev("s5", "Speaker", "speaker", 72),
]
PALETTE_DEVICES = [
    dev("p1", "Mouse", "mouse", 92),
    dev("p2", "Headset", "headset", 71),
    dev("p3", "Keyboard", "keyboard", 45),
    dev("p4", "Controller", "gamepad", 27),
    dev("p5", "Pen", "pen", 8),
]
PURPLE = {"high": "#B388FF", "medium": "#FF80AB", "low": "#FFD54F"}


def pills_image(devs, light, **cfg_kw) -> QImage:
    cfg = make_cfg(**cfg_kw)
    w = make_pills(devs, cfg, light)
    img = grab(w)
    w.deleteLater()
    return img


def pills_strip(devs, light, bar_w=None, bar_h=44, **cfg_kw) -> QImage:
    img = pills_image(devs, light, **cfg_kw)
    pw = img.width() / SCALE
    bw = bar_w or int(pw + 24)
    can = new_canvas(bw, bar_h, Qt.GlobalColor.transparent)
    p = QPainter(can)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_on_taskbar(p, img, 0, 0, bw, bar_h, light)
    p.end()
    return can


def img_pills_themes():
    h = 44
    strips = [pills_strip(DEVICES, light) for light in (False, True)]
    W = int(strips[0].width() / SCALE) + 24
    can = new_canvas(W, 2 * (h + 32) + 8, QColor(190, 198, 212))
    p = QPainter(can)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    y = 8
    for light, strip in zip((False, True), strips):
        label(p, 12, y, "Light taskbar" if light else "Dark taskbar", True, 9, True)
        y += 22
        p.drawImage(QPointF(12, y), strip)
        y += h + 10
    p.end()
    return can


def make_panel(devs, dark=True, hidden=(), size="normal", avail_h=1000):
    p = dp.DevicePanel(dark=dark)
    p.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    p._screen_avail = QRect(0, 0, 1920, avail_h)
    p.set_metrics(size, 100)
    p._fit(p._screen_avail)
    p.set_model(build_panel_model(devs, set(hidden), True, 20, dark, True, "en"))
    return p


def fit_content(p) -> None:
    """Shrink the panel to its content (list or Settings page) plus the toolbar: no empty area."""
    if p.in_settings:
        v = p.settings_view
        body = max(r.mapTo(v, r.rect().bottomLeft()).y() for r in v.rows().values()) + 10
    else:
        c = p._content
        body = max(w.geometry().bottom() for w in c.children() if isinstance(w, QWidget)) + 10
    p.setFixedSize(p.width(), body + p._toolbar_widget.sizeHint().height() + 22)


def panel_image(p) -> QImage:
    p.show()
    for _ in range(5):
        app.processEvents()
    fit_content(p)
    for _ in range(5):
        app.processEvents()
    return rounded_clip(grab(p), 8)


class _FakeCfg:
    def __init__(self, palette="classic", icon="outline"):
        self.d = {"wireless_enabled": True, "bluetooth_enabled": True, "notifications_enabled": True,
                  "full_charge_alert": True, "detail_log_enabled": False, "start_with_windows": False,
                  "show_in_fullscreen": False, "low_threshold": 20, "color_palette": palette,
                  "color_mode": "levels", "icon_style": icon, "placement_mode": "taskbar",
                  "taskbar_anchor": "right", "placement_monitor": "primary", "floating_positions": {},
                  "panel_size": "normal", "ui_scale": 100, "language": "en", "animations": "system",
                  "custom_colors": dict(colors.CUSTOM_DEFAULTS), "fullscreen_placement": "same",
                  "beta_readers": False, "update_check_auto": False, "notification_cooldown_min": 60,
                  "idle_grace_min": 30, "show_disconnected": True, "show_when_empty": False,
                  "dualsense_bt_extended": True, "custom_icons_kind": {}, "custom_icons_device": {}}

    def get(self, k):
        return self.d.get(k, False)


def settings_model(cfg: _FakeCfg):
    def set_bool(k, v):
        cfg.d[k] = bool(v)

    def set_palette(v):
        cfg.d["color_palette"] = v
        colors.set_palette(v)

    def set_icon_style(v):
        cfg.d["icon_style"] = v
        icons.set_icon_style(v)

    def noop(*a, **k):
        return None
    cb = SettingsCallbacks(set_bool=set_bool, set_threshold=lambda v: cfg.d.update(low_threshold=v),
                           set_palette=set_palette, set_startup=noop, open_device_log=noop,
                           open_logs_folder=noop, startup_enabled=lambda: False, open_detail_folder=noop,
                           set_icon_style=set_icon_style, reset_position=noop, open_custom_icons=noop)
    return build_settings_model(cfg, cb)


def reset_look():
    colors.set_palette("classic")
    icons.set_icon_style("outline")
    custom_icons.set_maps({}, {})


def img_panel():
    reset_look()
    apply_cfg(make_cfg(**CUSTOM))
    return panel_image(make_panel(PANEL_DEVICES, hidden={KEY("pen")}, size="large"))


def img_settings(open_choice=None, page="personalization"):
    reset_look()
    p = make_panel(PANEL_DEVICES[:2], size="large")
    p.set_settings_model(settings_model(_FakeCfg()))
    p.show()
    p.show_settings(page)
    app.processEvents()
    if open_choice:
        p.settings_view.row(open_choice).click()
        app.processEvents()
    return panel_image(p)


def img_grid(devs, cfg_key, values, extra=None):
    """One row per value, a dark and a light taskbar side by side (bars as wide as their pills)."""
    h = 44
    lab_w = 118
    pad = 14
    kws = []
    for v, _t in values:
        kw = {cfg_key: v}
        if extra:
            kw.update(extra(v))
        kws.append(kw)
    cells = [[pills_strip(devs, light, bar_h=h, **kw) for light in (False, True)] for kw in kws]
    bar_w = max(c.width() for row in cells for c in row) / SCALE
    w = int(pad * 2 + lab_w + 2 * bar_w + 12)
    top = 36
    can = new_canvas(w, top + len(values) * (h + 10) + pad, QColor(190, 198, 212))
    p = QPainter(can)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    label(p, pad + lab_w, 8, "Dark taskbar", True, 9, True, w=bar_w)
    label(p, pad + lab_w + bar_w + 12, 8, "Light taskbar", True, 9, True, w=bar_w)
    y = top
    for (v, text), row in zip(values, cells):
        label(p, pad, y + 12, text, True, 9.5, True, w=lab_w)
        for j, strip in enumerate(row):
            p.drawImage(QPointF(pad + lab_w + j * (bar_w + 12) + bar_w - strip.width() / SCALE, y), strip)
        y += h + 10
    p.end()
    return can


def img_palettes():
    vals = [("classic", "Classic"), ("colorblind", "Color-blind safe"), ("pastel", "Pastel"),
            ("high_contrast", "High contrast"), ("mono", "Monochrome"), ("custom", "Custom")]

    def extra(v):
        out = {"custom_colors": PURPLE} if v == "custom" else {}
        if v == "mono":
            out["color_mode"] = "mono"
        return out
    return img_grid(PALETTE_DEVICES, "color_palette", vals, extra)


def img_styles():
    vals = [("outline", "Outline"), ("filled", "Filled"), ("simple", "Simple"), ("retro", "Retro pixel art"),
            ("battery", "Battery"), ("none", "Text only")]
    return img_grid(STYLE_DEVICES, "icon_style", vals)


def img_custom_icons():
    reset_look()
    apply_cfg(make_cfg(**CUSTOM))
    panel = panel_image(make_panel(PANEL_DEVICES[:4], size="compact"))
    pw, ph = panel.width() / SCALE, panel.height() / SCALE
    strips = [pills_strip(DEVICES, light, **CUSTOM) for light in (False, True)]
    apply_cfg(make_cfg(**CUSTOM))
    sw = strips[0].width() / SCALE
    can = new_canvas(int(sw + 28), int(12 + 2 * 56 + ph + 16), QColor(190, 198, 212))
    q = QPainter(can)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    y = 12
    for s in strips:
        q.drawImage(QPointF(14, y), s)
        y += 56
    q.drawImage(QPointF(14 + (sw - pw) / 2, y), panel)
    q.end()
    return can


def csv_for_stats(path: Path):
    """24 days, one row every 2 minutes: ~66 h of discharge (100 -> 20 %), then ~1.5 h of charging."""
    import datetime as dt
    import math
    hdr = "time_iso,battery,charging,connected,link,idle,stale"
    step = 120
    cycle = 66 * 3600 + 90 * 60
    end = time.time()
    t = end - 24 * 86400
    t0 = t
    rows = []
    while t < end:
        pos = (t - t0) % cycle
        if pos < 66 * 3600:
            lvl = 100 - 80 * pos / (66 * 3600) + 1.2 * math.sin(pos / 5400)
            chg = 0
        else:
            lvl = 20 + 80 * (pos - 66 * 3600) / (90 * 60)
            chg = 1
        iso = dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
        rows.append(f"{iso},{max(1, min(100, round(lvl)))},{chg},1,2.4ghz,0,0")
        t += step
    path.write_text("\n".join([hdr] + rows) + "\n", encoding="utf-8")


def img_stats():
    from taskbar_battery_widget.stats_window import StatsWindow
    reset_look()
    csv = _TMP / "stats.csv"
    csv_for_stats(csv)
    win = StatsWindow("Logitech MX Master 3S", csv)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    end = time.time() + 10
    while win._stats is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    img = grab(win)
    win.shutdown()
    return img


def img_hero():
    from PySide6.QtGui import QLinearGradient
    reset_look()
    apply_cfg(make_cfg(**CUSTOM))
    panel = panel_image(make_panel(PANEL_DEVICES, hidden={KEY("pen")}, size="large"))
    pills = pills_image(DEVICES, False, **CUSTOM)
    pw, ph = panel.width() / SCALE, panel.height() / SCALE
    bar_h = 48
    W = int(pw + 150)
    H = int(ph + 26 + bar_h)
    can = new_canvas(W, H, QColor(0, 0, 0))
    p = QPainter(can)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor(30, 58, 110))
    g.setColorAt(0.55, QColor(88, 62, 140))
    g.setColorAt(1, QColor(28, 36, 70))
    p.fillRect(QRectF(0, 0, W, H), g)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(32, 32, 32, 240))
    p.drawRect(QRectF(0, H - bar_h, W, bar_h))
    p.drawImage(QPointF(W - pills.width() / SCALE - 14, H - bar_h + (bar_h - pills.height() / SCALE) / 2), pills)
    p.drawImage(QPointF(W - pw - 24, 16), panel)
    p.end()
    return can


# ------------------------------------------------------------------------------------------------ GIFs
def pulse_frames(devs, ms_total, step, cfg_kw, bar_w, light=False):
    cfg = make_cfg(**cfg_kw)
    w = make_pills(devs, cfg, light, now=0)
    frames = []
    for t in range(0, ms_total, step):
        _Clock.t = t
        pm = grab(w)
        bar_w = int(pm.width() / SCALE) + 24
        can = new_canvas(bar_w, 44, TASKBAR_BG[light])
        q = QPainter(can)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_on_taskbar(q, pm, 0, 0, bar_w, 44, light)
        q.end()
        frames.append(can)
    w.deleteLater()
    return frames


def gif_pulses():
    reset_look()
    charging = [dev("a", "Mouse", "mouse", 82), dev("b", "Headset", "headset", 56, charging=True),
                dev("c", "Controller", "gamepad", 64)]
    critical = [dev("a", "Mouse", "mouse", 82), dev("c", "Keyboard", "keyboard", 8),
                dev("d", "Controller", "gamepad", 64)]
    # each pulse repeats on its own period: 2 s for the charging bolt, 1.5 s for the critical level
    write_gif(pulse_frames(charging, animation.CHARGE_PERIOD_MS, 100, {}, 230), 100, OUT / "pulse-charging.gif")
    write_gif(pulse_frames(critical, animation.ALERT_PERIOD_MS, 100, {}, 230), 100, OUT / "pulse-critical.gif")


def gif_custom():
    reset_look()
    devs = [dev("mouse", "Mouse", "mouse", 82), dev("headset", "Headset", "headset", 56),
            dev("keyboard", "Keyboard", "keyboard", 41)]
    write_gif(pulse_frames(devs, 4800, 100, CUSTOM, 226), 100, OUT / "animated-custom-icon.gif")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[2:])
    steps = [("hero.png", img_hero), ("taskbar-pills.png", img_pills_themes), ("device-panel.png", img_panel),
             ("palettes.png", img_palettes), ("icon-styles.png", img_styles),
             ("custom-icons.png", img_custom_icons), ("settings-personalization.png", img_settings),
             ("settings-palette.png", lambda: img_settings("palette")), ("statistics.png", img_stats)]
    print("PNG:")
    for name, fn in steps:
        if not only or name in only:
            save_png(fn(), name)
    print("GIF:")
    if not only or ("pulse-charging.gif" in only or "pulse-critical.gif" in only):
        gif_pulses()
    if not only or "animated-custom-icon.gif" in only:
        gif_custom()


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.stdout.flush()
    os._exit(0)
