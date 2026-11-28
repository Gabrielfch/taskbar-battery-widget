"""Settings page rendered inside the device panel: navigation stack, rows and accordion choices.

The content comes from settings_model (pure data); this module only draws it and forwards the
user's actions to the model's callbacks. Everything is re-read from the model after each action.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import i18n
from .colors import band_color
from .icons import draw_kind_icon, glyph_icon
from .settings_model import Action, Choice, Header, Page, Separator, SettingsModel, Toggle

log = logging.getLogger(__name__)

# Base metrics at 100 %; every length goes through `scaled()` (the UI scale of the panel).
ROW_H = 36                 # >= the 32 px touch target of the panel buttons
BTN_PX = 32
GLYPH_PX = 18
TRAIL_PX = 16
LEAD_W = 28                # leading slot: glyph / checkbox / radio
INSET_PX = 18              # accordion options are indented
MIN_TARGET = 32            # touch targets never go below this (logical px), also at 90 %
SWATCH_PX = 10             # colour dots of a palette preview
PREVIEW_KIND = "mouse"     # device drawn in the icon style previews
TRAIL_SEP = " \u203a "      # "Personalization > Position" in search results


def scaled(base: int, k: float, floor: int = 1) -> int:
    return max(floor, round(base * k))


@dataclass(frozen=True)
class _Colors:
    text: QColor
    dim: QColor
    hover: QColor
    inset: QColor
    focus: QColor
    dark: bool = True

    @staticmethod
    def make(dark: bool) -> "_Colors":
        text = QColor(255, 255, 255) if dark else QColor(20, 20, 20)
        return _Colors(text=text, dim=band_color("grey", dark),
                       hover=QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 30),
                       inset=QColor(255, 255, 255, 14) if dark else QColor(0, 0, 0, 12),
                       focus=band_color("amber", dark), dark=bool(dark))


def _rgba(c: QColor) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


class _Row(QAbstractButton):
    """One full-width settings row: [glyph | checkbox | radio] text ... [value] [chevron]."""

    def __init__(self, rid: str, kind: str, text: str, colors: _Colors, *, lead: QIcon | None = None,
                 trail: QIcon | None = None, value: str = "", indicator: str = "", inset: bool = False,
                 scale: float = 1.0, preview: tuple | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._k = float(scale)
        self.rid = rid
        self.kind = kind                    # "page" | "toggle" | "choice" | "option" | "action"
        self._label = text
        self._value = value
        self._lead = lead
        self._trail = trail
        self._indicator = indicator         # "" | "check" | "radio"
        self._inset = inset
        self._preview = preview             # ("swatch", [(dark rgb, light rgb)]) | ("icon", style) | None
        self._c = colors
        self._ring = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(scaled(ROW_H, self._k, MIN_TARGET))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCheckable(indicator != "")
        self.setText(text)

    def sizeHint(self) -> QSize:
        return QSize(200, self.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.height())

    def value_text(self) -> str:
        return self._value

    def focusInEvent(self, ev) -> None:
        # the focus ring is for keyboard / Tab focus, not for a mouse click
        self._ring = ev.reason() != Qt.FocusReason.MouseFocusReason
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:
        self._ring = False
        super().focusOutEvent(ev)

    def paintEvent(self, _ev) -> None:
        c = self._c
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        if self._inset:
            p.setBrush(c.inset)
            p.drawRoundedRect(r, 5, 5)
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            p.setBrush(c.hover)
            p.drawRoundedRect(r, 5, 5)
        if self._ring and self.hasFocus():
            p.setPen(QPen(c.focus, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)
        fg = c.text if self.isEnabled() else c.dim
        h = self.height()
        k = self._k
        glyph, trail_px = scaled(GLYPH_PX, k), scaled(TRAIL_PX, k)
        x = scaled(12, k) + (scaled(INSET_PX, k) if self._inset else 0)
        lead = QRectF(x, (h - glyph) / 2, glyph, glyph)
        if self._indicator == "check":
            self._paint_check(p, lead, fg)
        elif self._indicator == "radio":
            self._paint_radio(p, lead, fg)
        elif self._lead is not None:
            self._lead.paint(p, lead.toRect(), Qt.AlignmentFlag.AlignCenter,
                             QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled)
        x += scaled(LEAD_W, k)
        right = self.width() - scaled(10, k)
        if self._trail is not None:
            self._trail.paint(p, QRect(right - trail_px, (h - trail_px) // 2, trail_px, trail_px))
            right -= trail_px + 4
        if self._preview:
            right = self._paint_preview(p, right, h, fg)
        fm = QFontMetrics(self.font())
        if self._value:
            vw = min(fm.horizontalAdvance(self._value), int((right - x) * 0.5))
            p.setPen(c.dim)
            p.drawText(QRect(right - vw, 0, vw, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       fm.elidedText(self._value, Qt.TextElideMode.ElideRight, vw))
            right -= vw + 8
        p.setPen(fg)
        width = max(10, right - x)
        p.drawText(QRect(x, 0, width, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   fm.elidedText(self._label, Qt.TextElideMode.ElideRight, width))
        p.end()

    def _paint_preview(self, p: QPainter, right: int, h: int, fg: QColor) -> int:
        """Draw the option's sample right-aligned before `right`; returns the new right edge."""
        kind, data = self._preview
        k = self._k
        if kind == "swatch" and data:
            d = scaled(SWATCH_PX, k)
            gap = scaled(4, k)
            x = right - len(data) * d - (len(data) - 1) * gap
            edge = QColor(self._c.text)
            edge.setAlpha(70)
            p.setPen(QPen(edge, 1.0))
            for rgb in data:
                col = QColor(*rgb[0 if self._c.dark else 1])
                p.setBrush(col)
                p.drawEllipse(QRectF(x, (h - d) / 2, d, d))
                x += d + gap
            return right - len(data) * d - (len(data) - 1) * gap - scaled(10, k)
        if kind == "icon" and data and data != "none":
            g = scaled(GLYPH_PX, k)
            rect = QRectF(right - g, (h - g) / 2, g, g)
            try:
                draw_kind_icon(p, PREVIEW_KIND, rect, fg, 80, False, data)
            except Exception:  # noqa: BLE001 - a preview must never break the row
                return right
            return right - g - scaled(10, k)
        return right

    def _paint_check(self, p: QPainter, lead: QRectF, fg: QColor) -> None:
        box = lead.adjusted(1, 1, -1, -1)
        p.setPen(QPen(fg, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 3, 3)
        if self.isChecked():
            pen = QPen(fg, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(box.left() + 3.5, box.center().y() + 0.5))
            path.lineTo(box.left() + 6.8, box.bottom() - 4.2)
            path.lineTo(box.right() - 3.2, box.top() + 4.2)
            p.drawPath(path)

    def _paint_radio(self, p: QPainter, lead: QRectF, fg: QColor) -> None:
        p.setPen(QPen(fg, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(lead.adjusted(1, 1, -1, -1))
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fg)
            p.drawEllipse(lead.center(), 4.0, 4.0)


class SettingsView(QWidget):
    """Back-arrow header plus the rows of the current page; owns the navigation stack."""

    leave_requested = Signal()      # Back pressed on the root page: return to the device list
    layout_changed = Signal()       # the page height may have changed: the panel re-fits itself
    page_changed = Signal(str)      # id of the page now shown
    rebuilt = Signal()              # the rows were recreated (tab order must be chained again)

    def __init__(self, model: SettingsModel, dark: bool = True, parent: QWidget | None = None,
                 scale: float = 1.0):
        super().__init__(parent)
        self.setObjectName("SettingsView")
        self._k = float(scale)
        self._model = model
        self._dark = bool(dark)
        self._colors = _Colors.make(self._dark)
        self._path: list[Page] = [model.root]
        self._direct = False                    # opened from outside Settings: Back leaves to the device list
        self._open: str | None = None           # id of the Choice whose accordion is open
        self._rows: dict[str, _Row] = {}
        self._order: list = []                      # focus order: back button, search field, then the rows
        self._query = ""                            # settings search (root page / a page opened directly)
        self._body: QWidget | None = None
        self._last_height = 0

        self._back = QToolButton(self)
        self._back.setAutoRaise(True)
        self._back.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._size_back()
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._back.clicked.connect(lambda _c=False: self.go_back())
        self._back.installEventFilter(self)
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        f = self._title.font()
        f.setBold(True)
        self._title.setFont(f)
        self._header = QWidget(self)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(2, 0, 8, 0)
        hl.setSpacing(6)
        hl.addWidget(self._back)
        hl.addWidget(self._title, 1)

        self._search = QLineEdit(self)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_query)
        self._search.installEventFilter(self)
        self._search_action = None
        self._search_style_key = None

        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setAutoFillBackground(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self._header)
        lay.addWidget(self._search)
        lay.addWidget(self._scroll)
        self._lay = lay
        self._rebuild()

    # ------------------------------------------------------------------ public API
    @property
    def model(self) -> SettingsModel:
        return self._model

    def set_model(self, model: SettingsModel) -> None:
        if model is not self._model:
            self._model = model
            ids = [pg.id for pg in self._path]
            self._path = model.path_to(ids[-1]) if ids else [model.root]
            if self._direct and ids and ids[0] in [pg.id for pg in self._path]:
                self._path = self._path[[pg.id for pg in self._path].index(ids[0]):]
            self._open = None
        self._rebuild()

    def _size_back(self) -> None:
        btn = scaled(BTN_PX, self._k, MIN_TARGET)
        self._back.setFixedSize(btn, btn)
        g = scaled(GLYPH_PX, self._k)
        self._back.setIconSize(QSize(g, g))

    @property
    def scale(self) -> float:
        return self._k

    def set_scale(self, k: float) -> None:
        """New interface scale (the panel sets the matching font on itself); rows are rebuilt."""
        if abs(float(k) - self._k) > 1e-6:
            self._k = float(k)
            self._size_back()
            self._rebuild()

    def set_dark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._colors = _Colors.make(self._dark)
            self._rebuild()

    @property
    def page_id(self) -> str:
        return self._path[-1].id

    @property
    def path_ids(self) -> list[str]:
        return [pg.id for pg in self._path]

    @property
    def open_choice(self) -> str | None:
        return self._open

    def row(self, rid: str) -> _Row | None:
        return self._rows.get(rid)

    def rows(self) -> dict[str, _Row]:
        return dict(self._rows)

    def back_button(self) -> QToolButton:
        return self._back

    def search_field(self) -> QLineEdit:
        return self._search

    @property
    def query(self) -> str:
        return self._query

    def reveal(self, node_id: str) -> None:
        """Open the page that holds `node_id`, unfold it when it is a choice and give it the focus."""
        node = self._model.find(node_id)
        if node is None:
            return
        page = node if isinstance(node, Page) else self._model.parent_of(node_id)
        if page is None:
            return
        self._path = self._model.path_to(page.id)
        self._direct = False
        self._open = node_id if isinstance(node, Choice) else None
        self._set_query("")
        target = "<none>" if isinstance(node, Page) else node_id
        self._rebuild(focus=target)
        if target in self._rows:
            row = self._rows[target]
            row.setFocus(Qt.FocusReason.TabFocusReason)     # keyboard ring: show where the setting is
            self._scroll.ensureWidgetVisible(row)
        else:
            self.focus_first()
        self.layout_changed.emit()
        self.page_changed.emit(self.page_id)

    def title_text(self) -> str:
        return self._title.text()

    def reset(self) -> None:
        """Back to the root page with every accordion folded (used when the panel closes)."""
        self._path = [self._model.root]
        self._direct = False
        self._open = None
        self._set_query("")
        self._rebuild()

    def show_page(self, page_id: str = "root", direct: bool = False) -> None:
        """Show a page. With `direct` the navigation stack holds only that page, so Back leaves to the device
        list (the page was opened from outside Settings, e.g. the Personalization toolbar button)."""
        self._path = self._model.path_to(page_id)
        self._direct = bool(direct)
        if direct and len(self._path) > 1:
            self._path = self._path[-1:]
        self._open = None
        self._set_query("")
        self._rebuild()
        self.layout_changed.emit()
        self.page_changed.emit(self.page_id)

    def go_back(self) -> bool:
        """One level up; from the root page ask the panel to return to the device list."""
        if self._query:             # Back first leaves the search results
            self._set_query("")
            self._rebuild(focus="<search>")
            self._search.setFocus(Qt.FocusReason.OtherFocusReason)
            self.layout_changed.emit()
            return True
        if len(self._path) <= 1:
            self.leave_requested.emit()
            return False
        left = self._path.pop()
        self._open = None
        self._rebuild(focus=left.id)
        self.layout_changed.emit()
        self.page_changed.emit(self.page_id)
        return True

    def refresh(self) -> None:
        """Re-read every value from the model (config changed elsewhere, an action finished)."""
        focus = self._focused_rid()
        self._rebuild(focus=focus)
        if self._height() != self._last_height:
            self.layout_changed.emit()

    def content_height(self) -> int:
        return self._height()

    def header_height(self) -> int:
        return self._header.sizeHint().height()

    def focus_order(self) -> list:
        return list(self._order)

    def focus_first(self) -> None:
        rows = [w for w in self._order[1:] if w is not self._search]
        target = rows[0] if rows else (self._search if self._search_visible() else self._back)
        target.setFocus(Qt.FocusReason.TabFocusReason)

    def focus_last(self) -> None:
        target = self._order[-1] if self._order else self._back
        target.setFocus(Qt.FocusReason.TabFocusReason)
        self._scroll.ensureWidgetVisible(target)

    def focus_back(self) -> None:
        self._back.setFocus(Qt.FocusReason.TabFocusReason)

    # ------------------------------------------------------------------ building
    def _height(self) -> int:
        body = self._body.sizeHint().height() if self._body is not None else 0
        search = self._search.height() + self._lay.spacing() if self._search_visible() else 0
        return self._header.sizeHint().height() + self._lay.spacing() + search + body

    def _search_visible(self) -> bool:
        return len(self._path) == 1     # the root page, or a page opened straight from the toolbar

    def _set_query(self, text: str) -> None:
        self._query = text
        if self._search.text() != text:
            self._search.blockSignals(True)
            self._search.setText(text)
            self._search.blockSignals(False)

    def _on_query(self, text: str) -> None:
        self._query = text.strip()
        self._open = None
        self._rebuild(focus="<search>")
        self._search.setFocus(Qt.FocusReason.OtherFocusReason)
        self.layout_changed.emit()

    def _style_search(self) -> None:
        """Style the search field; only does work when the theme, scale or language changed."""
        c = self._colors
        k = self._k
        text = i18n.tr("search_settings")
        key = (c.inset.name(), c.inset.alpha(), c.text.name(), c.hover.name(), c.hover.alpha(),
               c.focus.name(), c.dim.name(), k, text)
        if key == self._search_style_key:
            return
        self._search_style_key = key
        self._search.setFixedHeight(scaled(ROW_H - 4, k, MIN_TARGET))
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {_rgba(c.inset)}; color: {c.text.name()}; border: 1px solid {_rgba(c.hover)};"
            f" border-radius: 5px; padding: 0 {scaled(6, k)}px; margin: 0 {scaled(6, k)}px; }}"
            f" QLineEdit:focus {{ border: 1px solid {c.focus.name()}; }}")
        pal = self._search.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, c.dim)
        self._search.setPalette(pal)
        if self._search_action is not None:
            self._search.removeAction(self._search_action)
        self._search_action = self._search.addAction(glyph_icon("search", scaled(TRAIL_PX, k), c.dim),
                                                     QLineEdit.ActionPosition.LeadingPosition)
        self._search.setPlaceholderText(text)
        self._search.setAccessibleName(text)

    def _focused_rid(self) -> str | None:
        for rid, row in self._rows.items():
            if row.hasFocus():
                return rid
        if self._search.hasFocus():
            return "<search>"
        return "<back>" if self._back.hasFocus() else None

    def _rebuild(self, focus: str | None = None) -> None:
        if focus is None:
            focus = self._focused_rid()
        bar = self._scroll.verticalScrollBar()
        scroll_pos = bar.value()
        page = self._path[-1]
        c = self._colors
        old = self._scroll.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        self._rows = {}
        self._order = [self._back]
        searching = self._search_visible()
        self._search.setVisible(searching)
        self._style_search()
        if searching:
            self._order.append(self._search)
        elif self._query:
            self._set_query("")

        self._back.setIcon(glyph_icon("back", scaled(GLYPH_PX, self._k), c.text))
        back = i18n.tr("back")
        self._back.setToolTip(back)
        self._back.setAccessibleName(back)
        self._title.setText(page.title())
        self._title.setAccessibleName(page.title())
        self.setAccessibleName(page.title())

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        if searching and self._query:
            self._add_results(v, body)
            v.addStretch(1)
            self._finish(body, bar, scroll_pos, focus)
            return
        for node in page.children:
            if not _safe(node.visible, True):
                if self._open == node.id:
                    self._open = None
                continue
            if isinstance(node, Separator):
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {c.dim.name()};")
                sep.setContentsMargins(0, 0, 0, 0)
                holder = QWidget()
                hl = QVBoxLayout(holder)
                hl.setContentsMargins(10, 3, 10, 3)
                hl.addWidget(sep)
                v.addWidget(holder)
                continue
            self._add_node(v, body, node)
        v.addStretch(1)
        self._finish(body, bar, scroll_pos, focus)

    def _finish(self, body: QWidget, bar, scroll_pos: int, focus) -> None:
        self._body = body
        self._scroll.setWidget(body)

        order = [r for r in self._order if r is self._back or r is self._search or r.isEnabled()]
        self._order = order
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)
        self._last_height = self._height()
        bar.setValue(scroll_pos)
        self.rebuilt.emit()
        if focus == "<back>":
            self._back.setFocus(Qt.FocusReason.OtherFocusReason)
        elif focus == "<search>" and self._search.isVisible():
            self._search.setFocus(Qt.FocusReason.OtherFocusReason)
        elif focus in self._rows and self._rows[focus].isEnabled():
            self._rows[focus].setFocus(Qt.FocusReason.OtherFocusReason)

    def _make_row(self, rid: str, kind: str, text: str, **kw) -> _Row:
        row = _Row(rid, kind, text, self._colors, scale=self._k, **kw)
        row.installEventFilter(self)
        self._rows[rid] = row
        self._order.append(row)
        return row

    def _trail(self, name: str, enabled: bool = True) -> QIcon:
        return glyph_icon(name, scaled(TRAIL_PX, self._k), self._colors.text if enabled else self._colors.dim)

    def _add_node(self, v: QVBoxLayout, body: QWidget, node) -> None:
        c = self._colors
        if isinstance(node, Header):
            lab = QLabel(node.label(), body)
            lab.setTextFormat(Qt.TextFormat.PlainText)
            f = lab.font()
            f.setBold(True)
            f.setPixelSize(max(10, round(QFontMetrics(self.font()).height() * 0.72)))
            lab.setFont(f)
            lab.setStyleSheet(f"color: {c.dim.name()}; background: transparent;")
            lab.setContentsMargins(scaled(12, self._k), scaled(8, self._k), 0, scaled(2, self._k))
            lab.setAccessibleName(node.label())
            v.addWidget(lab)
        elif isinstance(node, Page):
            row = self._make_row(node.id, "page", node.title(), lead=glyph_icon(node.icon, scaled(GLYPH_PX, self._k), c.text),
                                 trail=self._trail("chevron_right"), parent=body)
            row.setAccessibleName(node.title())
            row.setAccessibleDescription(i18n.tr("settings_page_desc"))
            row.clicked.connect(lambda _c=False, pg=node: self._enter(pg))
            v.addWidget(row)
        elif isinstance(node, Toggle):
            on = bool(_safe(node.get, False))
            row = self._make_row(node.id, "toggle", node.label(), indicator="check", parent=body)
            row.setChecked(on)
            row.setAccessibleName(node.label())
            if node.tip_key:
                row.setToolTip(node.tip())
                row.setAccessibleDescription(node.tip())
            row.clicked.connect(lambda _c=False, t=node: self._toggle(t))
            v.addWidget(row)
        elif isinstance(node, Action):
            enabled = bool(_safe(node.enabled, True))
            row = self._make_row(node.id, "action", node.label(), parent=body)
            row.setEnabled(enabled)
            row.setAccessibleName(node.label())
            row.clicked.connect(lambda _c=False, a=node: self._run(a))
            v.addWidget(row)
        elif isinstance(node, Choice):
            self._add_choice(v, body, node)

    def _add_choice(self, v: QVBoxLayout, body: QWidget, node: Choice) -> None:
        options = _safe(node.options, [])
        cur = _safe(node.get, None)
        cur_label = next((o.label for o in options if o.value == cur), "")
        opened = self._open == node.id
        state = i18n.tr("settings_expanded" if opened else "settings_collapsed")
        row = self._make_row(node.id, "choice", node.label(), value=cur_label,
                             trail=self._trail("chevron_up" if opened else "chevron_down"), parent=body)
        name = ", ".join(x for x in (node.label(), cur_label, state) if x)
        row.setAccessibleName(name)
        row.setToolTip(node.label())
        row.clicked.connect(lambda _c=False, ch=node: self._toggle_choice(ch))
        v.addWidget(row)
        if not opened:
            return
        for o in options:
            rid = f"{node.id}:{o.value}"
            opt = self._make_row(rid, "option", o.label, indicator="radio", inset=True,
                                 preview=getattr(o, "preview", None), parent=body)
            opt.setChecked(o.value == cur)
            opt.setEnabled(o.enabled)
            opt.setAccessibleName(o.label)
            if o.tip:
                opt.setToolTip(o.tip)
                opt.setAccessibleDescription(o.tip)
            opt.clicked.connect(lambda _c=False, ch=node, val=o.value: self._pick(ch, val))
            v.addWidget(opt)

    def _add_results(self, v: QVBoxLayout, body: QWidget) -> None:
        c = self._colors
        hits = self._model.search(self._query)
        if not hits:
            lab = QLabel(i18n.tr("search_none"), body)
            lab.setTextFormat(Qt.TextFormat.PlainText)
            lab.setStyleSheet(f"color: {c.dim.name()}; background: transparent;")
            lab.setContentsMargins(scaled(12, self._k), scaled(10, self._k), 0, scaled(10, self._k))
            v.addWidget(lab)
            return
        for hit in hits:
            node = hit.node
            icon_page = node if isinstance(node, Page) else None
            if icon_page is None:
                chain = self._model.path_to(getattr(self._model.parent_of(node.id), "id", "root"))
                icon_page = chain[1] if len(chain) > 1 else None
            lead = (glyph_icon(icon_page.icon, scaled(GLYPH_PX, self._k), c.text)
                    if icon_page is not None and icon_page.icon else None)
            trail = TRAIL_SEP.join(hit.trail)
            row = self._make_row(f"result:{node.id}", "result", hit.label, value=trail, lead=lead,
                                 trail=self._trail("chevron_right"), parent=body)
            row.setAccessibleName(", ".join(x for x in (hit.label, trail) if x))
            row.setToolTip(TRAIL_SEP.join(hit.trail + [hit.label]))
            row.clicked.connect(lambda _c=False, nid=node.id: self.reveal(nid))
            v.addWidget(row)

    # ------------------------------------------------------------------ actions
    def _enter(self, page: Page) -> None:
        self._path.append(page)
        self._open = None
        self._set_query("")
        self._rebuild(focus="<none>")
        self.focus_first()
        self.layout_changed.emit()
        self.page_changed.emit(self.page_id)

    def _toggle(self, node: Toggle) -> None:
        try:
            node.set(not bool(node.get()))
        except Exception:  # noqa: BLE001 - one failing setting must not take the panel down
            log.exception("could not change %s", node.id)
        self.refresh()

    def _run(self, node: Action) -> None:
        try:
            node.run()
        except Exception:  # noqa: BLE001
            log.exception("settings action %s failed", node.id)
        self.refresh()

    def _toggle_choice(self, node: Choice) -> None:
        self._open = None if self._open == node.id else node.id
        self._rebuild(focus=node.id)
        self.layout_changed.emit()

    def _pick(self, node: Choice, value) -> None:
        try:
            node.set(value)
        except Exception:  # noqa: BLE001
            log.exception("could not change %s", node.id)
        self._rebuild(focus=f"{node.id}:{value}")       # the list stays open: the user can try several
        self.layout_changed.emit()

    # ------------------------------------------------------------------ keyboard
    def eventFilter(self, obj, ev) -> bool:
        if obj is getattr(self, "_search", None) and ev.type() == QEvent.Type.KeyPress and self._search_key(ev):
            return True
        if ev.type() == QEvent.Type.KeyPress and isinstance(obj, QAbstractButton) and self._handle_key(obj, ev):
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev) -> None:
        if self._is_back_key(ev):
            self.go_back()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def _search_key(self, ev) -> bool:
        """Keys of the search field: Down / Enter go to the results, Esc clears it (then goes back)."""
        key = ev.key()
        rows = [w for w in self._order if w is not self._back and w is not self._search and w.isEnabled()]
        if key == Qt.Key.Key_Down and rows:
            rows[0].setFocus(Qt.FocusReason.TabFocusReason)
            self._scroll.ensureWidgetVisible(rows[0])
            return True
        if key == Qt.Key.Key_Up:
            self._back.setFocus(Qt.FocusReason.TabFocusReason)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._query and rows:
                rows[0].click()
            return True
        if key == Qt.Key.Key_Escape:
            if self._query:
                self._set_query("")
                self._on_query("")
            else:
                self.go_back()
            return True
        return False

    @staticmethod
    def _is_back_key(ev) -> bool:
        key = ev.key()
        return (key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace, Qt.Key.Key_Back)
                or (key == Qt.Key.Key_Left and bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)))

    def _handle_key(self, obj: QAbstractButton, ev) -> bool:
        key = ev.key()
        if self._is_back_key(ev):
            self.go_back()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if obj.isEnabled():
                obj.click()
            return True
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if obj in self._order:
                i = self._order.index(obj) + (-1 if key == Qt.Key.Key_Up else 1)
                target = self._order[max(0, min(len(self._order) - 1, i))]
                target.setFocus(Qt.FocusReason.TabFocusReason)
                if target is not self._back:
                    self._scroll.ensureWidgetVisible(target)
            return True
        if key == Qt.Key.Key_Right:
            kind = getattr(obj, "kind", "")
            if kind == "page" or (kind == "choice" and self._open != obj.rid):
                obj.click()
            return True
        if key == Qt.Key.Key_Left:
            kind = getattr(obj, "kind", "")
            if kind == "option":
                parent = obj.rid.split(":", 1)[0]
                self._open = None
                self._rebuild(focus=parent)
                self.layout_changed.emit()
            elif kind == "choice" and self._open == obj.rid:
                self._toggle_choice(self._model.find(obj.rid))
            else:
                self.go_back()
            return True
        return False


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a broken getter must not break the page
        log.exception("settings getter failed")
        return default
