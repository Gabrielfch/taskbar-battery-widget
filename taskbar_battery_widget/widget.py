"""Frameless, always-on-top, non-activating pill bar docked to the Windows taskbar (or floating on a monitor)."""
from __future__ import annotations

import html
import math
import sys  # noqa: F401  (tests read widget.sys.platform)
import time

import shiboken6
from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAccessible, QAccessibleEvent, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QMenu, QWidget

from . import animation
from . import custom_icons
from . import taskbar
from .colors import (TASKBAR_BASE, band_color, battery_color, ensure_contrast, pill_tint, row_style, separator_color,
                     taskbar_base)
from .config import FLOATING_ANCHORS, PILL_STYLES, UI_SCALES
from .i18n import kind_name as _kind_name, tr
from .icons import draw_bolt, draw_kind_icon, get_icon_style
from .models import Device, visibility_key
from .naming import sanitize_name
from .remaining import remaining_text
from .popup import HOVER_DELAY_MS, HoverPopup, PopupContent, build_popup_content, place_popup, stale_text
from .placement import (FLOATING_SPACE_SHARE, FREE_SPACE_SHARE, MAX_PILL_H, MIN_LIMIT, MIN_PILL_H, REASON_LEFT_NEEDS_CENTERED,  # noqa: F401
                        REASON_NO_TASKBAR_FLOATING, Env,
                        Placement, ScreenInfo, center_rect_y, choose_placement, compute_placement, lookup_floating,
                        pill_height_for, resolve_screen, snap_anchor, taskbar_for_screen)
from .win_window import (_ZGuard, _apply_ex_styles, _assert_topmost, _foreground, _post_null,  # noqa: F401
                         popup_menu_foreground, set_click_through, shift_down)

MARGIN = 2           # outer margin around the pills (logical px)
SPACING = 4          # gap between pills
PAD = 8              # horizontal padding inside a pill
ICON_GAP = 5         # gap between icon and text
# per pill style: (horizontal padding inside a pill, gap between pills), logical px at 100 %
STYLE_PAD = {"classic": PAD, "soft": PAD, "transparent": 6, "compact": 3}
STYLE_SPACING = {"classic": SPACING, "soft": SPACING, "transparent": SPACING, "compact": 2}
SOFT_RADIUS = 4          # corner radius of the "soft" style (logical px at 100 %)
SOFT_ALPHA_SHARE = 0.55  # the "soft" background is this share as strong as the classic one
SEPARATOR_SHARE = 0.5    # height of the divider between transparent pills / pill height
MIN_FONT_PX = 12
BAR_MIN_PILL_H = 24   # below this height the level bar is dropped
BAR_H = 3.0
MOON_W = 7
TICK_MS = 500
SHIFT_POLL_MS = 100     # Shift polling while shown over a fullscreen app
DRAG_THRESHOLD = 4        # px of movement before a press in floating mode becomes a drag
FLOATING_BG_ALPHA = 245   # backdrop of the floating bar (slightly translucent)
FLOATING_BORDER_ALPHA = 60
REG_CACHE_TTL = 5.0       # s: Explorer\Advanced values (icon alignment, Widgets button) change rarely


def screen_keys() -> list:
    """[(monitor_key, QScreen)] for every Qt screen, in QGuiApplication.screens() order.

    Keys come from taskbar.monitor_key(); duplicates (identical monitors) get "#n" suffixes assigned in
    a name-sorted order so they do not swap when Qt reorders the list.
    """
    screens = [sc for sc in QGuiApplication.screens() if shiboken6.isValid(sc)]
    devs = _device_names(screens)
    taken: list[str] = []
    keys: dict[int, str] = {}
    for idx in sorted(range(len(screens)), key=lambda i: devs[i] or screens[i].name()):
        sc = screens[idx]
        mf, md, sn = sc.manufacturer(), sc.model(), sc.serialNumber()
        # Qt gave nothing to identify the panel: use the Windows interface path (hashed) rather than DISPLAYn
        sid = "" if (mf or md or sn) else taskbar.monitor_stable_id(devs[idx])
        k = taskbar.monitor_key(mf, md, sn, sc.name(), taken, sid)
        taken.append(k)
        keys[idx] = k
    return [(keys[i], sc) for i, sc in enumerate(screens)]


def _device_names(screens: list) -> list:
    """Windows device name (``\\\\.\\DISPLAYn``) of each QScreen, "" when unknown.

    QScreen.name() is a friendly name on recent Qt versions, so the screen is matched to its monitor by
    its native top-left corner (Qt does not scale screen positions); ties are broken by size.
    """
    devs = taskbar.monitor_devices()
    out = []
    for sc in screens:
        if not shiboken6.isValid(sc):       # destroyed meanwhile (unplug, RDP, headless CI)
            out.append("")
            continue
        g, dpr = sc.geometry(), sc.devicePixelRatio() or 1.0
        cands = [d for d in devs if d[1][:2] == (g.x(), g.y())]
        if len(cands) > 1:
            size = (round(g.width() * dpr), round(g.height() * dpr))
            cands = [d for d in cands if (d[1][2] - d[1][0], d[1][3] - d[1][1]) == size] or cands
        out.append(cands[0][0] if cands else "")
    return out


def _rect_t(r: QRect) -> tuple:
    return (r.x(), r.y(), r.width(), r.height())


def screen_infos() -> list:
    """[(ScreenInfo, QScreen)] with logical geometry, primary flagged; name = Windows device name."""
    for _attempt in range(2):           # a screen can be destroyed while we read it: retry once
        try:
            primary = QGuiApplication.primaryScreen()
            pairs = [(k, sc) for k, sc in screen_keys() if shiboken6.isValid(sc)]
            devs = _device_names([sc for _, sc in pairs])
            return [(ScreenInfo(k, devs[i] or sc.name(), _rect_t(sc.geometry()),
                                _rect_t(sc.availableGeometry()), sc.devicePixelRatio() or 1.0,
                                sc == primary), sc)
                    for i, (k, sc) in enumerate(pairs)]
        except RuntimeError:
            continue
    return []


# --------------------------------------------------------------------------- pure layout
def pill_width(text_w: int, icon: int, extra: int = 0, pad: int = PAD, gap: int = ICON_GAP) -> int:
    return pad + icon + gap + text_w + extra + pad


def layout_pills(widths: list[int], pill_h: int, vertical: bool = False,
                 spacing: int = SPACING, margin: int = MARGIN):
    """Return ([(x, y, w, h), ...], (total_w, total_h)) for pills in a row (or a column)."""
    if not widths:
        return [], (0, 0)
    rects = []
    if vertical:
        w = max(widths)
        y = margin
        for _ in widths:
            rects.append((margin, y, w, pill_h))
            y += pill_h + spacing
        return rects, (w + 2 * margin, y - spacing + margin)
    x = margin
    for w in widths:
        rects.append((x, margin, w, pill_h))
        x += w + spacing
    return rects, (x - spacing + margin, pill_h + 2 * margin)


