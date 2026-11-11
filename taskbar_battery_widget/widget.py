"""Frameless, always-on-top, non-activating pill bar docked to the Windows taskbar."""
from __future__ import annotations

import ctypes
import html
import sys
import time
from dataclasses import dataclass
from ctypes import wintypes as wt

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAccessible, QAccessibleEvent, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QMenu, QWidget

from . import taskbar
from .colors import band_color, battery_color, level_band, pill_tint
from .i18n import kind_name as _kind_name, tr
from .icons import draw_bolt, draw_kind_icon
from .models import Device, visibility_key
from .naming import sanitize_name
from .popup import HOVER_DELAY_MS, HoverPopup, PopupContent, build_popup_content, place_popup

MARGIN = 2           # outer margin around the pills (logical px)
SPACING = 4          # gap between pills
PAD = 8              # horizontal padding inside a pill
ICON_GAP = 5         # gap between icon and text
MIN_PILL_H, MAX_PILL_H = 20, 32
MIN_FONT_PX = 12
BAR_MIN_PILL_H = 24   # below this height the level bar is dropped
BAR_H = 3.0
MOON_W = 7
FREE_SPACE_SHARE = 0.4   # share of the taskbar run left of the tray the pills may use
TICK_MS = 500


# --------------------------------------------------------------------------- pure layout
def pill_height_for(taskbar_logical_thickness: float) -> int:
    return int(max(MIN_PILL_H, min(MAX_PILL_H, round(taskbar_logical_thickness) - 14)))


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


def center_rect_y(rect: tuple, cy: int) -> tuple:
    """Shift a (l, t, r, b) rect vertically so its centre is `cy`."""
    h = rect[3] - rect[1]
    top = cy - h // 2
    return (rect[0], top, rect[2], top + h)


def pill_index_at(rects: list[tuple], x: float, y: float) -> int:
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return -1


def tooltip_html(text: str) -> str:
    """Plain tooltip text -> escaped rich text, so device-supplied strings are never parsed as markup."""
    body = html.escape(text, quote=True).replace(chr(10), "<br>")
    return f"<p style='white-space:pre'>{body}</p>"


# --------------------------------------------------------------------------- win32
if sys.platform == "win32":
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.UINT]
    _u32.SetWindowPos.restype = wt.BOOL
    _u32.SetForegroundWindow.argtypes = [wt.HWND]
    _u32.SetForegroundWindow.restype = wt.BOOL
    _u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u32.PostMessageW.restype = wt.BOOL

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10


