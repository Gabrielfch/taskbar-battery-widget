"""Device panel: frameless popup listing connected / known devices with a visibility eye per row."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from . import i18n, taskbar
from .colors import DIM_ALPHA, IDLE_ALPHA, TASKBAR_BASE, band_color, section_header_color
from .icons import draw_kind_icon, eye_icon, record_icon
from .panel_model import PanelModel, PanelRow, section_title
from .popup import place_popup

log = logging.getLogger(__name__)

MAX_HEIGHT_FRACTION = 0.6
PANEL_WIDTH = 320          # content width, without the scrollbar
ICON_PX = 20
BTN_ICON_PX = 18
BTN_PX = 32               # minimum touch/click target of every button (logical px)
ROW_MARGIN_L, ROW_MARGIN_R, ROW_SPACING, ROW_BORDER = 8, 4, 8, 2
HIDE_GRACE_S = 0.3         # a tray click this soon after the panel closed is the click that closed it

def _t(key: str, lang: str, **kw) -> str:
    return i18n.tr_in(lang, "panel_" + key if key != "show_all" else key, **kw)


def _hex(c: QColor) -> str:
    return c.name()


def _kind_pixmap(kind: str, color: QColor, dpr: float) -> QPixmap:
    px = int(ICON_PX * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    draw_kind_icon(p, kind, QRectF(0, 0, ICON_PX, ICON_PX), color)
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


class DevicePanel(QFrame):
    visibility_toggled = Signal(str, bool)
    show_disconnected_toggled = Signal(bool)
    show_all_requested = Signal()
    logging_toggled = Signal(str, bool)
    open_log_requested = Signal(str)

    def __init__(self, parent=None, dark: bool | None = None, show_logging: bool = False):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("DevicePanel")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(i18n.tr("devices"))
        self._dark = (not taskbar.taskbar_uses_light_theme()) if dark is None else bool(dark)
        self.show_logging = bool(show_logging)
        self._model: PanelModel | None = None
        self._logging_keys: frozenset[str] = frozenset()
        self._sig: tuple | None = None
        self._nav: list[QToolButton] = []        # buttons reached with Up/Down (one per row)
        self._rows: list[list[QToolButton]] = [] # enabled buttons of each row (Left/Right)
        self.hidden_at: float | None = None      # time.monotonic() of the last hide
        self._screen_hooked = False
        self._buttons: dict[tuple[str, str], QToolButton] = {}
        self._content: QWidget | None = None
        self._screen_avail: QRect | None = None
        self._posted_hwnd = 0

        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setAutoFillBackground(False)
        self._footer = QPushButton(self)
        self._footer.clicked.connect(self.show_all_requested)
        self._footer.installEventFilter(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(self._scroll)
        lay.addWidget(self._footer)
        self._apply_style()

    # ------------------------------------------------------------------ public API
    def set_dark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._apply_style()
            self._sig = None
            if self._model is not None:
                self.set_model(self._model, self._logging_keys)

    def set_model(self, model: PanelModel, logging_keys=()) -> None:
        """Show `model`; rebuilds only when something visible changed and keeps scroll/focus."""
        logging_keys = frozenset(logging_keys or ())
        lang = i18n.get_language()
        sig = (model.signature, self._dark, lang, self.show_logging,
               logging_keys if self.show_logging else frozenset())
        self._model = model
        self._logging_keys = logging_keys
        self.setAccessibleName(i18n.tr("devices"))
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
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._foreground()
        first = self._nav[0] if self._nav else self
        first.setFocus(Qt.FocusReason.PopupFocusReason)

    # ------------------------------------------------------------------ appearance
    def _apply_style(self) -> None:
        dark = self._dark
        base = QColor(*TASKBAR_BASE[dark])
        text = QColor(255, 255, 255) if dark else QColor(20, 20, 20)
        edge = QColor(70, 70, 70) if dark else QColor(190, 190, 190)
        hover = "rgba(255,255,255,40)" if dark else "rgba(0,0,0,30)"
        focus = band_color("amber", dark).name()
        self._text = text
        handle = "rgba(255,255,255,90)" if dark else "rgba(0,0,0,90)"
        self.setStyleSheet(
            f"QFrame#DevicePanel {{ background: {_hex(base)}; border: 1px solid {_hex(edge)}; "
            f"border-radius: 6px; }}"
            f"QLabel {{ background: transparent; color: {_hex(text)}; }}"
            f"QToolButton {{ background: transparent; border: 2px solid transparent; "
            f"border-radius: 5px; padding: 2px; }}"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:focus, QPushButton:focus {{ border: 2px solid {focus}; }}"
            f"QPushButton {{ background: transparent; color: {_hex(text)}; border: 2px solid "
            f"transparent; border-radius: 5px; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QPushButton:disabled {{ color: {_hex(band_color('grey', dark))}; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {handle}; min-height: 24px; "
            f"border-radius: 4px; margin: 1px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
            f"QToolTip {{ background: {_hex(base)}; color: {_hex(text)}; "
            f"border: 1px solid {_hex(edge)}; padding: 3px; }}")

    @staticmethod
    def _size_button(b: QToolButton) -> None:
        b.setFixedSize(BTN_PX, BTN_PX)

    def _eye_button(self, name_text: str, checked: bool, tracked_key: tuple[str, str],
                    description: str = "") -> QToolButton:
        b = QToolButton(self)
        self._size_button(b)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.setIconSize(QSize(BTN_ICON_PX, BTN_ICON_PX))
        b.setToolTip(name_text)
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
        b.setIcon(eye_icon(BTN_ICON_PX, visible, color))

    def _header(self, kind: str, lang: str, eye: QToolButton | None) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 6, 4, 2)
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
        h.setContentsMargins(ROW_MARGIN_L, 4, ROW_MARGIN_R, 4)
        h.setSpacing(ROW_SPACING)

        icon_color = QColor(st.fg)
        if row.idle:
            icon_color.setAlpha(IDLE_ALPHA)
        elif st.dimmed:
            icon_color.setAlpha(DIM_ALPHA)
        dpr = self.devicePixelRatioF() or 1.0
        icon = QLabel()
        icon.setPixmap(_kind_pixmap(row.kind, icon_color, dpr))
        icon.setFixedSize(ICON_PX, ICON_PX)
        h.addWidget(icon)

        n_btns = (1 + (1 if self.show_logging else 0)
                  + (1 if self.show_logging and row.key in self._logging_keys else 0))
        avail = (PANEL_WIDTH - 2 * ROW_BORDER - ROW_MARGIN_L - ROW_MARGIN_R - ICON_PX
                 - BTN_PX * n_btns - ROW_SPACING * (n_btns + 1) - 2)

        cell = QWidget()
        cell.setFixedWidth(avail)
        cv = QVBoxLayout(cell)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        name = QLabel()
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setFixedWidth(avail)
        name.setText(QFontMetrics(name.font()).elidedText(row.name, Qt.TextElideMode.ElideRight, avail))
        name.setToolTip(row.name)
        cv.addWidget(name)
        status = QLabel(level_text(row))
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        status.setStyleSheet(f"color: {_hex(st.fg)}; background: transparent;")
        if st.dimmed:
            f = status.font()
            f.setItalic(True)
            status.setFont(f)
        status.setFixedWidth(avail)
        need = QFontMetrics(status.font()).boundingRect(
            0, 0, avail, 10000, int(Qt.TextFlag.TextWordWrap), status.text()).height()
        status.setFixedHeight(need + 2)
        cv.addWidget(status)
        h.addWidget(cell)

        buttons: list[QToolButton] = []
        if self.show_logging:
            on = row.key in self._logging_keys
            rec = QToolButton(self)
            self._size_button(rec)
            rec.setCheckable(True)
            rec.setChecked(on)
            rec.setAutoRaise(True)
            rec.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            rec.setIconSize(QSize(BTN_ICON_PX, BTN_ICON_PX))
            label = _t("record", lang, name=row.name)
            rec.setToolTip(label)
            rec.setAccessibleName(label)
            rec.setAccessibleDescription(desc)
            rec.setIcon(record_icon(BTN_ICON_PX, on, band_color("red", self._dark) if on
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
                op.setToolTip(lbl)
                op.setAccessibleName(lbl)
                op.setAccessibleDescription(desc)
                op.installEventFilter(self)
                op.clicked.connect(lambda _=False, k=row.key: self.open_log_requested.emit(k))
                self._buttons[(row.key, "open")] = op
                h.addWidget(op)
                buttons.append(op)

        eye = self._eye_button(_t("show_in_bar", lang, name=row.name), row.visible, (row.key, "eye"),
                               desc)
        if not eye_enabled:
            hint = _t("known_eye_needed", lang)
            eye.setEnabled(False)
            eye.setToolTip(hint)
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

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
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
            eye = self._eye_button(_t("show_known", lang), model.show_disconnected, ("", "known"))
            eye.toggled.connect(lambda val, b=eye: self._on_known(val, b))
            self._add_row([eye])
            v.addWidget(self._header("known", lang, eye))
            for r in model.known:
                v.addWidget(self._row_widget(r, lang, eye_enabled=model.show_disconnected))
        v.addStretch(1)
        content.setFixedWidth(PANEL_WIDTH)
        self._content = content
        self._scroll.setWidget(content)
        self.setAccessibleName(i18n.tr("devices"))

        self._footer.setText(_t("show_all", lang))
        self._footer.setAccessibleName(_t("show_all", lang))
        self._footer.setEnabled(any(not r.visible for r in model.connected + model.known))
        self._chain_tab_order()
        self._fit(self._screen_avail or self._primary_avail())
        bar.setValue(scroll_pos)
        if focus_key in self._buttons:
            self._buttons[focus_key].setFocus(Qt.FocusReason.OtherFocusReason)

    def _chain_tab_order(self) -> None:
        order = list(self._buttons.values())      # creation order == visual order
        order.append(self._footer)
        for x, y in zip(order, order[1:]):
            QWidget.setTabOrder(x, y)

    @staticmethod
    def _primary_avail() -> QRect:
        s = QGuiApplication.primaryScreen()
        return s.availableGeometry() if s is not None else QRect(0, 0, 1920, 1040)

    def _fit(self, avail: QRect) -> None:
        """Size the panel: content height capped at 60 % of the screen's available height."""
        if self._content is None:
            return
        self._content.adjustSize()
        want = self._content.sizeHint().height()
        margins = self.layout().contentsMargins()
        extra = (margins.top() + margins.bottom() + self.layout().spacing()
                 + self._footer.sizeHint().height() + 2)
        cap = max(80, int(avail.height() * MAX_HEIGHT_FRACTION) - extra)
        height = min(want, cap)
        sb = self._scroll.verticalScrollBar().sizeHint().width() if want > cap else 0
        self._scroll.setFixedSize(PANEL_WIDTH + sb, height)
        self.setFixedSize(PANEL_WIDTH + sb + margins.left() + margins.right() + 2, height + extra)
        if self.isVisible():
            g = self.geometry()
            x = max(avail.left(), min(g.x(), avail.right() - g.width() + 1))
            y = max(avail.top(), min(g.y(), avail.bottom() - g.height() + 1))
            if (x, y) != (g.x(), g.y()):
                self.move(x, y)

    # ------------------------------------------------------------------ slots
    def _on_eye(self, key: str, visible: bool, b: QToolButton) -> None:
        self._set_eye_icon(b, visible)
        self.visibility_toggled.emit(key, visible)

    def _on_known(self, value: bool, b: QToolButton) -> None:
        self._set_eye_icon(b, value)
        self.show_disconnected_toggled.emit(value)

    def _on_record(self, key: str, on: bool, b: QToolButton) -> None:
        b.setIcon(record_icon(BTN_ICON_PX, on, band_color("red", self._dark) if on
                              else band_color("grey", self._dark)))
        self.logging_toggled.emit(key, on)

    # ------------------------------------------------------------------ keyboard
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key == Qt.Key.Key_Escape:
                self.close()
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
            self.close()
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

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        wh = self.windowHandle()
        if wh is not None and not self._screen_hooked:
            self._screen_hooked = True
            wh.screenChanged.connect(self._on_screen_changed)
        self._on_screen_changed()       # icons are drawn for the screen the panel is on now

    def _on_screen_changed(self, *_a) -> None:
        """Icons are pixmaps at the current devicePixelRatio: drop the cached signature and rebuild."""
        self._sig = None
        if self._model is not None:
            self.set_model(self._model, self._logging_keys)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self.hidden_at = time.monotonic()
        hwnd, self._posted_hwnd = self._posted_hwnd, 0
        if hwnd:
            try:
                from .widget import _post_null
                _post_null(hwnd)
            except Exception:
                pass