def fit_count(extents: list[int], limit: int | None, overflow_extent: int,
              spacing: int = SPACING, margin: int = MARGIN) -> int:
    """How many leading pills fit in `limit` px along the main axis (the rest go to a "+N" pill).

    Returns len(extents) when everything fits (or limit is None); otherwise at least 1.
    """
    n = len(extents)
    if limit is None or n <= 1:
        return n

    def total(k: int, extra: bool) -> int:
        parts = extents[:k] + ([overflow_extent] if extra else [])
        return 2 * margin + sum(parts) + spacing * max(0, len(parts) - 1)

    if total(n, False) <= limit:
        return n
    for k in range(n - 1, 0, -1):
        if total(k, True) <= limit:
            return k
    return 1


def pill_index_at(rects: list[tuple], x: float, y: float) -> int:
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return -1


def tooltip_html(text: str) -> str:
    """Plain tooltip text -> escaped rich text, so device-supplied strings are never parsed as markup."""
    body = html.escape(text, quote=True).replace(chr(10), "<br>")
    return f"<p style='white-space:pre'>{body}</p>"





IDLE_ALPHA = 153     # ~60 % opacity of the icon and bar of sleeping devices (never of the text)
DIM_ALPHA = 170      # icon of a disconnected device
FLASH_MS, FLASH_TICKS = 300, 6


@dataclass
class _Pill:
    kind: str
    text: str
    tooltip: str
    low: bool = False
    charging: bool = False
    dim: bool = False
    idle: bool = False
    level: int | None = None
    device: Device | None = None
    warn: bool = False          # orange/red band: "!" glyph before the text
    alert: bool = False         # at/below the low threshold (not charging): border + red tint
    a11y: str = ""              # spoken summary of the pill
    members: list | None = None # for the "+N" overflow pill: the pills it stands for