def _apply_ex_styles(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    h = wt.HWND(hwnd)
    cur = _u32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    _u32.SetWindowLongPtrW(h, GWL_EXSTYLE, cur | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


WM_NULL = 0x0000


def _foreground(hwnd: int) -> None:
    """SetForegroundWindow on one of our own windows (needed so a popup menu closes on outside clicks)."""
    if sys.platform != "win32" or not hwnd:
        return
    _u32.SetForegroundWindow(wt.HWND(hwnd))


def _post_null(hwnd: int) -> None:
    """Classic tray-menu companion: WM_NULL after the menu ends so the menu loop does not linger."""
    if sys.platform != "win32" or not hwnd:
        return
    _u32.PostMessageW(wt.HWND(hwnd), WM_NULL, 0, 0)


def popup_menu_foreground(menu, pos, owner_hwnd: int = 0) -> None:
    """Show `menu` at `pos` from a non-activating window so it dismisses on any outside click.

    The menu's window is made foreground right after it appears; when it hides, WM_NULL is posted to the
    owner window (or the menu's own window when no owner is given).
    """
    menu.popup(pos)
    try:
        mh = int(menu.winId())
        _foreground(mh)
        target = owner_hwnd or mh

        def _closed() -> None:
            try:
                menu.aboutToHide.disconnect(_closed)
            except (RuntimeError, TypeError):
                pass
            _post_null(target)
        menu.aboutToHide.connect(_closed)
    except Exception:
        pass


def _assert_topmost(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    _u32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


EVENT_OBJECT_REORDER = 0x8004
WINEVENT_OUTOFCONTEXT = 0x0000
_ZGUARD_MIN_INTERVAL = 0.015  # seconds; avoids a raise storm if something else keeps reordering


class _ZGuard:
    """Re-raises our window the instant the shell reorders windows.

    On Windows 11 explorer pushes the taskbar back above ordinary topmost windows within ~50 ms,
    so a periodic SetWindowPos alone loses. A WinEvent hook on EVENT_OBJECT_REORDER (delivered in
    this thread's message loop) lets us win the race without a busy timer.
    """

    def __init__(self, hwnd_getter, extra_getter=None):
        self._get = hwnd_getter
        self._extra = extra_getter      # hwnd of a companion window kept just above ours
        self._hook = None
        self._last = 0.0
        self._proc = None

    def start(self) -> None:
        if sys.platform != "win32" or self._hook:
            return
        proto = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                   wt.DWORD, wt.DWORD)
        self._proc = proto(self._on_event)
        _u32.UnhookWinEvent.argtypes = [wt.HANDLE]
        _u32.UnhookWinEvent.restype = wt.BOOL
        _u32.SetWinEventHook.restype = wt.HANDLE
        _u32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, proto, wt.DWORD,
                                         wt.DWORD, wt.DWORD]
        self._hook = _u32.SetWinEventHook(EVENT_OBJECT_REORDER, EVENT_OBJECT_REORDER, None,
                                          self._proc, 0, 0, WINEVENT_OUTOFCONTEXT)

    def stop(self) -> None:
        if self._hook:
            try:
                _u32.UnhookWinEvent(self._hook)
            finally:
                self._hook = None
                self._proc = None

    def _on_event(self, _hook, _event, hwnd, _idobj, _idchild, _thread, _time) -> None:
        try:
            mine = self._get()
            if not mine or (hwnd or 0) == mine:
                return       # our own reorder: re-raising would feed back into itself
            now = time.monotonic()
            if now - self._last < _ZGUARD_MIN_INTERVAL:
                return
            self._last = now
            _assert_topmost(mine)
            extra = self._extra() if self._extra else 0
            if extra:
                _assert_topmost(extra)
        except Exception:
            pass


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
        self._light = taskbar.taskbar_uses_light_theme()
        self._pills: list[_Pill] = []
        self._rects: list[tuple] = []
        self._hover = -1
        self._pill_h = 28
        self._vertical = False
        self._styled = False
        self._menu: QMenu | None = None
        self._last_geom = None
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
        self._zguard = _ZGuard(lambda: int(self.winId()) if self.isVisible() else 0,
                               self._popup_hwnd)
        self.destroyed.connect(self._zguard.stop)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.update_position)
        self._rebuild()

    # ------------------------------------------------------------------ public API
    def set_config(self, cfg) -> None:
        self._cfg = cfg
        self._rebuild()
        self.update_position()

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = list(devices)
        self._rebuild()
        self._devices_seen = True
        self.update_position()

    def set_radio(self, on: bool) -> None:
        self._radio_on = bool(on)
        self._rebuild()
        self.update_position()

    def set_light_theme(self, light: bool) -> None:
        if light != self._light:
            self._light = light
            self.update()

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
            if not d.connected:
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
                tip += f"\n{kind_name}"
                band = level_band(d.battery, bool(d.charging), threshold)
                alert = band == "red"
                if alert:
                    spoken += f" {tr('low_warning')}"
                pills.append(_Pill(d.kind, f"{d.battery}%", tip,
                                   low=d.battery <= threshold, charging=bool(d.charging),
                                   level=d.battery, device=d, idle=idle,
                                   warn=band in ("orange", "red"), alert=alert, a11y=spoken))
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
        if not self.isVisible() or not self._pills:
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
                             not self._light)

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
        if lc is not None:
            return lc
        return band_color("red", dark) if pill.alert else fg

    def _pill_width(self, p: _Pill, fm: QFontMetrics, icon: int, bolt: int) -> int:
        tw = fm.horizontalAdvance(("!" if p.warn else "") + p.text)
        if p.kind == "overflow":
            return 2 * PAD + tw
        extra = (bolt if p.charging else 0) + (MOON_W + 3 if p.idle else 0)
        return pill_width(tw, icon, extra)

    def _relayout(self) -> None:
        fm = QFontMetrics(self._font())
        icon = self._icon_size()
        bolt = max(6, icon // 2)
        allp = self._all_pills
        widths = [self._pill_width(p, fm, icon, bolt) for p in allp]
        extents = [self._pill_h] * len(allp) if self._vertical else widths
        ow_text = f"+{len(allp)}"
        ow_w = 2 * PAD + fm.horizontalAdvance(ow_text)
        k = fit_count(extents, self._max_extent, self._pill_h if self._vertical else ow_w)
        pills, widths = list(allp[:k]), widths[:k]
        if k < len(allp):
            rest = allp[k:]
            pills.append(_Pill("overflow", f"+{len(rest)}", "\n".join(r.a11y for r in rest),
                               a11y=f"+{len(rest)}", members=rest))
            widths.append(2 * PAD + fm.horizontalAdvance(f"+{len(rest)}"))
        self._pills = pills
        if self._kfocus >= len(pills):
            self._set_kfocus(len(pills) - 1)
        self._rects, (tw, th) = layout_pills(widths, self._pill_h, self._vertical)
        self.setFixedSize(QSize(max(tw, 1), max(th, 1)))
        self.update()

    # ------------------------------------------------------------------ positioning
    def _pick_screen(self, monitor_rect):
        mw, mh = taskbar.rect_w(monitor_rect), taskbar.rect_h(monitor_rect)
        for s in QGuiApplication.screens():
            g, dpr = s.geometry(), s.devicePixelRatio()
            if abs(g.width() * dpr - mw) <= 2 and abs(g.height() * dpr - mh) <= 2:
                return s
        return QGuiApplication.primaryScreen()

    def update_position(self) -> None:
        info = taskbar.get_taskbar_info()
        if info is None or not self._pills:
            self._set_visible(False)
            return
        if taskbar.is_tucked(info.rect, info.monitor_rect, info.edge, info.autohide) \
                or taskbar.is_fullscreen_foreground(info.monitor_rect):
            self._set_visible(False)
            return
        screen = self._pick_screen(info.monitor_rect)
        dpr = screen.devicePixelRatio() or 1.0
        self._edge = info.edge
        vertical = info.edge in (taskbar.EDGE_LEFT, taskbar.EDGE_RIGHT)
        thickness = (taskbar.rect_w(info.rect) if vertical else taskbar.rect_h(info.rect)) / dpr
        ph = pill_height_for(thickness)
        run = (info.tray_rect[1] - info.rect[1]) if vertical else (info.tray_rect[0] - info.rect[0])
        limit = max(120, int(run / dpr * FREE_SPACE_SHARE))
        if ph != self._pill_h or vertical != self._vertical or limit != self._max_extent:
            self._pill_h, self._vertical, self._max_extent = ph, vertical, limit
            self._relayout()
        w_phys, h_phys = round(self.width() * dpr), round(self.height() * dpr)
        prect = taskbar.anchor_widget_rect(info, w_phys, h_phys, gap=round(4 * dpr))
        if not vertical:    # centre on the notification area's icon row, not just the bar
            ty = (info.tray_rect[1] + info.tray_rect[3]) // 2
            if info.rect[1] <= ty <= info.rect[3]:
                prect = center_rect_y(prect, ty)
        x, y, _w, _h = taskbar.physical_to_logical_rect(
            prect, info.monitor_rect[:2], (screen.geometry().x(), screen.geometry().y()), dpr)
        if self._last_geom != (x, y):
            self._last_geom = (x, y)
            self.move(x, y)
        self._set_visible(True)
        if self.isVisible():
            _assert_topmost(int(self.winId()))
            if self._popup is not None and self._popup.isVisible():
                self._popup.raise_topmost()

    def _set_visible(self, vis: bool) -> None:
        if vis and not self.isVisible():
            self.show()
        elif not vis and self.isVisible():
            self._hide_popup()
            self.hide()

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
        if idx < 0 or idx >= len(self._pills) or not self.isVisible():
            return
        pill = self._pills[idx]
        if pill.members is not None:
            content = PopupContent(tr("more_devices"), [m.a11y for m in pill.members[:10]])
        elif pill.device is None:
            parts = [t for t in pill.tooltip.split(chr(10)) if t]
            content = PopupContent(parts[0] if parts else "", parts[1:])
        else:
            content = build_popup_content(pill.device, int(self._cfg_get("low_threshold", 20)))
        if self._popup is None:
            self._popup = HoverPopup()
        self._popup.set_content(content, dark=not self._light,
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
        super().hideEvent(e)

    def closeEvent(self, e) -> None:
        self._hide_popup()
        self._timer.stop()
        self._flash_timer.stop()
        self._zguard.stop()
        super().closeEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._styled:
            self._styled = True
            _apply_ex_styles(int(self.winId()))
        _assert_topmost(int(self.winId()))

    # ------------------------------------------------------------------ painting
    def _colors(self):
        return QColor(20, 20, 20) if self._light else QColor(255, 255, 255)

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
        dark = not self._light
        fg = self._colors()
        p.setFont(self._font())
        icon = self._icon_size()
        for i, (pill, (x, y, w, h)) in enumerate(zip(self._pills, self._rects)):
            r = QRectF(x, y, w, h)
            level_color = self.pill_color(pill)
            color = self._text_color(pill, fg, dark)
            hover = i == self._hover
            if pill.alert:
                fill = pill_tint("low_hover" if hover or self._flashing(pill) else "low", dark)
            else:
                fill = pill_tint("hover" if hover else "normal", dark)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, h / 2.5, h / 2.5)
            if i == self._kfocus and self.hasFocus():     # keyboard focus ring
                pen = QPen(fg)
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), h / 2.5, h / 2.5)
            if pill.alert:      # non-colour cue for "very low": 1 px outline
                pen = QPen(color)
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), h / 2.5, h / 2.5)
            if pill.kind == "overflow":
                p.setPen(color)
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), pill.text)
                continue
            ir = QRectF(x + PAD, y + (h - icon) / 2, icon, icon)
            icon_color = fg if level_color is not None else color
            if pill.dim:
                icon_color = QColor(icon_color)
                icon_color.setAlpha(DIM_ALPHA)
            elif pill.idle:
                icon_color = QColor(icon_color)
                icon_color.setAlpha(IDLE_ALPHA)
            draw_kind_icon(p, pill.kind, ir, icon_color)
            text_x = x + PAD + icon + ICON_GAP
            if pill.idle:
                moon = QColor(fg)
                moon.setAlpha(210)
                self._draw_moon(p, QRectF(text_x - 2, y + (h - MOON_W) / 2, MOON_W, MOON_W), moon)
                text_x += MOON_W + 3
            bolt = max(6, icon // 2) if pill.charging else 0
            p.setPen(color)
            p.drawText(QRectF(text_x, y, w - (text_x - x) - PAD - bolt, h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       ("!" if pill.warn else "") + pill.text)
            if level_color is not None and pill.level is not None and h >= BAR_MIN_PILL_H:
                bw = w - 2 * PAD
                by = y + h - 5.0
                bar = QColor(color)
                if pill.idle:
                    bar.setAlpha(IDLE_ALPHA)
                track = QColor(color)
                track.setAlpha(55)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(track)
                p.drawRoundedRect(QRectF(x + PAD, by, bw, BAR_H), BAR_H / 2, BAR_H / 2)
                p.setBrush(bar)
                p.drawRoundedRect(QRectF(x + PAD, by, max(BAR_H, bw * max(0, min(100, pill.level)) / 100),
                                         BAR_H), BAR_H / 2, BAR_H / 2)
            if pill.charging:
                bh = icon * 0.8
                draw_bolt(p, QRectF(x + w - PAD - bolt, y + (h - bh) / 2, bolt, bh),
                          QColor(110, 220, 120) if not self._light else QColor(30, 150, 60))
        p.end()

    # ------------------------------------------------------------------ interaction
    def mouseMoveEvent(self, e) -> None:
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
        if e.button() == Qt.MouseButton.LeftButton:     # touch / click: show the card right away
            idx = pill_index_at(self._rects, e.position().x(), e.position().y())
            if idx >= 0:
                self._hover = idx
                self._show_popup()
                self.update()
        if e.button() == Qt.MouseButton.RightButton:
            self._open_menu(e.globalPosition().toPoint())

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
