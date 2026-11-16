"""Device panel: frameless popup listing connected / known devices with a visibility eye per row."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import custom_icons, i18n, taskbar
from .config import PANEL_SIZE_FRACTION, UI_SCALES
from .colors import DIM_ALPHA, IDLE_ALPHA, TASKBAR_BASE, band_color, section_header_color
from .icons import (about_icon, close_icon, draw_kind_icon, get_icon_style, eye_icon, gear_icon, pencil_icon, power_icon, record_icon,
                    refresh_icon, show_all_icon)
from .panel_model import PanelModel, PanelRow, section_title
from .popup import place_popup

log = logging.getLogger(__name__)

# Panel size: a FIXED window (same for the device list and every Settings page). Its height is a share of
# the available height of the screen it opens on (config panel_size: compact 0.40 / normal 0.50 / large
# 0.65, see config.PANEL_SIZE_FRACTION); its width follows the height:
#     width = clamp(height * WIDTH_PER_HEIGHT, MIN_WIDTH, MAX_WIDTH_FRACTION * available width) * ui_scale
# (never wider than WIDTH_SCREEN_LIMIT of the available width). Content that does not fit scrolls.
WIDTH_PER_HEIGHT = 0.62
MIN_WIDTH = 300
MAX_WIDTH_FRACTION = 0.4
WIDTH_SCREEN_LIMIT = 0.9
MIN_HEIGHT = 240
ICON_PX = 20
BTN_ICON_PX = 18
BTN_PX = 32               # minimum touch/click target of every button (logical px)
ROW_MARGIN_L, ROW_MARGIN_R, ROW_SPACING, ROW_BORDER = 8, 4, 8, 2
MAX_ALIAS_LEN = 48
ARM_FALLBACK_MS = 800      # if the panel is still not armed this long after show_at, retry activation
HIDE_CHECK_MS = 50        # re-check the application state this long after the focus window changed
HIDE_GRACE_S = 0.3        # a tray click this soon after the panel closed is the click that closed it

def _t(key: str, lang: str, **kw) -> str:
    return i18n.tr_in(lang, "panel_" + key if key != "show_all" else key, **kw)


def _hex(c: QColor) -> str:
    return c.name()


def panel_dimensions(avail: QRect, size: str = "normal", scale_pct: int = 100) -> tuple[int, int]:
    """(width, height) in logical px of the panel for a screen whose available area is `avail`."""
    frac = PANEL_SIZE_FRACTION.get(size, PANEL_SIZE_FRACTION["normal"])
    scale = (scale_pct if scale_pct in UI_SCALES else 100) / 100.0
    height = min(avail.height(), max(MIN_HEIGHT, round(avail.height() * frac)))
    base = max(MIN_WIDTH, min(height * WIDTH_PER_HEIGHT, avail.width() * MAX_WIDTH_FRACTION))
    width = min(round(base * scale), int(avail.width() * WIDTH_SCREEN_LIMIT))
    return width, height


def scaled_font(scale: float) -> QFont:
    """The application font at `scale` times its size (pixel or point sized)."""
    f = QFont(QGuiApplication.font())
    if f.pixelSize() > 0:
        f.setPixelSize(max(1, round(f.pixelSize() * scale)))
    else:
        f.setPointSizeF(max(1.0, f.pointSizeF() * scale))
    return f


class _ElidedLabel(QLabel):
    """Single-line label that takes the width the layout gives it and elides only what does not fit."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._full = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def _elide(self) -> None:
        w = self.width()
        text = self._full
        if w > 8 and QFontMetrics(self.font()).horizontalAdvance(text) > w:
            text = QFontMetrics(self.font()).elidedText(text, Qt.TextElideMode.ElideRight, w)
        if text != self.text():
            super().setText(text)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._elide()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.FontChange:
            self._elide()


def _kind_pixmap(kind: str, color: QColor, dpr: float, level: int | None = None,
                 charging: bool = False, key: str = "", size: int = ICON_PX) -> QPixmap:
    """Row icon; the "none" style (widget only) falls back to outline: the panel needs the kind.
    A user image (device > kind) replaces the built-in icon; the colour's alpha is its opacity."""
    style = get_icon_style()
    if style == "none":
        style = "outline"
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    rect = QRectF(0, 0, size, size)
    custom = custom_icons.file_for(kind, key)
    if not (custom and custom_icons.draw_custom_icon(p, rect, custom, color.alphaF())):
        draw_kind_icon(p, kind, rect, color, level, charging, style)
    p.end()
    return pm


def cue_prefix(row: PanelRow) -> str:
    """Non-colour cues in front of the status text."""
    cues = row.style.cues
    out = ""
    if "warn" in cues:
        out += "! "
    if "charging" in cues:
        out += "⚡ "
    if "idle" in cues:
        out += "☾ "
    return out


def level_text(row: PanelRow) -> str:
    if row.battery is None or row.stale:
        return cue_prefix(row) + row.status_text
    return f"{cue_prefix(row)}{row.battery}% · {row.status_text}"


def _dialog_open() -> bool:
    from .dialogs import dialog_open
    return dialog_open()