class TaskbarWidget(QWidget):
    context_menu_requested = Signal(QPoint)
    panel_requested = Signal(QRect)         # the "+N" pill was activated (global pill rect)
    placement_warning = Signal(str)         # e.g. "left_needs_centered_icons": the left anchor fell back

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        # keyboard access: the pills are reachable once the widget has focus (it never takes it on click)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._kfocus = -1               # pill with the keyboard focus ring, -1 = none
        self._cfg = cfg
        self._devices: list[Device] = []
        self._radio_on = True
        self._rates: dict | None = None     # key -> Rates (time-left estimates)
        self._light = taskbar.taskbar_uses_light_theme()
        self._pills: list[_Pill] = []
        self._rects: list[tuple] = []
        self._hover = -1
        self._pill_h = 28
        self._pill_scale = 1.0          # effective pill height / the height the placement asked for
        self._vertical = False
        self._styled = False
        self._menu: QMenu | None = None
        self._last_geom = None
        self._floating = False          # last applied placement was floating (draws a backdrop, drags)
        self._cur_screen: ScreenInfo | None = None
        self._cur_qscreen = None
        self._last_reason = "ok"
        self._press_global: QPoint | None = None
        self._press_origin = QPoint()
        self._dragging = False
        self._press_idx = -1
        self._screens_cache: list | None = None     # [(ScreenInfo, QScreen)], dropped on screen signals
        self._reg_cache: dict = {}                  # fn -> (monotonic time, value), ~5 s TTL
        self._overrides: dict = {}                  # cfg key -> (cfg value when set, value) after a failed save
        self._n_screens = 1
        self._watched: list = []
        self._want_visible = False
        self._all_pills: list[_Pill] = []
        self._max_extent: int | None = None     # px along the main axis the pills may use
        self._alert_keys: set[str] | None = None
        self._devices_seen = False
        self._flash_keys: set[str] = set()
        self._flash_left = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(FLASH_MS)
        self._flash_timer.timeout.connect(self._flash_tick)
        self.setAccessibleName(tr("app_name"))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._edge = taskbar.EDGE_BOTTOM
        self._popup: HoverPopup | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(self._show_popup)
        self._fs_override = False       # fullscreen app on this monitor: the "in fullscreen" spot is in use
        self._passive = False           # shown over a fullscreen app: click-through unless Shift is held
        self._shift_held = False
        self._shift_timer = QTimer(self)
        self._shift_timer.setInterval(SHIFT_POLL_MS)
        self._shift_timer.timeout.connect(self._shift_tick)
        self._zguard = _ZGuard(lambda: int(self.winId()) if self.isVisible() else 0,
                               self._popup_hwnd)
        self.destroyed.connect(self._zguard.stop)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.update_position)
        self._clock = animation.clock()     # shared; runs only while something visible is animated
        self._clock.register(self, self._anim_demand)
        self._clock.tick.connect(self._on_anim_tick)
        self._clock.state_changed.connect(self._on_anim_state)
        if cfg is not None:
            self._clock.set_mode(self._cfg_get("animations", "system"))
        self._rebuild()
        self._connect_screen_signals()

    # ------------------------------------------------------------------ public API
    def set_config(self, cfg) -> None:
        self._cfg = cfg
        self._overrides.clear()
        self._clock.set_mode(self._cfg_get("animations", "system"))
        self._rebuild()
        self.update_position()

    def refresh_style(self) -> None:
        """Re-measure and repaint after ``icons.set_icon_style(...)`` (set_config(cfg) does the same)."""
        self._relayout()
        self.update_position()

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = list(devices)
        self._rebuild()
        self._devices_seen = True
        self.update_position()

    def set_rates(self, rates) -> None:
        """key -> log_stats.Rates for recorded devices (time-left estimates); used on the next rebuild."""
        self._rates = dict(rates) if rates else None

    def set_radio(self, on: bool) -> None:
        self._radio_on = bool(on)
        self._rebuild()
        self.update_position()

    def set_light_theme(self, light: bool) -> None:
        if light != self._light:
            self._light = light
            self._rebuild()         # the pill colours depend on the theme
            self.update()

    def _pill_dark(self) -> bool:
        """Dark look of the pills: they sit on the taskbar, so they follow it; a floating widget (no taskbar
        behind it) follows the app theme when the user picked "light" or "dark"."""
        theme = self._cfg_get("app_theme", "auto")
        if self._floating and theme in ("light", "dark"):
            return theme == "dark"
        return not self._light

    def set_menu(self, menu: QMenu) -> None:
        self._menu = menu

    def start(self) -> None:
        self._timer.start()
        self._zguard.start()
        self.update_position()

    def stop(self) -> None:
        self._timer.stop()
        self._zguard.stop()
        self.hide()

    def pill_texts(self) -> list[str]:
        return [p.text for p in self._pills]

    # ------------------------------------------------------------------ model -> pills
    def _cfg_get(self, key, default):
        try:
            return self._cfg.get(key) if self._cfg is not None else default
        except Exception:
            return default

    def _rebuild(self) -> None:
        threshold = int(self._cfg_get("low_threshold", 20))
        show_disc = bool(self._cfg_get("show_disconnected", False))
        hidden = self.hidden_ids()
        pills: list[_Pill] = []
        if not self._radio_on:
            off = tr("bluetooth_off")
            pills.append(_Pill("bluetooth_off", tr("pill_off"), off, dim=True, a11y=off))
        for d in self._devices:
            if visibility_key(d.id) in hidden:
                continue
            if not self._radio_on and getattr(d, "link", "bluetooth") == "bluetooth":
                continue
            if not d.connected and not show_disc:
                continue
            name = sanitize_name(d.name) or "?"
            kind_name = _kind_name(d.kind) if d.kind else ""
            link = getattr(d, "link", "bluetooth")
            link_key = {"2.4ghz": "link_24ghz", "usb": "link_usb"}.get(link, "link_bluetooth")
            kind_name = f"{kind_name} - {tr(link_key)}" if kind_name else tr(link_key)
            if getattr(d, "stale", False) and d.battery is not None:
                last = stale_text(d)
                pills.append(_Pill(d.kind, f"{d.battery}%", f"{name}\n{last}\n{kind_name}", dim=True,
                                   device=d, a11y=f"{name} {last}"))
            elif not d.connected:
                text, tip = tr("pill_none"), f"{name}\n{tr('disconnected')}\n{kind_name}"
                pills.append(_Pill(d.kind, text, tip, dim=True, device=d,
                                   a11y=f"{name} {tr('disconnected')}"))
            elif d.battery is None:
                pills.append(_Pill(d.kind, "?", f"{name}\n{tr('battery_unknown')}\n{kind_name}", device=d,
                                   a11y=f"{name} {tr('battery_unknown')}"))
            else:
                tip = f"{name}\n{d.battery}%"
                spoken = f"{name} {d.battery} %"
                if d.charging:
                    tip += f" ({tr('charging')})"
                    spoken += f" {tr('charging')}"
                idle = bool(getattr(d, "idle", False))
                if idle:
                    tip += f" ({tr('idle')})"
                    spoken += f" {tr('idle')}"
                left = None if idle else remaining_text(d, self._rates)
                if left:
                    tip += f"\n{left}"
                tip += f"\n{kind_name}"
                st = row_style(d, threshold, self._pill_dark(), self._color_mode() == "levels")
                alert = st.alert
                if alert:
                    spoken += f" {tr('low_warning')}"
                pills.append(_Pill(d.kind, f"{d.battery}%", tip,
                                   low=d.battery <= threshold, charging=bool(d.charging),
                                   level=d.battery, device=d, idle=idle,
                                   warn="warn" in st.cues, alert=alert, a11y=spoken))
        if not pills and self._cfg_get("show_when_empty", False):
            pills.append(_Pill("bluetooth", tr("pill_none"), tr("no_devices"), dim=True,
                                a11y=tr("no_devices")))
        self._all_pills = pills
        self._track_alerts()
        self._hide_popup()
        self._hover = -1
        self.setAccessibleName(tr("app_name"))
        self.setAccessibleDescription(", ".join(p.a11y for p in pills) or tr("no_devices"))
        self._relayout()
        self._set_kfocus(self._kfocus)      # keep the accessible name in step with the rebuilt pills
        self._rearm_hover()

    def _cursor_pos(self) -> QPoint:
        return QCursor.pos()

    def _rearm_hover(self) -> None:
        """After a rebuild the pill under a still mouse is hovered again (and its popup re-armed)."""
        if not self.isVisible() or not self._pills or not self._interactive():
            return
        lp = self.mapFromGlobal(self._cursor_pos())
        idx = pill_index_at(self._rects, lp.x(), lp.y())
        if idx >= 0:
            self._hover = idx
            self._hover_timer.start()

    # ---- one-off blink when a device first drops to the alert level
    def _track_alerts(self) -> None:
        cur = {visibility_key(p.device.id) for p in self._all_pills if p.alert and p.device is not None}
        if self._alert_keys is not None and self._devices_seen:
            new = cur - self._alert_keys
            if new:
                self._flash_keys, self._flash_left = new, FLASH_TICKS
                self._flash_timer.start()
        self._alert_keys = cur

    def _flash_tick(self) -> None:
        self._flash_left = max(0, self._flash_left - 1)
        if self._flash_left == 0:
            self._flash_timer.stop()
            self._flash_keys = set()
            self._clock.refresh()       # the critical pulse resumes after the one-off blink
        self.update()

    def _flashing(self, pill: _Pill) -> bool:
        return (pill.device is not None and self._flash_left % 2 == 1
                and visibility_key(pill.device.id) in self._flash_keys)

    def hidden_ids(self) -> set[str]:
        """Visibility keys (models.visibility_key) of the devices hidden by the user."""
        v = self._cfg_get("hidden_devices", [])
        return {x for x in v if isinstance(x, str)} if isinstance(v, (list, tuple, set)) else set()

    def _color_mode(self) -> str:
        return "mono" if self._cfg_get("color_mode", "levels") == "mono" else "levels"

    def pill_color(self, pill: _Pill) -> QColor | None:
        """Level colour of a pill, or None when it has no reading / colours are off."""
        if self._color_mode() == "mono" or pill.level is None:
            return None
        return battery_color(pill.level, pill.charging, int(self._cfg_get("low_threshold", 20)),
                             self._pill_dark())

    def _ui_pct(self) -> int:
        """Config ui_scale (90 / 100 / 115 / 130); anything else counts as 100."""
        v = self._cfg_get("ui_scale", 100)
        return v if isinstance(v, int) and not isinstance(v, bool) and v in UI_SCALES else 100

    @staticmethod
    def scaled_pill_h(base: int, pct: int, bar_thickness: float | None = None) -> int:
        """Pill height for the UI scale `pct`. On a taskbar it never exceeds the bar thickness minus the
        outer margins (so at 130 % a small bar limits the pills: they simply stop growing); it is never
        reduced below what the placement itself asked for by that limit."""
        h = round(base * pct / 100)
        if bar_thickness is not None and pct > 100:
            h = min(h, max(base, int(bar_thickness) - 2 * MARGIN))
        return max(1, h)

    def pill_style(self) -> str:
        """Configured pill look (classic | soft | transparent | compact); anything else counts as transparent."""
        v = self._cfg_get("pill_style", "transparent")
        return v if isinstance(v, str) and v in PILL_STYLES else "transparent"

    def _backless(self) -> bool:
        """Transparent styles: no pill background or outline (the value colour and "!" carry the state)."""
        return self.pill_style() in ("transparent", "compact")

    @property
    def _pad(self) -> int:
        base = STYLE_PAD[self.pill_style()]
        return max(3 if base < PAD else 4, round(base * self._pill_scale))

    @property
    def _spacing(self) -> int:
        return max(1, round(STYLE_SPACING[self.pill_style()] * self._pill_scale))

    @property
    def _gap(self) -> int:
        return max(3, round(ICON_GAP * self._pill_scale))

    def _font(self) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(max(MIN_FONT_PX, round(self._pill_h * 0.45)))
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def _icon_size(self) -> int:
        return max(12, round(self._pill_h * 0.58))

    def _text_color(self, pill: _Pill, fg: QColor, dark: bool) -> QColor:
        """Opaque text colour; every choice keeps >= 4.5:1 on the pill background (see colors.py)."""
        if pill.kind == "overflow":
            return fg
        if pill.dim or (pill.device is not None and pill.level is None):
            return band_color("grey", dark)
        lc = self.pill_color(pill)
        col = lc if lc is not None else (band_color("red", dark) if pill.alert else fg)
        if self._backless():    # nothing tints the pill: the text sits on the taskbar itself
            return ensure_contrast(col, taskbar_base(dark), 4.5)
        return col

    @staticmethod
    def _custom_file(pill: _Pill, icon: int) -> str | None:
        """User image for this pill (device > kind) if one is set and loads; else None (built-in icon)."""
        if pill.device is None:
            return None
        name = custom_icons.file_for(pill.kind, visibility_key(pill.device.id))
        if name is None or not custom_icons.usable(name):
            return None
        return name

    def _pill_width(self, p: _Pill, fm: QFontMetrics, icon: int, bolt: int) -> int:
        tw = fm.horizontalAdvance(("!" if p.warn else "") + p.text)
        if p.kind == "overflow":
            return 2 * self._pad + tw
        style = get_icon_style()
        own_bolt = p.charging and style != "battery"     # the battery icon draws its own bolt
        extra = (bolt if own_bolt else 0) + (MOON_W + 3 if p.idle else 0)
        if style == "none" and self._custom_file(p, icon) is None:
            return pill_width(tw, 0, extra, pad=self._pad, gap=0)
        return pill_width(tw, icon, extra, pad=self._pad, gap=self._gap)

    def _relayout(self) -> None:
        fm = QFontMetrics(self._font())
        icon = self._icon_size()
        bolt = max(6, icon // 2)
        allp = self._all_pills
        widths = [self._pill_width(p, fm, icon, bolt) for p in allp]
        extents = [self._pill_h] * len(allp) if self._vertical else widths
        ow_text = ("!" if any(p.warn for p in allp) else "") + f"+{len(allp)}"
        ow_w = 2 * self._pad + fm.horizontalAdvance(ow_text)
        k = fit_count(extents, self._max_extent, self._pill_h if self._vertical else ow_w)
        pills, widths = list(allp[:k]), widths[:k]
        if k < len(allp):
            rest = allp[k:]
            warn = any(r.warn or r.alert for r in rest)
            alert = any(r.alert for r in rest)
            n = len(rest)
            more = tr("panel_more_one" if n == 1 else "panel_more_other", n=n)
            pills.append(_Pill("overflow", f"+{n}", "\n".join(r.a11y for r in rest),
                               a11y=more, members=rest, warn=warn, alert=alert))
            widths.append(2 * self._pad + fm.horizontalAdvance(("!" if warn else "") + f"+{n}"))
        self._pills = pills
        if self._kfocus >= len(pills):
            self._set_kfocus(len(pills) - 1)
        self._rects, (tw, th) = layout_pills(widths, self._pill_h, self._vertical, spacing=self._spacing)
        self.setFixedSize(QSize(max(tw, 1), max(th, 1)))
        self.update()
        self._clock.refresh()       # the animated pills may have changed

    # ------------------------------------------------------------------ positioning
    def _screens(self) -> list:
        """[(ScreenInfo, QScreen)]; cached and rebuilt only after a screen signal."""
        cache = self._screens_cache
        if cache is not None:
            # a screen can be destroyed before its removal signal reaches us (unplug, RDP, tests)
            current = QGuiApplication.screens()
            if any(not shiboken6.isValid(sc) or sc not in current for _, sc in cache):
                cache = None
        if cache is None:
            cache = self._screens_cache = screen_infos()
        return cache

    def _reg(self, fn):
        """fn() with a ~5 s cache (registry reads); keyed by the function object."""
        now = time.monotonic()
        hit = self._reg_cache.get(fn)
        if hit is not None and now - hit[0] < REG_CACHE_TTL:
            return hit[1]
        val = fn()
        self._reg_cache[fn] = (now, val)
        return val

    def _cfg_eff(self, key, default):
        """Config value, or the in-memory value of a save that failed (while the config is unchanged)."""
        cur = self._cfg_get(key, default)
        ov = self._overrides.get(key)
        return ov[1] if ov is not None and ov[0] == cur else cur

    def _store(self, key, value, default) -> None:
        base = self._cfg_get(key, default)
        try:
            if self._cfg is None:
                raise RuntimeError("no config")
            self._cfg.set(key, value)
            self._overrides.pop(key, None)
        except Exception:
            self._overrides[key] = (base, value)    # a failed save still applies for this session

    def _connect_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.primaryScreenChanged.connect(self._on_screens_changed)
        for sc in QGuiApplication.screens():
            self._watch_screen(sc)

    def _watch_screen(self, sc) -> None:
        if any(sc == w for w in self._watched):
            return
        self._watched.append(sc)
        sc.geometryChanged.connect(self._on_screens_changed)
        sc.availableGeometryChanged.connect(self._on_screens_changed)

    def _on_screen_added(self, sc) -> None:
        self._watch_screen(sc)
        self._on_screens_changed()

    def _on_screen_removed(self, sc) -> None:
        keep = []
        for w in self._watched:
            if w == sc:
                try:
                    w.geometryChanged.disconnect(self._on_screens_changed)
                    w.availableGeometryChanged.disconnect(self._on_screens_changed)
                except (RuntimeError, TypeError):
                    pass
            else:
                keep.append(w)
        self._watched = keep
        if self._cur_qscreen is not None and self._cur_qscreen == sc:
            self._cur_qscreen = None
        self._on_screens_changed()

    def _on_screens_changed(self, *_args) -> None:
        self._screens_cache = None
        self._last_geom = None
        self.update_position()
        QTimer.singleShot(0, self._reposition_later)     # the screen list settles after the signal

    def _reposition_later(self) -> None:
        try:
            self._last_geom = None
            self.update_position()
        except RuntimeError:        # widget already deleted
            pass

    def _is_floating(self) -> bool:
        return self._cfg_get("placement_mode", "taskbar") == "floating"

    def _build_placement(self, choice: str, screen: ScreenInfo, fallback: bool = False) -> Placement:
        if self._is_floating():
            anchor, dx, dy = lookup_floating(self._cfg_eff("floating_positions", {}), screen.key, choice)
            return Placement("floating", "right", choice, anchor, dx, dy)
        anchor = "left" if self._cfg_get("taskbar_anchor", "right") == "left" else "right"
        if fallback:    # taskbar mode on a monitor without a taskbar: saved floating spot of that monitor
            fa, dx, dy = lookup_floating(self._cfg_eff("floating_positions", {}), screen.key, choice)
            return Placement("taskbar", anchor, choice, fa, dx, dy)
        return Placement("taskbar", anchor, choice)

    def _build_env(self, info, screen: ScreenInfo, placement: Placement, fallback: bool = False) -> Env:
        fullscreen = False
        if (self._pills
                and (info is not None or fallback or self._is_floating())):
            if self._n_screens > 1:
                fullscreen = taskbar.is_fullscreen_on_monitor(screen.name)
            else:       # single monitor: the global verdict alone decides (1.5.0 behaviour)
                if info is not None:
                    mon = info.monitor_rect
                else:
                    gx, gy, gw, gh = screen.geometry
                    d = screen.dpr or 1.0
                    mon = (round(gx * d), round(gy * d), round((gx + gw) * d), round((gy + gh) * d))
                fullscreen = taskbar.is_fullscreen_foreground(mon)
        left = placement.mode == "taskbar" and placement.anchor == "left" and info is not None
        centered = self._reg(taskbar.taskbar_icons_centered) if left else True
        widgets = self._reg(taskbar.taskbar_widgets_button) if left else False
        return Env(info=info, fullscreen=fullscreen, has_pills=bool(self._pills),
                   screen_name=screen.name, screen_origin=screen.geometry[:2], dpr=screen.dpr or 1.0,
                   size=(self.width(), self.height()), available=screen.available, icons_centered=centered,
                   widgets_button=widgets, floating_fallback=fallback,
                   show_in_fullscreen=bool(self._cfg_get("show_in_fullscreen", False)))

    def _apply_screen(self, qscreen) -> None:
        """Bind the window to `qscreen` before moving it (per-monitor DPI)."""
        if qscreen is None or qscreen == self._cur_qscreen:
            return
        self._cur_qscreen = qscreen
        wh = self.windowHandle()
        if wh is not None:
            if wh.screen() != qscreen:
                wh.setScreen(qscreen)
        elif self.screen() != qscreen:
            self.setScreen(qscreen)

    @staticmethod
    def _left_button_down() -> bool:
        return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def _cancel_drag(self) -> None:
        """Forget a drag/press whose release never arrived (button released elsewhere, window hidden)."""
        was = self._dragging
        self._press_global, self._dragging, self._press_idx = None, False, -1
        if was:
            self._last_geom = None      # the window was moved by hand: force it back to its place
            self.update()

    def current_screen_key(self) -> str | None:
        """Key of the monitor the widget currently resolves to (None before the first placement)."""
        return self._cur_screen.key if self._cur_screen is not None else None

    def _set_floating(self, floating: bool) -> None:
        if self._floating != floating:
            self._floating = floating
            if self._cfg_get("app_theme", "auto") != "auto":
                self._rebuild()     # pill colours follow the app theme only while floating
            if floating:
                self.setCursor(Qt.CursorShape.SizeAllCursor)    # the whole bar is a drag handle
            else:
                self.unsetCursor()
            self.update()

    def update_position(self) -> None:
        if self._dragging or self._press_global is not None:
            if not self._left_button_down():
                self._cancel_drag()         # stuck drag: the release was lost
            elif self._dragging:
                return
        pairs = self._screens()
        self._n_screens = len(pairs)
        choice = self._cfg_eff("placement_monitor", "primary")
        choice = choice if isinstance(choice, str) and choice else "primary"
        sinfo = resolve_screen(choice, [p[0] for p in pairs])   # missing monitor -> primary (config untouched)
        if sinfo is None or not self._pills:
            self._set_visible(False)
            return
        qscreen = next(q for i, q in pairs if i is sinfo)
        self._cur_screen = sinfo
        floating = self._is_floating()
        infos = [] if floating else taskbar.get_taskbar_infos()
        tinfo = None if floating else taskbar_for_screen(sinfo, infos, {i.name for i, _ in pairs})
        # A monitor without a taskbar floats; no primary taskbar at all (explorer restarting) just hides.
        fallback = (not floating and tinfo is None and any(i.kind == "primary" for i in infos))
        placement = self._build_placement(choice, sinfo, fallback)
        env = self._build_env(tinfo, sinfo, placement, fallback)
        fs_placement = choose_placement(env, placement, self._cfg_eff("fullscreen_placement", "same"),
                                        self._cfg_eff("fullscreen_floating_positions", {}), sinfo.key, choice)
        self._fs_override = fs_placement is not placement
        if self._fs_override:       # float on this monitor while the fullscreen app is up; back afterwards
            placement, floating, tinfo = fs_placement, True, None
            env = replace(env, info=None, floating_fallback=False)
        res = compute_placement(env, placement)
        self._note_reason(res.reason)
        floating = floating or res.reason == REASON_NO_TASKBAR_FLOATING
        if not res.visible:
            self._set_floating(floating)
            self._set_visible(False)
            return
        self._apply_screen(qscreen)
        self._edge = (tinfo.edge if tinfo is not None
                      else taskbar.EDGE_TOP if placement.float_anchor.startswith("top") else taskbar.EDGE_BOTTOM)
        self._set_floating(floating)
        thickness = None
        if tinfo is not None and not floating:      # a real taskbar: the pills may not outgrow it
            dpr = env.dpr or 1.0
            thickness = (taskbar.rect_w(tinfo.rect) if res.vertical else taskbar.rect_h(tinfo.rect)) / dpr
        eff_h = self.scaled_pill_h(res.pill_h, self._ui_pct(), thickness)
        scale = eff_h / res.pill_h if res.pill_h else 1.0
        vertical, max_extent = res.vertical, res.max_extent
        if floating and bool(self._cfg_get("stack_devices", False)) and sinfo.available:
            # Stacked (any floating position): one device per row; a bottom anchor grows upward, a top one
            # downward, a middle one around its centre (the anchor math keeps it in place). Limit = height.
            vertical, max_extent = True, max(MIN_LIMIT, int(sinfo.available[3] * FLOATING_SPACE_SHARE))
        if (eff_h != self._pill_h or vertical != self._vertical or max_extent != self._max_extent
                or abs(scale - self._pill_scale) > 1e-6):
            self._pill_h, self._vertical, self._max_extent = eff_h, vertical, max_extent
            self._pill_scale = scale
            self._relayout()
            env = replace(env, size=(self.width(), self.height()))      # size changed; nothing else re-read
            res = compute_placement(env, placement)
        x, y = res.rect[0], res.rect[1]
        key = (x, y, self.width(), self.height(), sinfo.name, placement.mode, res.reason)
        if self._last_geom != key:
            self._last_geom = key
            self.move(x, y)
        self._set_visible(True)
        self._set_passive(res.fullscreen_passive and self.isVisible())
        if self.isVisible():
            _assert_topmost(int(self.winId()))
            if self._popup is not None and self._popup.isVisible():
                self._popup.raise_topmost()
        self._clock.refresh()       # also re-reads the Windows animation setting once a minute

    def _note_reason(self, reason: str) -> None:
        """Emit placement_warning once each time the placement starts needing a fallback."""
        if reason == REASON_LEFT_NEEDS_CENTERED and self._last_reason != reason:
            self.placement_warning.emit(reason)
        self._last_reason = reason

    def _set_visible(self, vis: bool) -> None:
        if vis and not self.isVisible():
            self.show()
        elif not vis and self.isVisible():
            self._hide_popup()
            self._set_passive(False)
            self.hide()

    # ------------------------------------------------------------------ fullscreen passive mode
    def _interactive(self) -> bool:
        return not self._passive or self._shift_held

    def _set_passive(self, on: bool) -> None:
        """Over a fullscreen app the widget ignores the mouse (click-through) unless Shift is held."""
        if on == self._passive:
            return
        self._passive = on
        self._shift_held = False
        self._clock.refresh()       # over a fullscreen app nothing animates
        self.update()
        self._hide_popup()
        if self._hover != -1:
            self._hover = -1
            self.update()
        if self._dragging or self._press_global is not None:
            self._cancel_drag()
        if on:
            set_click_through(int(self.winId()), True)
            self._shift_timer.start()
        else:
            self._shift_timer.stop()
            set_click_through(int(self.winId()), False)

    def _shift_tick(self) -> None:
        if not self._passive:
            self._shift_timer.stop()
            return
        down = shift_down()
        if down == self._shift_held:
            return
        self._shift_held = down
        set_click_through(int(self.winId()), not down)
        if not down:
            self._hide_popup()
            self._hover = -1
            if self._dragging or self._press_global is not None:
                self._cancel_drag()
            self.update()

    # ------------------------------------------------------------------ hover popup
    def _popup_hwnd(self) -> int:
        p = self._popup
        return int(p.winId()) if p is not None and p.isVisible() else 0

    def _hide_popup(self) -> None:
        self._hover_timer.stop()
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()

    def _show_popup(self) -> None:
        idx = self._hover
        if not self._interactive() or idx < 0 or idx >= len(self._pills) or not self.isVisible():
            return
        pill = self._pills[idx]
        if pill.members is not None:
            content = PopupContent(tr("more_devices"), [m.a11y for m in pill.members[:10]])
        elif pill.device is None:
            parts = [t for t in pill.tooltip.split(chr(10)) if t]
            content = PopupContent(parts[0] if parts else "", parts[1:])
        else:
            content = build_popup_content(pill.device, int(self._cfg_get("low_threshold", 20)),
                                          remaining=remaining_text(pill.device, self._rates))
        if self._popup is None:
            self._popup = HoverPopup()
        self._popup.set_content(content, dark=self._pill_dark(),
                                show_dot=self._color_mode() == "levels")
        rx, ry, rw, rh = self._rects[idx]
        tl = self.mapToGlobal(QPoint(int(rx), int(ry)))
        scr = (self.screen() or QGuiApplication.primaryScreen()).geometry()
        pos = place_popup((tl.x(), tl.y(), int(rw), int(rh)),
                          (self._popup.width(), self._popup.height()),
                          (scr.x(), scr.y(), scr.width(), scr.height()), self._edge)
        self._popup.show_at(pos)

    def hideEvent(self, e) -> None:
        self._hide_popup()
        if self._dragging or self._press_global is not None:
            self._cancel_drag()         # hidden mid-drag: the release can no longer reach us
        super().hideEvent(e)
        self._clock.refresh()

    def closeEvent(self, e) -> None:
        self._hide_popup()
        self._timer.stop()
        self._flash_timer.stop()
        self._zguard.stop()
        self._clock.unregister(self)
        self._clock.refresh()
        super().closeEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._styled:
            self._styled = True
            _apply_ex_styles(int(self.winId()))
        _assert_topmost(int(self.winId()))
        self._clock.refresh()

    # ------------------------------------------------------------------ animation
    def _anim_live(self) -> bool:
        """Animated content moves: enabled, not switched off by Windows, and not in the passive fullscreen state."""
        return self._clock.allowed() and not self._passive and self.isVisible()

    def _anim_plan(self, now: int) -> list[tuple[int, int]]:
        """[(pill index, ms until it needs repainting)] for every pill animated right now."""
        style = get_icon_style()
        out = []
        for i, pill in enumerate(self._pills):
            ms = None
            name = None
            if pill.kind != "overflow":
                name = self._custom_file(pill, 0)
                if name is not None and custom_icons.is_animated(name):
                    ms = custom_icons.ms_to_next_frame(name, now)
                if pill.charging and (style != "battery" or name is None):
                    ms = animation.PULSE_INTERVAL_MS if ms is None else min(ms, animation.PULSE_INTERVAL_MS)
            if pill.alert and self._flash_left == 0:
                ms = animation.PULSE_INTERVAL_MS if ms is None else min(ms, animation.PULSE_INTERVAL_MS)
            if ms is not None:
                out.append((i, ms))
        return out

    def _anim_demand(self):
        """Clock callback: ms until the next repaint, or None (hidden, passive, nothing animated)."""
        if not self._pills or not self._anim_live():
            return None
        plan = self._anim_plan(self._clock.now_ms())
        return min(ms for _, ms in plan) if plan else None

    def _on_anim_tick(self, now: int) -> None:
        if not self._anim_live():
            return
        for i, _ms in self._anim_plan(now):
            x, y, w, h = self._rects[i]
            self.update(QRect(int(x) - 1, int(y) - 1, int(w) + 2, int(h) + 2))

    def _on_anim_state(self) -> None:
        self.update()       # paused / resumed: redraw (still or moving)

    # ------------------------------------------------------------------ painting
    def _colors(self):
        return QColor(255, 255, 255) if self._pill_dark() else QColor(20, 20, 20)

    @staticmethod
    def _draw_moon(p: QPainter, r: QRectF, color: QColor) -> None:
        outer = QPainterPath()
        outer.addEllipse(r)
        cut = QPainterPath()
        cut.addEllipse(r.translated(r.width() * 0.38, -r.height() * 0.18))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(outer.subtracted(cut))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = self._pill_dark()
        fg = self._colors()
        if self._floating:      # backdrop so the pills read over any wallpaper
            bg = QColor(*TASKBAR_BASE[dark])
            bg.setAlpha(FLOATING_BG_ALPHA)
            edge = QColor(fg)
            edge.setAlpha(FLOATING_BORDER_ALPHA)
            border = QPen(edge)
            border.setWidthF(1.0)
            p.setPen(border)
            p.setBrush(bg)
            rad = min(self.height() / 2, self._pill_h / 2 + MARGIN)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        p.setFont(self._font())
        icon = self._icon_size()
        style = get_icon_style()
        now = self._clock.now_ms() if self._anim_live() else None      # None: paused, draw the still look
        pstyle = self.pill_style()
        backless = pstyle in ("transparent", "compact")
        if pstyle == "transparent":
            self._draw_separators(p, dark)
        for i, (pill, (x, y, w, h)) in enumerate(zip(self._pills, self._rects)):
            r = QRectF(x, y, w, h)
            level_color = self.pill_color(pill)
            color = self._text_color(pill, fg, dark)
            hover = i == self._hover
            pulse = animation.alert_pulse(now) if pill.alert and self._flash_left == 0 else 0.0
            rad = max(2.0, SOFT_RADIUS * self._pill_scale) if pstyle == "soft" else h / 2.5
            content_opacity = 1.0
            if backless:
                # no background: only a faint hover tint; the critical pulse / blink fades the text and icon
                if hover:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(pill_tint("normal", dark))
                    p.drawRoundedRect(r, rad, rad)
                if pill.alert:
                    content_opacity = 0.45 if self._flashing(pill) else animation.alert_text_opacity(pulse)
            else:
                if pill.alert:
                    if hover or self._flashing(pill):
                        fill = pill_tint("low_hover", dark)
                    else:
                        fill = animation.alert_fill(dark, pulse)
                else:
                    fill = pill_tint("hover" if hover else "normal", dark)
                if pstyle == "soft":
                    fill = QColor(fill)
                    fill.setAlpha(round(fill.alpha() * SOFT_ALPHA_SHARE))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, rad, rad)
            if i == self._kfocus and self.hasFocus():     # keyboard focus ring
                pen = QPen(fg)
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
            if content_opacity < 1.0:
                p.setOpacity(content_opacity)
            if pill.alert and not backless:      # non-colour cue for "very low": 1 px outline
                edge_c = QColor(color)
                edge_c.setAlpha(animation.alert_border_alpha(pulse))
                pen = QPen(edge_c)
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
            if pill.kind == "overflow":
                p.setPen(color)
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), ("!" if pill.warn else "") + pill.text)
                p.setOpacity(1.0)
                continue
            ir = QRectF(x + self._pad, y + (h - icon) / 2, icon, icon)
            icon_color = fg if level_color is not None else color
            if pill.dim:
                icon_color = QColor(icon_color)
                icon_color.setAlpha(DIM_ALPHA)
            elif pill.idle:
                icon_color = QColor(icon_color)
                icon_color.setAlpha(IDLE_ALPHA)
            custom = self._custom_file(pill, icon)
            chg = animation.charge_pulse(now) if pill.charging else 1.0     # charging bolt: opacity 60-100 %
            if custom is not None:      # user image: drawn in every style (also "none"), dimmed like the icon
                custom_icons.draw_custom_icon(p, ir, custom, icon_color.alphaF(), now)
                text_x = x + self._pad + icon + self._gap
            elif style == "none":
                text_x = x + self._pad
            else:
                if style in ("battery", "retro") and level_color is not None:
                    accent = QColor(color)      # the level colour drives the gauge / the retro accent
                    accent.setAlpha(icon_color.alpha())
                    icon_color = accent
                if style == "battery":      # the gauge carries its own bolt: the whole icon breathes
                    p.setOpacity(chg)
                draw_kind_icon(p, pill.kind, ir, icon_color, pill.level, pill.charging, style)
                p.setOpacity(1.0)
                text_x = x + self._pad + icon + self._gap
            if pill.idle:
                moon = QColor(fg)
                moon.setAlpha(210)
                self._draw_moon(p, QRectF(text_x - 2, y + (h - MOON_W) / 2, MOON_W, MOON_W), moon)
                text_x += MOON_W + 3
            own_bolt = pill.charging and style != "battery"
            bolt = max(6, icon // 2) if own_bolt else 0
            p.setPen(color)
            p.drawText(QRectF(text_x, y, w - (text_x - x) - self._pad - bolt, h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       ("!" if pill.warn else "") + pill.text)
            if level_color is not None and pill.level is not None and h >= BAR_MIN_PILL_H:
                bw = w - 2 * self._pad
                by = y + h - 5.0
                bar = QColor(color)
                if pill.idle:
                    bar.setAlpha(IDLE_ALPHA)
                track = QColor(color)
                track.setAlpha(55)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(track)
                p.drawRoundedRect(QRectF(x + self._pad, by, bw, BAR_H), BAR_H / 2, BAR_H / 2)
                p.setBrush(bar)
                p.drawRoundedRect(QRectF(x + self._pad, by, max(BAR_H, bw * max(0, min(100, pill.level)) / 100),
                                         BAR_H), BAR_H / 2, BAR_H / 2)
            if own_bolt:
                bh = icon * 0.8
                p.setOpacity(chg)
                draw_bolt(p, QRectF(x + w - self._pad - bolt, y + (h - bh) / 2, bolt, bh),
                          QColor(110, 220, 120) if dark else QColor(30, 150, 60))
                p.setOpacity(1.0)
            p.setOpacity(1.0)
        p.end()

    def _draw_separators(self, p: QPainter, dark: bool) -> None:
        """Thin divider centred in the gap between two neighbouring pills (none after the last one)."""
        pen = QPen(separator_color(dark))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for (x, y, w, h), (nx, ny, _nw, _nh) in zip(self._rects, self._rects[1:]):
            if self._vertical:
                ly = (y + h + ny) / 2
                half = w * SEPARATOR_SHARE / 2
                cx = x + w / 2
                p.drawLine(QPointF(cx - half, ly), QPointF(cx + half, ly))
            else:
                lx = (x + w + nx) / 2
                half = h * SEPARATOR_SHARE / 2
                cy = y + h / 2
                p.drawLine(QPointF(lx, cy - half), QPointF(lx, cy + half))

    # ------------------------------------------------------------------ interaction
    @staticmethod
    def _drag_threshold() -> int:
        try:
            return max(DRAG_THRESHOLD, int(QGuiApplication.styleHints().startDragDistance()))
        except Exception:
            return DRAG_THRESHOLD

    def _clamp_drag(self, pos: QPoint, cursor: QPoint) -> QPoint:
        """Keep the dragged window inside the available area of the monitor under the cursor."""
        screen = self._cur_screen
        if self._is_floating() and not self._fs_override:   # free to cross monitors; a fallback / fullscreen spot stays on its monitor
            for info, _q in self._screens():
                gx, gy, gw, gh = info.geometry
                if gx <= cursor.x() < gx + gw and gy <= cursor.y() < gy + gh:
                    screen = info
                    break
        if screen is None:
            return pos
        ax, ay, aw, ah = screen.available
        x = max(ax, min(pos.x(), ax + aw - self.width()))
        y = max(ay, min(pos.y(), ay + ah - self.height()))
        return QPoint(x, y)

    def mouseMoveEvent(self, e) -> None:
        if not self._interactive():
            return
        if (self._press_global is not None and self._floating
                and e.buttons() & Qt.MouseButton.LeftButton):
            delta = e.globalPosition().toPoint() - self._press_global
            if not self._dragging and math.hypot(delta.x(), delta.y()) > self._drag_threshold():
                self._dragging = True
                self._hide_popup()
                self._hover = -1
                self.update()
            if self._dragging:
                self.move(self._clamp_drag(self._press_origin + delta, e.globalPosition().toPoint()))
                return
        idx = pill_index_at(self._rects, e.position().x(), e.position().y())
        if idx != self._hover:
            self._hover = idx
            self._hide_popup()
            if idx >= 0:
                self._hover_timer.start()
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hide_popup()
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e) -> None:
        self._hide_popup()
        if not self._interactive():
            return
        if e.button() == Qt.MouseButton.LeftButton:     # touch / click: show the card right away
            self._press_global = e.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._dragging = False
            idx = pill_index_at(self._rects, e.position().x(), e.position().y())
            self._press_idx = idx
            if idx >= 0:
                self._hover = idx
                if self._pills[idx].members is not None:
                    if not self._floating:      # floating: opens on release, so "+N" can also be dragged
                        self._open_panel(idx)
                    self.update()
                    return
                self._show_popup()
                self.update()
        if e.button() == Qt.MouseButton.RightButton:
            self._open_menu(e.globalPosition().toPoint())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            dragged, idx = self._dragging, self._press_idx
            self._press_global, self._dragging, self._press_idx = None, False, -1
            if dragged:
                self._finish_drag()
                return
            if (self._floating and 0 <= idx < len(self._pills) and self._pills[idx].members is not None
                    and pill_index_at(self._rects, e.position().x(), e.position().y()) == idx):
                self._open_panel(idx)       # plain click on "+N" in floating mode
                return
        super().mouseReleaseEvent(e)

    def _save_floating(self, key: str, entry: dict, new_choice: str | None = None) -> None:
        """Store `entry` for monitor `key` (and a new monitor choice); applied this session even if the save fails."""
        positions = self._cfg_eff("floating_positions", {})
        positions = dict(positions) if isinstance(positions, dict) else {}
        positions[key] = entry
        choice = self._cfg_eff("placement_monitor", "primary")
        final = new_choice if new_choice is not None else choice
        if final == "primary":
            positions.pop("primary", None)      # stale legacy entry would make the lookup ambiguous
        self._store("floating_positions", positions, {})
        if final != choice:
            self._store("placement_monitor", final, "primary")
        self._last_geom = None
        self.update_position()

    def _finish_drag(self) -> None:
        """Snap the dropped widget to the nearest anchor of the monitor under it and remember it."""
        if self._fs_override and self._cur_screen is not None:
            self._finish_fullscreen_drag(self._cur_screen)
            return
        pairs = self._screens()
        centre = self.geometry().center()
        target = None
        taskbar_mode = not self._is_floating()      # no-taskbar fallback: stay on that monitor, mode untouched
        for info, _q in pairs:
            gx, gy, gw, gh = info.geometry
            if not taskbar_mode and gx <= centre.x() < gx + gw and gy <= centre.y() < gy + gh:
                target = info
                break
        if target is None:
            target = self._cur_screen or resolve_screen("primary", [i for i, _ in pairs])
        if target is None:
            return
        anchor, dx, dy = snap_anchor((self.x(), self.y(), self.width(), self.height()), target.available)
        choice = self._cfg_eff("placement_monitor", "primary")
        new_choice = choice if taskbar_mode else ("primary" if (choice == "primary" and target.primary)
                                                  else target.key)
        self._save_floating(target.key, {"anchor": anchor, "dx": dx, "dy": dy}, new_choice)

    def _finish_fullscreen_drag(self, screen: ScreenInfo) -> None:
        """Drop over a fullscreen app: remember the spot for this monitor in fullscreen_floating_positions
        only (the normal position is untouched). A fixed-anchor choice becomes "floating" with the new spot."""
        anchor, dx, dy = snap_anchor((self.x(), self.y(), self.width(), self.height()), screen.available)
        positions = self._cfg_eff("fullscreen_floating_positions", {})
        positions = dict(positions) if isinstance(positions, dict) else {}
        positions[screen.key] = {"anchor": anchor, "dx": dx, "dy": dy}
        self._store("fullscreen_floating_positions", positions, {})
        if self._cfg_eff("fullscreen_placement", "same") != "floating":
            self._store("fullscreen_placement", "floating", "same")
        self._last_geom = None
        self.update_position()

    def set_floating_anchor(self, anchor: str) -> None:
        """Keyboard alternative to dragging: park the bar on `anchor` of the current monitor (no offset)."""
        if anchor not in FLOATING_ANCHORS:
            return
        if self._cur_screen is None:
            self.update_position()
        key = self.current_screen_key()
        if key is None:
            return
        self._save_floating(key, {"anchor": anchor, "dx": 0, "dy": 0})

    def _open_panel(self, idx: int) -> None:
        x, y, w, h = self._rects[idx]
        tl = self.mapToGlobal(QPoint(int(x), int(y)))
        self.panel_requested.emit(QRect(tl.x(), tl.y(), int(w), int(h)))

    def _open_menu(self, gp: QPoint) -> None:
        self.context_menu_requested.emit(gp)
        if self._menu is not None:
            popup_menu_foreground(self._menu, gp, int(self.winId()))

    # ---- keyboard / accessibility
    def _set_kfocus(self, idx: int) -> None:
        idx = idx if 0 <= idx < len(self._pills) else -1
        changed = idx != self._kfocus
        self._kfocus = idx
        self.setAccessibleName(self._pills[idx].a11y if idx >= 0 else tr("app_name"))
        self.update()
        if changed or idx >= 0:
            try:
                QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.NameChanged))
            except Exception:
                pass

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self._kfocus < 0 and self._pills:
            back = e.reason() == Qt.FocusReason.BacktabFocusReason
            self._set_kfocus(len(self._pills) - 1 if back else 0)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._hide_popup()
        if (self._dragging or self._press_global is not None) and not self._left_button_down():
            self._cancel_drag()
        self._set_kfocus(-1)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        if self._kfocus >= 0:
            new = self._kfocus + (1 if nxt else -1)
            if 0 <= new < len(self._pills):
                self._set_kfocus(new)
                return True
        return super().focusNextPrevChild(nxt)

    def _kfocus_rect_center(self) -> QPoint:
        x, y, w, h = self._rects[self._kfocus]
        return self.mapToGlobal(QPoint(int(x + w / 2), int(y + h / 2)))

    def keyPressEvent(self, e) -> None:
        key, mods = e.key(), e.modifiers()
        n = len(self._pills)
        if n and key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._hide_popup()
            self._set_kfocus(max(0, self._kfocus - 1) if self._kfocus >= 0 else 0)
        elif n and key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._hide_popup()
            self._set_kfocus(min(n - 1, self._kfocus + 1))
        elif n and key == Qt.Key.Key_Home:
            self._set_kfocus(0)
        elif n and key == Qt.Key.Key_End:
            self._set_kfocus(n - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and 0 <= self._kfocus < n:
            self._hover = self._kfocus
            if self._pills[self._kfocus].members is not None:
                self._hide_popup()
                self._open_panel(self._kfocus)
            else:
                self._show_popup()
        elif key == Qt.Key.Key_Escape:
            self._hide_popup()
        elif key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10
                                        and mods & Qt.KeyboardModifier.ShiftModifier):
            gp = self._kfocus_rect_center() if 0 <= self._kfocus < n else self.mapToGlobal(self.rect().center())
            self._open_menu(gp)
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            return True         # the custom HoverPopup replaces native tooltips
        return super().event(e)