class DevicePanel(QFrame):
    visibility_toggled = Signal(str, bool)
    show_disconnected_toggled = Signal(bool)
    show_all_requested = Signal()
    logging_toggled = Signal(str, bool)
    alias_changed = Signal(str, str)        # visibility key, new alias ("" = back to the real name)
    open_log_requested = Signal(str)
    refresh_requested = Signal()
    settings_requested = Signal(QRect)      # global rect of the gear button; the tray answers with show_settings()
    about_requested = Signal()
    quit_requested = Signal()
    icon_change_requested = Signal(str)     # visibility key: the tray asks for an image and saves it
    icon_reset_requested = Signal(str)      # visibility key: drop that device's own custom icon

    def __init__(self, parent=None, dark: bool | None = None, show_logging: bool = False):
        # Not a Qt.Popup: the About dialog and the other windows must open above the panel without
        # closing it. "Click outside" is emulated with the application-state check below.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("DevicePanel")
        self._show_gen = 0                       # invalidates pending arm-fallback timers
        self._armed = False                      # True once the panel (or the app) has really been active
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(i18n.tr("devices"))
        self._dark = (not taskbar.taskbar_uses_light_theme()) if dark is None else bool(dark)
        self.show_logging = bool(show_logging)
        self._model: PanelModel | None = None
        self._logging_keys: frozenset[str] = frozenset()
        self._sig: tuple | None = None
        self._edit: QLineEdit | None = None      # inline alias editor while one is open
        self._edit_key = ""
        self._edit_label: QLabel | None = None
        self._edit_icon: QToolButton | None = None      # clickable icon / remove cross of the row being edited
        self._edit_clear: QToolButton | None = None
        self._row_ctx: dict[str, tuple] = {}            # key -> (row layout, icon label, row, icon colour)
        self._modal = False                             # a file dialog / message box of the tray is open
        self._nav: list[QToolButton] = []        # buttons reached with Up/Down (one per row)
        self._rows: list[list[QToolButton]] = [] # enabled buttons of each row (Left/Right)
        self.hidden_at: float | None = None      # time.monotonic() of the last hide
        self._screen_hooked = False
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)
            app.focusWindowChanged.connect(self._on_focus_window_changed)
        from . import dialogs
        dialogs.hub.opened.connect(self._on_dialog_opened)
        dialogs.hub.closed.connect(self._on_dialog_closed)
        self._buttons: dict[tuple[str, str], QToolButton] = {}
        self._content: QWidget | None = None
        self._screen_avail: QRect | None = None
        self._posted_hwnd = 0
        self._edge = "bottom"                    # screen edge the panel was opened from
        self._settings_view = None               # SettingsView, created on the first show_settings()
        self._settings_model = None
        self._size = "normal"                    # config panel_size
        self._scale_pct = 100                    # config ui_scale (percent)
        self._k = 1.0

        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setAutoFillBackground(False)
        self._show_all_enabled = False
        self._toolbar: dict[str, QToolButton] = {}
        self._toolbar_widget = self._build_toolbar()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._stack = QStackedWidget(self)       # page 0: device list, page 1: Settings
        self._stack.addWidget(self._scroll)
        lay.addWidget(self._stack, 1)
        lay.addWidget(self._toolbar_widget)
        self._apply_style()
        self._apply_scale()

    # ------------------------------------------------------------------ public API
    def set_dark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._apply_style()
            if self._settings_view is not None:
                self._settings_view.set_dark(self._dark)
            self._sig = None
            if self._model is not None:
                self.set_model(self._model, self._logging_keys)

    @property
    def panel_size(self) -> str:
        return self._size

    @property
    def ui_scale(self) -> int:
        return self._scale_pct

    def set_metrics(self, size: str, scale_pct: int) -> None:
        """Apply config panel_size / ui_scale live: fonts, rows, buttons and the window are redone."""
        size = size if size in PANEL_SIZE_FRACTION else "normal"
        scale_pct = int(scale_pct) if scale_pct in UI_SCALES else 100
        if size == self._size and scale_pct == self._scale_pct:
            return
        self._size, self._scale_pct = size, scale_pct
        self._k = scale_pct / 100.0
        self._apply_scale()
        if self._settings_view is not None:
            self._settings_view.set_scale(self._k)
        self._sig = None
        if self._model is not None and self._edit is None:
            self.set_model(self._model, self._logging_keys)
        self._refit(keep_edge=True)
        if self.isVisible() and self.in_settings:
            self._settings_view.focus_first()

    def retranslate(self) -> None:
        """The UI language changed: texts of the toolbar, the device list and the open Settings page."""
        lang = i18n.get_language()
        self.setAccessibleName(i18n.tr("devices"))
        self._refresh_toolbar(lang)
        self._sig = None
        if self._model is not None and self._edit is None:
            self.set_model(self._model, self._logging_keys)
        if self._settings_view is not None:
            self._settings_view.refresh()

    def set_model(self, model: PanelModel, logging_keys=()) -> None:
        """Show `model`; rebuilds only when something visible changed and keeps scroll/focus."""
        logging_keys = frozenset(logging_keys or ())
        lang = i18n.get_language()
        sig = (model.signature, self._dark, lang, get_icon_style(), custom_icons.maps_version(), self._scale_pct,
               self.show_logging, logging_keys if self.show_logging else frozenset())
        self._model = model
        self._logging_keys = logging_keys
        self.setAccessibleName(i18n.tr("devices"))
        if self._edit is not None:      # never rebuild under an open alias editor: applied when it closes
            return
        if sig == self._sig:
            return
        self._sig = sig
        self._rebuild(model, lang)

    def show_at(self, anchor) -> None:
        """Open the panel next to `anchor` (QPoint or QRect, global coordinates)."""
        rect = QRect(anchor, QSize(1, 1)) if isinstance(anchor, QPoint) else QRect(anchor)
        point = QPoint(anchor) if isinstance(anchor, QPoint) else rect.center()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        geo = screen.geometry()
        avail = screen.availableGeometry()
        self._screen_avail = avail
        self._fit(avail)
        c = rect.center()
        dist = {"top": c.y() - geo.top(), "bottom": geo.bottom() - c.y(),
                "left": c.x() - geo.left(), "right": geo.right() - c.x()}
        near = min(dist, key=dist.get)
        edge = {"bottom": "bottom", "top": "top", "left": "left", "right": "right"}[near]
        x, y = place_popup((rect.x(), rect.y(), rect.width(), rect.height()),
                           (self.width(), self.height()),
                           (avail.x(), avail.y(), avail.width(), avail.height()), edge=edge)
        self._edge = edge
        self.move(x, y)
        self.show()
        self.raise_()
        self._set_topmost(True)
        self.activateWindow()
        self._foreground()
        self._show_gen += 1
        gen = self._show_gen
        QTimer.singleShot(ARM_FALLBACK_MS, lambda: self._arm_fallback(gen, 0))
        if self.in_settings:
            self._settings_view.focus_first()
        else:
            first = self._nav[0] if self._nav else self
            first.setFocus(Qt.FocusReason.PopupFocusReason)

    # ------------------------------------------------------------------ settings page
    @property
    def in_settings(self) -> bool:
        return self._settings_view is not None and self._stack.currentWidget() is self._settings_view

    @property
    def settings_view(self):
        return self._settings_view

    def set_settings_model(self, model) -> None:
        """The Settings tree the gear shows (built by the tray); an open page re-reads it."""
        self._settings_model = model
        if self._settings_view is not None:
            self._settings_view.set_model(model)

    def show_settings(self, page_id: str = "root") -> None:
        """Replace the device list by the Settings page `page_id` (default: the root page)."""
        if self._settings_model is None:
            return
        if self._edit is not None:
            self._finish_edit(True)
        if self._settings_view is None:
            from .settings_view import SettingsView
            v = SettingsView(self._settings_model, self._dark, scale=self._k)
            v.leave_requested.connect(self.show_devices)
            v.layout_changed.connect(self._on_settings_layout)
            v.rebuilt.connect(self._chain_settings_tab_order)
            self._settings_view = v
            self._stack.addWidget(v)
        self._settings_view.show_page(page_id)
        self._stack.setCurrentWidget(self._settings_view)
        self._toolbar["settings"].setChecked(True)
        self._chain_settings_tab_order()
        self._refit(keep_edge=True)
        if self.isVisible():
            self._settings_view.focus_first()

    def show_devices(self) -> None:
        """Back to the device list (from any Settings page)."""
        was = self.in_settings
        if self._settings_view is not None:
            self._stack.setCurrentWidget(self._scroll)
            self._settings_view.reset()
        self._toolbar["settings"].setChecked(False)
        if was:
            self._refit(keep_edge=True)
            if self.isVisible():
                first = self._nav[0] if self._nav else self._toolbar["settings"]
                first.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh_settings(self) -> None:
        """Re-read the values shown on the open Settings page (no-op on the device list)."""
        if self.in_settings:
            self._settings_view.refresh()

    def _on_settings_layout(self) -> None:
        if self.in_settings:
            self._refit(keep_edge=True)

    def _refit(self, keep_edge: bool = False) -> None:
        self._fit(self._screen_avail or self._primary_avail(), keep_edge=keep_edge)

    def _chain_settings_tab_order(self) -> None:
        if self._settings_view is None:
            return
        order = self._settings_view.focus_order() + self._toolbar_buttons()
        for x, y in zip(order, order[1:]):
            QWidget.setTabOrder(x, y)

    # ------------------------------------------------------------------ scale
    def _s(self, base: int) -> int:
        return max(1, round(base * self._k))

    @property
    def _btn(self) -> int:
        """Side of every button: scaled, but never below the 32 px touch target."""
        return max(BTN_PX, self._s(BTN_PX))

    @property
    def _icon(self) -> int:
        return self._s(ICON_PX)

    @property
    def _bicon(self) -> int:
        return self._s(BTN_ICON_PX)

    def _apply_scale(self) -> None:
        self.setFont(scaled_font(self._k))
        for b in self._toolbar.values():
            self._size_button(b)
            b.setIconSize(QSize(self._bicon, self._bicon))
        h = self._toolbar_widget.layout()
        h.setContentsMargins(self._s(2), 0, self._s(2), 0)
        h.setSpacing(self._s(4))
        lay = self.layout()
        lay.setContentsMargins(self._s(6), self._s(6), self._s(6), self._s(6))
        lay.setSpacing(self._s(4))
        self._refresh_toolbar(i18n.get_language())

    # ------------------------------------------------------------------ appearance
    def _apply_style(self) -> None:
        dark = self._dark
        base = QColor(*TASKBAR_BASE[dark])
        text = QColor(255, 255, 255) if dark else QColor(20, 20, 20)
        edge = QColor(70, 70, 70) if dark else QColor(190, 190, 190)
        hover = "rgba(255,255,255,40)" if dark else "rgba(0,0,0,30)"
        focus = band_color("amber", dark).name()
        checked = "rgba(255,255,255,70)" if dark else "rgba(0,0,0,55)"
        self._text = text
        handle = "rgba(255,255,255,90)" if dark else "rgba(0,0,0,90)"
        self.setStyleSheet(
            f"QFrame#DevicePanel {{ background: {_hex(base)}; border: 1px solid {_hex(edge)}; "
            f"border-radius: 6px; }}"
            f"QLabel {{ background: transparent; color: {_hex(text)}; }}"
            f"QToolButton {{ background: transparent; border: 2px solid transparent; "
            f"border-radius: 5px; padding: 2px; }}"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:focus {{ border: 2px solid {focus}; }}"
            f"QToolButton#gearBtn:checked {{ background: {checked}; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {handle}; min-height: 24px; "
            f"border-radius: 4px; margin: 1px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
            f"QToolTip {{ background: {_hex(base)}; color: {_hex(text)}; "
            f"border: 1px solid {_hex(edge)}; padding: 3px; }}")
        self._refresh_toolbar(i18n.get_language())

    # ------------------------------------------------------------------ toolbar
    def _build_toolbar(self) -> QWidget:
        w = QWidget(self)
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(4)
        specs = (("refresh", self.refresh_requested.emit),
                 ("show_all", self.show_all_requested.emit),
                 ("settings", self._emit_settings),
                 ("about", self.about_requested.emit),
                 ("quit", self.quit_requested.emit))
        for name, slot in specs:
            b = QToolButton(w)
            self._size_button(b)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setIconSize(QSize(self._bicon, self._bicon))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.installEventFilter(self)
            b.clicked.connect(lambda _c=False, f=slot: f())
            self._toolbar[name] = b
            if name == "settings":
                b.setObjectName("gearBtn")
                b.setCheckable(True)            # marked while the Settings page is shown
            if name == "quit":
                h.addStretch(1)
            h.addWidget(b)
        return w

    def _emit_settings(self) -> None:
        """The gear alternates between the device list and the Settings page."""
        b = self._toolbar["settings"]
        if self.in_settings:
            self.show_devices()
            return
        self.settings_requested.emit(QRect(b.mapToGlobal(QPoint(0, 0)), b.size()))
        b.setChecked(self.in_settings)          # the tray may not have shown it

    def _refresh_toolbar(self, lang: str) -> None:
        grey = band_color("grey", self._dark)
        labels = {"refresh": i18n.tr_in(lang, "refresh"), "show_all": i18n.tr_in(lang, "show_all"),
                  "settings": i18n.tr_in(lang, "menu_settings"),
                  "about": i18n.tr_in(lang, "panel_about"), "quit": i18n.tr_in(lang, "quit")}
        for name, b in self._toolbar.items():
            b.setToolTip(labels[name])
            b.setAccessibleName(labels[name])
        t = self._toolbar
        t["show_all"].setEnabled(self._show_all_enabled)
        t["refresh"].setIcon(refresh_icon(self._bicon, self._text))
        t["show_all"].setIcon(show_all_icon(self._bicon, self._text if self._show_all_enabled else grey))
        t["settings"].setIcon(gear_icon(self._bicon, self._text))
        t["about"].setIcon(about_icon(self._bicon, self._text))
        t["quit"].setIcon(power_icon(self._bicon, self._text))

    def _toolbar_buttons(self) -> list[QToolButton]:
        return list(self._toolbar.values())

    def _size_button(self, b: QToolButton) -> None:
        b.setFixedSize(self._btn, self._btn)

    def _eye_button(self, name_text: str, checked: bool, tracked_key: tuple[str, str],
                    description: str = "", tip_on: str = "", tip_off: str = "") -> QToolButton:
        b = QToolButton(self)
        self._size_button(b)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.setIconSize(QSize(self._bicon, self._bicon))
        b.setProperty("tip_on", tip_on or name_text)
        b.setProperty("tip_off", tip_off or name_text)
        b.setToolTip(b.property("tip_on") if checked else b.property("tip_off"))
        b.setAccessibleName(name_text)
        if description:
            b.setAccessibleDescription(description)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_eye_icon(b, checked)
        b.installEventFilter(self)
        self._buttons[tracked_key] = b
        return b

    def _set_eye_icon(self, b: QToolButton, visible: bool) -> None:
        color = self._text if visible else band_color("grey", self._dark)
        b.setIcon(eye_icon(self._bicon, visible, color))

    def _header(self, kind: str, lang: str, eye: QToolButton | None) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(self._s(8), self._s(6), self._s(4), self._s(2))
        lab = QLabel(section_title(kind, lang))
        lab.setTextFormat(Qt.TextFormat.PlainText)
        f = lab.font()
        f.setBold(True)
        lab.setFont(f)
        lab.setStyleSheet(f"color: {_hex(section_header_color(kind, self._dark))}; "
                          f"background: transparent;")
        lab.setAccessibleName(section_title(kind, lang))
        h.addWidget(lab, 1)
        if eye is not None:
            h.addWidget(eye)
        return w

    def _row_widget(self, row: PanelRow, lang: str, eye_enabled: bool = True) -> QWidget:
        st = row.style
        w = QFrame()
        w.setObjectName("row")
        border = f"{ROW_BORDER}px solid {_hex(st.fg)}" if st.alert else f"{ROW_BORDER}px solid transparent"
        w.setStyleSheet(f"QFrame#row {{ background: {_hex(st.bg)}; border: {border}; "
                        f"border-radius: 6px; }}")
        desc = f"{row.name}, {level_text(row)}"
        h = QHBoxLayout(w)
        h.setContentsMargins(self._s(ROW_MARGIN_L), self._s(4), self._s(ROW_MARGIN_R), self._s(4))
        h.setSpacing(self._s(ROW_SPACING))

        icon_color = QColor(st.fg)
        if row.idle:
            icon_color.setAlpha(IDLE_ALPHA)
        elif st.dimmed:
            icon_color.setAlpha(DIM_ALPHA)
        dpr = self.devicePixelRatioF() or 1.0
        icon = QLabel()
        self._row_ctx[row.key] = (h, icon, row, icon_color)
        icon.setPixmap(_kind_pixmap(row.kind, icon_color, dpr, row.battery, row.charging, row.key, self._icon))
        icon.setFixedSize(self._icon, self._icon)
        h.addWidget(icon)

        # the text cell takes every pixel the buttons leave; the name elides only if it really does not fit
        cell = QWidget()
        cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cv = QVBoxLayout(cell)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        name = _ElidedLabel(row.name)
        if row.original_name:
            tip = row.name + "\n" + _t("original_name", lang, name=row.original_name)
            name.setToolTip(tip)
            w.setToolTip(tip)
        else:
            name.setToolTip(row.name)
        cv.addWidget(name)
        status = QLabel(level_text(row))
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.setStyleSheet(f"color: {_hex(st.fg)}; background: transparent;")
        if st.dimmed:
            f = status.font()
            f.setItalic(True)
            status.setFont(f)
        cv.addWidget(status)
        h.addWidget(cell, 1)
        avail = 0       # the alias editor takes the width of the name label

        buttons: list[QToolButton] = []
        if self.show_logging:
            on = row.key in self._logging_keys
            rec = QToolButton(self)
            self._size_button(rec)
            rec.setCheckable(True)
            rec.setChecked(on)
            rec.setAutoRaise(True)
            rec.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            rec.setIconSize(QSize(self._bicon, self._bicon))
            label = _t("record", lang, name=row.name)
            rec.setProperty("tip_on", _t("tip_record_stop", lang))
            rec.setProperty("tip_off", _t("tip_record_start", lang))
            rec.setToolTip(rec.property("tip_on") if on else rec.property("tip_off"))
            rec.setAccessibleName(label)
            rec.setAccessibleDescription(desc)
            rec.setIcon(record_icon(self._bicon, on, band_color("red", self._dark) if on
                                    else band_color("grey", self._dark)))
            rec.installEventFilter(self)
            self._buttons[(row.key, "rec")] = rec
            rec.toggled.connect(lambda v, k=row.key, b=rec: self._on_record(k, v, b))
            h.addWidget(rec)
            buttons.append(rec)
            if on:
                op = QToolButton(self)
                self._size_button(op)
                op.setText("…")
                op.setAutoRaise(True)
                op.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                lbl = _t("open_log", lang, name=row.name)
                op.setToolTip(_t("tip_open_log", lang))
                op.setAccessibleName(lbl)
                op.setAccessibleDescription(desc)
                op.installEventFilter(self)
                op.clicked.connect(lambda _=False, k=row.key: self.open_log_requested.emit(k))
                self._buttons[(row.key, "open")] = op
                h.addWidget(op)
                buttons.append(op)

        pen = QToolButton(self)
        self._size_button(pen)
        pen.setAutoRaise(True)
        pen.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        pen.setIconSize(QSize(self._bicon, self._bicon))
        pen.setToolTip(_t("tip_alias_edit" if row.original_name else "tip_alias", lang))
        pen.setAccessibleName(_t("alias", lang, name=row.name))
        pen.setAccessibleDescription(desc)
        pen.setCursor(Qt.CursorShape.PointingHandCursor)
        pen.setIcon(pencil_icon(self._bicon, self._text if row.original_name
                                else band_color("grey", self._dark)))
        pen.installEventFilter(self)
        self._buttons[(row.key, "alias")] = pen
        pen.clicked.connect(lambda _=False, r=row, c=cv, n=name, a=avail, lg=lang:
                            self._start_edit(r, c, n, a, lg))
        h.addWidget(pen)
        buttons.append(pen)

        eye = self._eye_button(_t("show_in_bar", lang, name=row.name), row.visible, (row.key, "eye"),
                               desc, tip_on=_t("tip_hide", lang), tip_off=_t("tip_show", lang))
        if not eye_enabled:
            hint = _t("known_eye_needed", lang)
            eye.setEnabled(False)
            eye.setToolTip(_t("tip_known_needed", lang))
            eye.setAccessibleDescription(f"{desc}. {hint}")
        eye.toggled.connect(lambda v, k=row.key, b=eye: self._on_eye(k, v, b))
        h.addWidget(eye)
        buttons.append(eye)
        self._add_row([b for b in buttons if b.isEnabled()], primary=eye if eye_enabled else None)
        return w

    def _add_row(self, buttons: list[QToolButton], primary: QToolButton | None = None) -> None:
        """Register the focusable buttons of one row (Left/Right inside it, Up/Down between rows)."""
        if not buttons:
            return
        self._rows.append(buttons)
        self._nav.append(primary if primary in buttons else buttons[0])

    # ------------------------------------------------------------------ build
    def _rebuild(self, model: PanelModel, lang: str) -> None:
        focus_key = next((k for k, b in self._buttons.items() if b.hasFocus()), None)
        bar = self._scroll.verticalScrollBar()
        scroll_pos = bar.value()
        old = self._scroll.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        for b in self._buttons.values():
            b.hide()
            b.deleteLater()
        self._buttons = {}
        self._nav = []
        self._rows = []
        self._row_ctx = {}

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(self._s(4))
        if not model.radio_on:
            off = QLabel(_t("radio_off", lang))
            off.setTextFormat(Qt.TextFormat.PlainText)
            off.setWordWrap(True)
            off.setContentsMargins(8, 4, 8, 2)
            off.setStyleSheet(f"color: {_hex(band_color('amber', self._dark))}; "
                              f"background: transparent;")
            off.setAccessibleName(_t("radio_off", lang))
            v.addWidget(off)
        v.addWidget(self._header("connected", lang, None))
        if model.connected:
            for r in model.connected:
                v.addWidget(self._row_widget(r, lang))
        else:
            empty = QLabel(_t("empty", lang))
            empty.setContentsMargins(8, 2, 8, 2)
            empty.setStyleSheet(f"color: {_hex(band_color('grey', self._dark))}; "
                                f"background: transparent;")
            v.addWidget(empty)
        if model.known:
            eye = self._eye_button(_t("show_known", lang), model.show_disconnected, ("", "known"),
                                   tip_on=_t("tip_hide_known", lang), tip_off=_t("tip_show_known", lang))
            eye.toggled.connect(lambda val, b=eye: self._on_known(val, b))
            self._add_row([eye])
            v.addWidget(self._header("known", lang, eye))
            for r in model.known:
                v.addWidget(self._row_widget(r, lang, eye_enabled=model.show_disconnected))
        v.addStretch(1)
        self._content = content
        self._scroll.setWidget(content)
        self.setAccessibleName(i18n.tr("devices"))

        self._show_all_enabled = any(not r.visible for r in model.connected + model.known)
        self._refresh_toolbar(lang)
        self._chain_tab_order()
        self._fit(self._screen_avail or self._primary_avail())
        bar.setValue(scroll_pos)
        if focus_key in self._buttons:
            self._buttons[focus_key].setFocus(Qt.FocusReason.OtherFocusReason)

    def _chain_tab_order(self) -> None:
        order = list(self._buttons.values())      # creation order == visual order
        order.extend(self._toolbar_buttons())
        for x, y in zip(order, order[1:]):
            QWidget.setTabOrder(x, y)

    @staticmethod
    def _primary_avail() -> QRect:
        s = QGuiApplication.primaryScreen()
        return s.availableGeometry() if s is not None else QRect(0, 0, 1920, 1040)

    def _fit(self, avail: QRect, keep_edge: bool = False) -> None:
        """Give the panel its fixed size for the screen `avail` (see panel_dimensions); the same size for
        the device list and every Settings page. With `keep_edge` a panel opened above the taskbar keeps
        its bottom edge when the size changes."""
        width, height = panel_dimensions(avail, self._size, self._scale_pct)
        old = self.geometry()
        self.setFixedSize(width, height)
        if self.isVisible():
            if keep_edge and self._edge == "bottom":
                self.move(old.x(), old.bottom() + 1 - self.height())
            g = self.geometry()
            x = max(avail.left(), min(g.x(), avail.right() - g.width() + 1))
            y = max(avail.top(), min(g.y(), avail.bottom() - g.height() + 1))
            if (x, y) != (g.x(), g.y()):
                self.move(x, y)

    # ------------------------------------------------------------------ slots
    @staticmethod
    def _set_state_tip(b: QToolButton, on: bool) -> None:
        if b.isEnabled():
            b.setToolTip(b.property("tip_on") if on else b.property("tip_off"))

    def _on_eye(self, key: str, visible: bool, b: QToolButton) -> None:
        self._set_eye_icon(b, visible)
        self._set_state_tip(b, visible)
        self.visibility_toggled.emit(key, visible)

    def _on_known(self, value: bool, b: QToolButton) -> None:
        self._set_eye_icon(b, value)
        self._set_state_tip(b, value)
        self.show_disconnected_toggled.emit(value)

    def _on_record(self, key: str, on: bool, b: QToolButton) -> None:
        self._set_state_tip(b, on)
        b.setIcon(record_icon(self._bicon, on, band_color("red", self._dark) if on
                              else band_color("grey", self._dark)))
        self.logging_toggled.emit(key, on)

    # ------------------------------------------------------------------ alias editing
    def _start_edit(self, row: PanelRow, layout: QVBoxLayout, label: QLabel, width: int, lang: str) -> None:
        if self._edit is not None:
            self._finish_edit(True)
            return
        edit = QLineEdit(self)
        edit.setMaxLength(MAX_ALIAS_LEN)
        edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        edit.setMinimumWidth(0)
        edit.setFixedHeight(max(label.height(), edit.sizeHint().height()))
        edit.setText(row.name if row.original_name else "")
        edit.setPlaceholderText(row.original_name or row.name)
        edit.setAccessibleName(_t("alias_edit", lang, name=row.original_name or row.name))
        edit.setStyleSheet(f"QLineEdit {{ background: transparent; color: {_hex(self._text)}; "
                           f"border: 1px solid {_hex(band_color('amber', self._dark))}; "
                           f"border-radius: 3px; padding: 0 2px; }}")
        edit.installEventFilter(self)
        self._edit, self._edit_key, self._edit_label = edit, row.key, label
        label.hide()
        layout.insertWidget(0, edit)
        edit.show()
        self._add_edit_extras(row, lang)
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.selectAll()

    def _finish_edit(self, save: bool) -> None:
        edit, key, label = self._edit, self._edit_key, self._edit_label
        if edit is None:
            return
        self._edit = self._edit_label = None        # first: hiding it fires FocusOut again
        text = edit.text().strip()
        edit.removeEventFilter(self)
        edit.hide()
        edit.deleteLater()
        self._remove_edit_extras(key)
        if label is not None:
            label.show()
        btn = self._buttons.get((key, "alias"))
        if btn is not None:
            btn.setFocus(Qt.FocusReason.OtherFocusReason)
        if save and text != self._current_alias(key):
            self.alias_changed.emit(key, text)
        elif self._model is not None:
            self.set_model(self._model, self._logging_keys)     # apply what was deferred

    # While the alias is edited the row icon becomes a button (pick an image for this device) and, when the
    # device has an image of its own, a small cross removes it. The tray does the import and the saving.
    def _add_edit_extras(self, row: PanelRow, lang: str) -> None:
        ctx = self._row_ctx.get(row.key)
        if ctx is None:
            return
        h, label = ctx[0], ctx[1]
        pen = self._buttons.get((row.key, "alias"))
        side = self._icon + self._s(6)
        focus = _hex(band_color("amber", self._dark))
        css = (f"QToolButton {{ background: transparent; border: 2px solid transparent; border-radius: 4px; "
               f"padding: 0; }} QToolButton:hover {{ background: rgba(128,128,128,60); }} "
               f"QToolButton:focus {{ border: 2px solid {focus}; }}")
        icon = QToolButton(self)
        icon.setFixedSize(side, side)
        icon.setIconSize(QSize(self._icon, self._icon))
        icon.setAutoRaise(True)
        icon.setStyleSheet(css)
        icon.setFocusPolicy(Qt.FocusPolicy.TabFocus)    # a mouse click must not pull the focus off the editor
        icon.setCursor(Qt.CursorShape.PointingHandCursor)
        icon.setToolTip(_t("tip_icon_change", lang))
        icon.setAccessibleName(_t("icon_change", lang, name=row.name))
        icon.installEventFilter(self)
        icon.clicked.connect(lambda _=False, k=row.key: self._request_icon(k, False))
        clear = QToolButton(self)
        clear.setFixedSize(self._bicon + self._s(4), self._bicon + self._s(4))
        clear.setIconSize(QSize(self._bicon, self._bicon))
        clear.setAutoRaise(True)
        clear.setStyleSheet(css)
        clear.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setIcon(close_icon(self._bicon, self._text))
        clear.setToolTip(_t("tip_icon_reset", lang))
        clear.setAccessibleName(_t("icon_reset", lang, name=row.name))
        clear.installEventFilter(self)
        clear.clicked.connect(lambda _=False, k=row.key: self._request_icon(k, True))
        label.hide()
        h.insertWidget(0, icon)
        h.insertWidget(1, clear)
        self._edit_icon, self._edit_clear = icon, clear
        if self._edit is not None:
            self.setTabOrder(self._edit, icon)
            self.setTabOrder(icon, clear)
            if pen is not None:
                self.setTabOrder(clear, pen)
        self._sync_edit_extras()

    def _sync_edit_extras(self) -> None:
        """Repaint the editing row's icon from the current maps; the cross shows only for an own icon."""
        icon, clear = self._edit_icon, self._edit_clear
        ctx = self._row_ctx.get(self._edit_key)
        if icon is None or clear is None or ctx is None:
            return
        row, color = ctx[2], ctx[3]
        dpr = self.devicePixelRatioF() or 1.0
        icon.setIcon(QIcon(_kind_pixmap(row.kind, color, dpr, row.battery, row.charging, row.key, self._icon)))
        own = custom_icons.device_file(row.key) is not None
        clear.setVisible(own)
        pen = self._buttons.get((row.key, "alias"))
        i = self._row_of(pen) if pen is not None else None
        if i is not None:
            buttons = self._rows[i]
            for b in (icon, clear):
                if b in buttons:
                    buttons.remove(b)
            j = buttons.index(pen)
            buttons[j:j] = [icon] + ([clear] if own else [])

    def _remove_edit_extras(self, key: str) -> None:
        icon, clear = self._edit_icon, self._edit_clear
        self._edit_icon = self._edit_clear = None
        ctx = self._row_ctx.get(key)
        for b in (icon, clear):
            if b is None:
                continue
            for buttons in self._rows:
                if b in buttons:
                    buttons.remove(b)
            b.removeEventFilter(self)
            b.hide()
            if ctx is not None:
                ctx[0].removeWidget(b)
            b.deleteLater()
        if ctx is not None and icon is not None:
            ctx[1].show()

    def _request_icon(self, key: str, reset: bool) -> None:
        """Ask the tray to pick/import (or drop) an image; the alias editor stays open with its text."""
        edit = self._edit
        if edit is None or key != self._edit_key:
            return
        self._modal = True
        try:
            (self.icon_reset_requested if reset else self.icon_change_requested).emit(key)
        finally:
            self._modal = False
        if self._edit is not edit:          # the editor was closed meanwhile
            return
        self._sync_edit_extras()
        if self.isVisible():
            self.activateWindow()
        edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _check_edit_focus(self) -> None:
        """Focus left the editor by keyboard: the edit ends unless it went to the row's icon buttons."""
        if self._edit is None or self._modal:
            return
        fw = QApplication.focusWidget()
        if fw is not None and fw in (self._edit, self._edit_icon, self._edit_clear):
            return
        self._finish_edit(True)

    def _current_alias(self, key: str) -> str:
        m = self._model
        if m is None:
            return ""
        for r in m.connected + m.known:
            if r.key == key:
                return r.name if r.original_name else ""
        return ""

    # ------------------------------------------------------------------ keyboard
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self._edit and obj is not None:
            if ev.type() == QEvent.Type.KeyPress:
                if ev.key() == Qt.Key.Key_Escape:
                    self._finish_edit(False)
                    return True
                if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._finish_edit(True)
                    return True
            elif ev.type() == QEvent.Type.FocusOut:
                reason = ev.reason()
                if self._modal or reason == Qt.FocusReason.PopupFocusReason:
                    return False        # file dialog / context menu of the line edit: keep editing
                if reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
                    QTimer.singleShot(0, self._check_edit_focus)    # may just be going to the icon buttons
                    return False
                self._finish_edit(True)
            return False
        if self._edit is not None and obj is not None and obj in (self._edit_icon, self._edit_clear):
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                self._finish_edit(False)
                return True
            if ev.type() == QEvent.Type.FocusOut:
                if not self._modal and ev.reason() != Qt.FocusReason.PopupFocusReason:
                    QTimer.singleShot(0, self._check_edit_focus)
        if ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if self.in_settings and (key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace)
                                     or (key == Qt.Key.Key_Left
                                         and ev.modifiers() & Qt.KeyboardModifier.AltModifier)):
                self._settings_view.go_back()       # one level up; from the root page: the device list
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
            tb = self._toolbar_buttons()
            if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and isinstance(obj, QToolButton)
                    and obj.isEnabled()):
                obj.click()
                return True
            if obj in tb:
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    live = [b for b in tb if b.isEnabled()]
                    j = max(0, min(len(live) - 1, live.index(obj) + (-1 if key == Qt.Key.Key_Left else 1)))
                    live[j].setFocus(Qt.FocusReason.TabFocusReason)
                    return True
                if key == Qt.Key.Key_Up and self.in_settings:
                    self._settings_view.focus_last()
                    return True
                if key == Qt.Key.Key_Up and self._nav:
                    self._nav[-1].setFocus(Qt.FocusReason.TabFocusReason)
                    self._scroll.ensureWidgetVisible(self._nav[-1])
                    return True
                if key == Qt.Key.Key_Down:
                    return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._nav:
                self._move_focus(obj, -1 if key == Qt.Key.Key_Up else 1)
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._rows:
                self._move_in_row(obj, -1 if key == Qt.Key.Key_Left else 1)
                return True
        return super().eventFilter(obj, ev)

    def _move_focus(self, obj: QObject, step: int) -> None:
        cur = self._row_index(obj)
        idx = 0 if cur is None else max(0, min(len(self._nav) - 1, cur + step))
        target = self._nav[idx]
        target.setFocus(Qt.FocusReason.TabFocusReason)
        self._scroll.ensureWidgetVisible(target)

    def _row_of(self, obj: QObject) -> int | None:
        for i, buttons in enumerate(self._rows):
            if obj in buttons:
                return i
        return None

    def _row_index(self, obj: QObject) -> int | None:
        """Index into `_nav` of the row that holds `obj` (any of its buttons)."""
        i = self._row_of(obj)
        if i is None:
            return None
        target = self._rows[i]
        for n, b in enumerate(self._nav):
            if b in target:
                return n
        return None

    def _move_in_row(self, obj: QObject, step: int) -> None:
        i = self._row_of(obj)
        if i is None:
            return
        buttons = self._rows[i]
        j = max(0, min(len(buttons) - 1, buttons.index(obj) + step))
        buttons[j].setFocus(Qt.FocusReason.TabFocusReason)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            if self.in_settings:
                self._settings_view.go_back()
            else:
                self.close()
            return
        if self.in_settings:
            super().keyPressEvent(ev)
            return
        if ev.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._nav:
            self._move_focus(self.focusWidget() or self, -1 if ev.key() == Qt.Key.Key_Up else 1)
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ click-outside trick
    def _foreground(self) -> None:
        try:
            from .widget import _foreground
            self._posted_hwnd = int(self.winId())
            _foreground(self._posted_hwnd)
        except Exception:
            log.debug("could not foreground the device panel", exc_info=True)

    # The panel closes when the whole application stops being active (click on the desktop, the
    # taskbar or another program). Focus moving to another window of this app (About dialog, stats
    # window) keeps the application Active: the panel stays open but loses its topmost band so that
    # window is drawn in front of it; it becomes topmost again when the panel is re-shown/re-activated.
    def _app_active(self) -> bool:
        return QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive

    def _set_topmost(self, on: bool) -> None:
        """Toggle the Win32 topmost band without touching Qt flags (that would recreate the window)."""
        try:
            from .win_window import _assert_topmost, _drop_topmost
            hwnd = int(self.winId())
            (_assert_topmost if on else _drop_topmost)(hwnd)
        except Exception:
            log.debug("could not change the topmost state of the device panel", exc_info=True)

    def _note_deactivation(self) -> None:
        # measured from the moment the loss of activation is seen (before HIDE_CHECK_MS), so a slightly
        # slow click on the tray icon is still recognised as the click that closed the panel
        self.hidden_at = time.monotonic()

    # ---- application dialogs (see dialogs.py): the panel must survive a native dialog taking the focus
    def _on_dialog_opened(self) -> None:
        if self.isVisible():
            self._set_topmost(False)        # a native dialog is not a QWindow: lower the band explicitly

    def _on_dialog_closed(self) -> None:
        if self.isVisible():
            QTimer.singleShot(HIDE_CHECK_MS, self._after_dialog)

    def _after_dialog(self) -> None:
        from . import dialogs
        if not self.isVisible() or dialogs.dialog_open():
            return
        if self._app_active():
            self._armed = True
            self._set_topmost(True)
            self.activateWindow()
        else:
            self._note_deactivation()
            self.hide()

    def _on_app_state_changed(self, state) -> None:
        if not self.isVisible():
            return
        if state == Qt.ApplicationState.ApplicationActive:
            self._armed = True
        elif self._armed and not _dialog_open():
            self._note_deactivation()
            QTimer.singleShot(0, self._hide_if_inactive)

    def _on_focus_window_changed(self, w=None) -> None:
        if not self.isVisible():
            return
        if self._app_active():
            self._armed = True
            if w is not None:
                if w is self.windowHandle():
                    self._set_topmost(True)
                elif (w.flags() & Qt.WindowType.WindowType_Mask) != Qt.WindowType.Popup:
                    # another normal window of the app (About, stats): let it be drawn above the panel
                    self._set_topmost(False)
        elif self._armed and not _dialog_open():
            self._note_deactivation()
            QTimer.singleShot(HIDE_CHECK_MS, self._hide_if_inactive)

    def _arm_fallback(self, gen: int, stage: int) -> None:
        """If SetForegroundWindow was denied the activation never arrives: retry, then arm anyway."""
        if gen != self._show_gen or not self.isVisible() or self._armed:
            return
        if stage == 0:
            self.activateWindow()
            self._foreground()
            QTimer.singleShot(ARM_FALLBACK_MS, lambda: self._arm_fallback(gen, 1))
            return
        self._armed = True
        if not self._app_active() and not _dialog_open():
            self._note_deactivation()
            self.hide()

    def _hide_if_inactive(self) -> None:
        # re-check: a focus handoff between two windows of the app can pass through "no focus window"
        if self.isVisible() and self._armed and not self._app_active() and not _dialog_open():
            self.hide()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._armed = True
            if self.isVisible():
                self._set_topmost(True)

    def showEvent(self, ev) -> None:
        self._armed = self._app_active()       # a late activation (see _foreground) arms it afterwards
        super().showEvent(ev)
        wh = self.windowHandle()
        if wh is not None and not self._screen_hooked:
            self._screen_hooked = True
            wh.screenChanged.connect(self._on_screen_changed)
        self._on_screen_changed()       # icons are drawn for the screen the panel is on now

    def _on_screen_changed(self, *_a) -> None:
        """Icons are pixmaps at the current devicePixelRatio: drop the cached signature and rebuild."""
        screen = self.screen() if _a else None
        if screen is not None and self.isVisible():
            avail = screen.availableGeometry()
            if avail != self._screen_avail:      # moved to another monitor / DPI: reshape for it
                self._screen_avail = avail
                self._refit(keep_edge=False)
        self._sig = None
        if self._model is not None:
            self.set_model(self._model, self._logging_keys)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        if self._edit is not None:      # never leave an alias editor open on a hidden panel (it blocks set_model)
            self._finish_edit(True)
        self.hidden_at = time.monotonic()
        if self.in_settings:        # the next opening shows the device list again
            self.show_devices()
        hwnd, self._posted_hwnd = self._posted_hwnd, 0
        if hwnd:
            try:
                from .widget import _post_null
                _post_null(hwnd)
            except Exception:
                pass
