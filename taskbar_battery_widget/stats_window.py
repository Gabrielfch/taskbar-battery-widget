"""Per-device statistics window: summary cards and QPainter charts computed from the detailed CSV log."""
from __future__ import annotations

import datetime as _dt
import logging
import math
import threading
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                           QPainterPath, QPalette, QPen, QShortcut)
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget)

from . import i18n, log_stats
from .colors import band_color
from .i18n import tr

log = logging.getLogger(__name__)

MIN_SAMPLES = 10
REFRESH_MS = 60_000
STALE_RELOAD_S = 60.0
DASH = "—"
# range id -> seconds (None = everything)
RANGES: dict[str, float | None] = {"24h": 86400.0, "7d": 7 * 86400.0, "30d": 30 * 86400.0, "all": None}
DEFAULT_RANGE = "7d"


# ---------------------------------------------------------------- formatting

def _lang(lang: str | None) -> str:
    return i18n.normalize_language(lang or i18n.get_language())


def _dur(key: str, n: int, lang: str | None) -> str:
    return i18n.tr_in(_lang(lang), key, n=n)


def format_duration(hours: float | None, lang: str | None = None) -> str:
    """'0 min', '45 min', '5 h 30 min', '1 d 4 h' (units localized); '—' for None."""
    if hours is None:
        return DASH
    minutes = max(0, round(hours * 60))
    if minutes >= 24 * 60:
        d, rest = divmod(minutes, 24 * 60)
        h = round(rest / 60)
        if h >= 24:
            d, h = d + 1, 0
        return f"{_dur('dur_d', d, lang)} {_dur('dur_h', h, lang)}" if h else _dur("dur_d", d, lang)
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{_dur('dur_h', h, lang)} {_dur('dur_min', m, lang)}" if m else _dur("dur_h", h, lang)
    return _dur("dur_min", minutes, lang)


def decimal_text(text: str, lang: str | None = None) -> str:
    """Use the decimal comma in the languages that write it (es, pt, fr, ru)."""
    return text.replace(".", ",") if _lang(lang) in i18n.DECIMAL_COMMA else text


def _fromts(ts: float) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None


def format_day(day: _dt.date | _dt.datetime, lang: str | None = None, year: bool = False) -> str:
    """Date in the language's order (d/m/y, m/d/y or y/m/d); optional 2-digit year."""
    order, sep = i18n.DATE_STYLE.get(_lang(lang), ("mdy", "/"))
    codes = {"d": "%d", "m": "%m", "y": "%y"}
    parts = [codes[c] for c in order if year or c != "y"]
    return day.strftime(sep.join(parts))


def format_when(ts: float | None, lang: str | None = None) -> str:
    """Full date and time with the locale's day/month order (cards, tooltips)."""
    if ts is None:
        return DASH
    d = _fromts(ts)
    if d is None:
        return DASH
    return f"{format_day(d, lang, year=True)} {d.strftime('%H:%M')}"


def format_axis_time(ts: float, span_s: float, lang: str | None = None) -> str:
    """X axis label: time only for spans <= 12 h, date+time up to 2 days, else date (+year if > 300 days)."""
    d = _fromts(ts)
    if d is None:
        return ""
    if span_s <= 12 * 3600:
        return d.strftime("%H:%M")
    if span_s <= 2 * 86400:
        return f"{format_day(d, lang)} {d.strftime('%H:%M')}"
    return format_day(d, lang, year=span_s > 300 * 86400)


def axis_label_sample(span_s: float) -> str:
    """Widest plausible label for a span, used to size labels with QFontMetrics."""
    if span_s <= 12 * 3600:
        return "00:00"
    if span_s <= 2 * 86400:
        return "00/00 00:00"
    return "00/00/00" if span_s > 300 * 86400 else "00/00"


def format_rate(rate: float | None, lang: str | None = None) -> str:
    if rate is None:
        return DASH
    return decimal_text(f"{rate:.1f}", lang) + " " + i18n.tr_in(_lang(lang), "unit_rate")


def format_cycles(cycles: float, charge_count: int, lang: str | None = None) -> str:
    """'2.1' ('2,1' with a decimal comma); the dash when no charge was seen."""
    if not charge_count:
        return DASH
    text = f"{cycles:.1f}"
    return decimal_text(text, lang)


def format_pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return DASH
    return f"{v:+.0f} %" if signed else f"{v:.0f} %"


def _dark() -> bool:
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def level_line_color(dark: bool) -> QColor:
    """Blue level line (distinct from the amber of the drain chart), contrasted on both themes."""
    return QColor(0x6C, 0xB4, 0xFF) if dark else QColor(0x1F, 0x5F, 0xBF)


def _base_pt(font: QFont) -> float:
    pt = font.pointSizeF()
    if pt > 0:
        return pt
    px = font.pixelSize()
    return px * 0.75 if px > 0 else 9.0


def _scaled(font: QFont, factor: float, bold: bool = False) -> QFont:
    f = QFont(font)
    f.setPointSizeF(_base_pt(font) * factor)
    if bold:
        f.setBold(True)
    return f


def nice_int_axis(peak: float) -> tuple[float, list[float]]:
    """Integer-only y axis (ymax, ticks) for count-like data."""
    top = max(1, math.ceil(peak))
    if top <= 4:
        return float(top), [float(i) for i in range(top + 1)]
    step = math.ceil(top / 4)
    return float(step * 4), [0.0, float(step * 2), float(step * 4)]


# ---------------------------------------------------------------- charts

class _Chart(QWidget):
    """Base: palette-derived colours, plot rectangle, accessible summary and hover tooltips."""
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 38, 12, 8, 24

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName(title)
        self._hits: list[tuple[QRectF, str]] = []    # hover regions -> tooltip text

    def _axis_font(self) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, _base_pt(self.font()) - 1))
        return f

    def _colors(self):
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.WindowText)
        grid = QColor(text)
        grid.setAlpha(70)
        return text, grid, pal.color(QPalette.ColorRole.Base)

    def _plot_rect(self) -> QRectF:
        return QRectF(self.MARGIN_L, self.MARGIN_T + 14,
                      max(10, self.width() - self.MARGIN_L - self.MARGIN_R),
                      max(10, self.height() - self.MARGIN_T - 14 - self.MARGIN_B))

    def _draw_frame(self, p: QPainter, plot: QRectF, ymax: float, yticks: list[float]) -> None:
        text, grid, base = self._colors()
        p.fillRect(self.rect(), base)
        p.setFont(self._axis_font())
        p.setPen(text)
        p.drawText(QRectF(self.MARGIN_L, 2, plot.width(), 16), Qt.AlignmentFlag.AlignLeft, self._title)
        for v in yticks:
            y = plot.bottom() - plot.height() * (v / ymax if ymax else 0)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(text)
            p.drawText(QRectF(0, y - 8, self.MARGIN_L - 4, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:g}")

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for rect, text in self._hits:
            if rect.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)


class _LevelChart(_Chart):
    def __init__(self, parent=None):
        super().__init__(tr("stats_chart_level"), parent)
        self._series: list[tuple[float, int | None]] = []
        self._spans: list[tuple[float, float]] = []
        self._t0 = 0.0
        self._t1 = 1.0
        self._paths: list[QPainterPath] = []

    def set_data(self, series, spans, t0: float, t1: float) -> None:
        self._series, self._spans = list(series), list(spans)
        self._t0, self._t1 = t0, max(t1, t0 + 1)
        pts = [(t, v) for t, v in self._series if v is not None]
        if pts:
            self.setAccessibleDescription(tr("stats_level_desc", n=len(pts), lo=min(v for _, v in pts),
                                             hi=max(v for _, v in pts), last=pts[-1][1],
                                             when=format_when(pts[-1][0])))
        else:
            self.setAccessibleDescription(tr("stats_no_data"))
        self._rebuild()
        self.update()

    def _x_of(self, plot: QRectF, t: float) -> float:
        span = self._t1 - self._t0
        return plot.left() + plot.width() * min(1.0, max(0.0, (t - self._t0) / span))

    def _rebuild(self) -> None:
        """Cache the polyline paths and hover regions (only on new data or resize, not per paint)."""
        plot = self._plot_rect()
        paths: list[QPainterPath] = []
        hits: list[tuple[QRectF, str]] = []
        path = QPainterPath()
        down = False
        for t, v in self._series:
            if v is None:
                if down:
                    paths.append(path)
                path, down = QPainterPath(), False
                continue
            pt = QPointF(self._x_of(plot, t), plot.bottom() - plot.height() * v / 100.0)
            if down:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
                down = True
            hits.append((QRectF(pt.x() - 3, plot.top(), 6, plot.height()), f"{format_when(t)}: {v} %"))
        if down:
            paths.append(path)
        if len(hits) > 400:                      # keep hover lookup cheap
            hits = hits[:: len(hits) // 400 + 1]
        self._paths, self._hits = paths, hits

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        self._draw_frame(p, plot, 100, [0, 50, 100])
        text, grid, base = self._colors()
        dark = _dark()
        span = self._t1 - self._t0

        # x axis: label count and width follow the real font metrics (no clipping at 150 % scaling)
        fm = QFontMetrics(p.font())
        lw = fm.horizontalAdvance(axis_label_sample(span)) + 10
        n = int(max(2, min(5, plot.width() // lw)))
        p.setPen(text)
        for i in range(n):
            t = self._t0 + span * i / (n - 1)
            x = self._x_of(plot, t)
            left = min(max(0.0, x - lw / 2), max(0.0, self.width() - lw))
            p.drawText(QRectF(left, plot.bottom() + 3, lw, 16), Qt.AlignmentFlag.AlignHCenter,
                       format_axis_time(t, span))

        shade = band_color("green", dark)
        shade.setAlpha(60 if dark else 90)
        for a, b in self._spans:
            xa = self._x_of(plot, a)
            xb = max(self._x_of(plot, b), xa + 2)
            p.fillRect(QRectF(xa, plot.top(), xb - xa, plot.height()), shade)

        pen = QPen(level_line_color(dark), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for path in self._paths:
            p.drawPath(path)

        # legend (top right)
        legend = tr("stats_legend_charging")
        lwid = fm.horizontalAdvance(legend)
        right = self.width() - self.MARGIN_R
        sw = QRectF(right - lwid - 18, 6, 10, 10)
        sh = band_color("green", dark)
        sh.setAlpha(120)
        p.fillRect(sw, sh)
        p.setPen(text)
        p.drawText(QRectF(right - lwid - 4, 2, lwid + 4, 16), Qt.AlignmentFlag.AlignRight, legend)
        p.end()


class _BarChart(_Chart):
    def __init__(self, title: str, unit: str, color_name: str, parent=None):
        super().__init__(title, parent)
        self._unit = unit
        self._color_name = color_name
        self._bars: list[tuple[_dt.date, float]] = []

    def set_data(self, bars: list[tuple[_dt.date, float]]) -> None:
        self._bars = list(bars)
        total = sum(v for _, v in self._bars)
        if self._bars:
            day, last = self._bars[-1]
            self.setAccessibleDescription(
                tr("stats_bars_desc", n=len(self._bars), total=f"{total:g}", unit=self._unit,
                   when=format_day(day, year=len(self._bars) > 300), last=f"{last:g}"))
        else:
            self.setAccessibleDescription(tr("stats_no_data"))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        peak = max((v for _, v in self._bars), default=0)
        ymax, ticks = nice_int_axis(peak)
        self._draw_frame(p, plot, ymax, ticks)
        text, _, _ = self._colors()
        self._hits = []
        n = len(self._bars)
        if n:
            year = n > 300
            slot = plot.width() / n
            bw = max(2.0, min(40.0, slot * 0.7))
            color = band_color(self._color_name, _dark())
            lw = QFontMetrics(p.font()).horizontalAdvance("00/00/00" if year else "00/00") + 10
            label_every = max(1, math.ceil(lw / slot))
            for i, (day, v) in enumerate(self._bars):
                cx = plot.left() + slot * (i + 0.5)
                h = plot.height() * v / ymax
                rect = QRectF(cx - bw / 2, plot.bottom() - h, bw, h)
                if v > 0:
                    p.fillRect(rect, color)
                self._hits.append((QRectF(cx - slot / 2, plot.top(), slot, plot.height()),
                                   f"{format_day(day, year=year)}: {v:g} {self._unit}"))
                if i % label_every == 0:
                    p.setPen(text)
                    left = min(max(0.0, cx - lw / 2), max(0.0, self.width() - lw))
                    p.drawText(QRectF(left, plot.bottom() + 3, lw, 16), Qt.AlignmentFlag.AlignHCenter,
                               format_day(day, year=year))
        p.end()


# ---------------------------------------------------------------- window

class _Bridge(QObject):
    loaded = Signal(int, object, object, bool)      # generation, samples, Stats, ok


class _Card(QFrame):
    """Plain container: the three child labels carry the accessible reading (no duplicated name)."""

    def __init__(self, title: str, tip: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        self.value = QLabel(DASH)
        self.value.setFont(_scaled(self.value.font(), 1.35, bold=True))
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        dim = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        dim.setAlpha(170)
        pal = self.title.palette()
        pal.setColor(QPalette.ColorRole.WindowText, dim)
        self.title.setPalette(pal)
        self.detail.setPalette(pal)
        for w in (self.title, self.value, self.detail):
            lay.addWidget(w)
        if tip:
            self.setToolTip(tip)
        self.set("", DASH)

    def set(self, value: str, detail: str = "") -> None:
        self.value.setText(value or DASH)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))


CARD_KEYS = ("charges", "runtime", "between", "rate", "charge_time", "last_charge", "cycles")
CARD_TIPS = ("runtime", "cycles")      # cards with an explanatory tooltip (stats_card_<key>_tip)

_RANGE_QSS = (
    "QPushButton { padding: 4px 12px; border: 1px solid palette(mid); border-radius: 4px;"
    " background: palette(button); color: palette(button-text); }"
    "QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); font-weight: bold; }"
    "QPushButton:focus { border: 2px solid palette(window-text); }"
)


class StatsWindow(QWidget):
    def __init__(self, name: str, csv_path: Path, open_folder: Callable[[], None] | None = None):
        super().__init__(None)
        self.device_name = name
        self.csv_path = Path(csv_path)
        self._open_folder = open_folder
        self._gen = 0
        self._closed = False
        self._loading = False
        self._pending = False
        self._error = False
        self._loaded_at = 0.0
        self._samples: list = []
        self._stats = None
        self._range = DEFAULT_RANGE
        self._bridge = _Bridge(self)
        self._bridge.loaded.connect(self._on_loaded)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.reload)
        self.setWindowTitle(tr("stats_title", name=name))
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._apply_initial_size()
        self._build()
        for seq in ("F5", "Ctrl+R"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(self.reload)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.close)
        self.reload()

    def _apply_initial_size(self) -> None:
        w, h, mw, mh = 720, 640, 480, 360
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() > 0 and avail.height() > 0:
                w, h = min(w, avail.width() - 40), min(h, avail.height() - 60)
                mw, mh = min(mw, w), min(mh, h)
        self.resize(w, h)
        self.setMinimumSize(mw, mh)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.name_label = QLabel(self.device_name)
        self.name_label.setFont(_scaled(self.name_label.font(), 1.5, bold=True))
        self.header_label = QLabel(tr("stats_loading"))
        root.addWidget(self.name_label)
        root.addWidget(self.header_label)

        self.empty_label = QLabel(tr("stats_loading"))
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label, 1)

        self.content = QWidget()
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        self.cards: dict[str, _Card] = {}
        for i, key in enumerate(CARD_KEYS):
            tip = tr(f"stats_card_{key}_tip") if key in CARD_TIPS else ""
            c = _Card(tr(f"stats_card_{key}"), tip)
            self.cards[key] = c
            grid.addWidget(c, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        cl.addLayout(grid)

        rng = QHBoxLayout()
        rng.addWidget(QLabel(tr("stats_range") + ":"))
        self.range_group = QButtonGroup(self)
        self.range_buttons: dict[str, QPushButton] = {}
        for rid in RANGES:
            b = QPushButton(tr(f"stats_range_{rid}"))
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setStyleSheet(_RANGE_QSS)
            b.setToolTip(tr(f"stats_range_{rid}_tip"))
            b.setChecked(rid == self._range)
            b.setAccessibleName(f"{tr('stats_range')} {b.text()}")
            self.range_group.addButton(b)
            self.range_buttons[rid] = b
            b.clicked.connect(lambda _=False, r=rid: self.set_range(r))
            rng.addWidget(b)
        rng.addStretch(1)
        cl.addLayout(rng)

        self.level_chart = _LevelChart()
        self.drop_chart = _BarChart(tr("stats_chart_drop"), "%", "amber")
        self.charges_chart = _BarChart(tr("stats_chart_charges"), tr("stats_unit_charges"), "green")
        cl.addWidget(self.level_chart, 3)
        cl.addWidget(self.drop_chart, 2)
        cl.addWidget(self.charges_chart, 2)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        self.refresh_button = QPushButton(tr("stats_refresh"))
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.setToolTip(tr("stats_refresh_tip"))
        self.refresh_button.clicked.connect(self.reload)
        self.folder_button = QPushButton(tr("stats_open_folder"))
        self.folder_button.setAutoDefault(False)
        self.folder_button.setToolTip(tr("stats_open_folder_tip"))
        self.folder_button.clicked.connect(self._on_open_folder)
        row.addWidget(self.refresh_button)
        row.addWidget(self.folder_button)
        row.addStretch(1)
        root.addLayout(row)
        self.scroll.hide()
        self.empty_label.hide()

    def _on_open_folder(self) -> None:
        if self._open_folder is not None:
            self._open_folder()

    def set_device(self, name: str, csv_path: Path, open_folder: Callable[[], None] | None = None) -> None:
        """The device may have been renamed (new log path); update title/labels and reload."""
        changed = Path(csv_path) != self.csv_path
        self.device_name = name
        self.csv_path = Path(csv_path)
        if open_folder is not None:
            self._open_folder = open_folder
        self.setWindowTitle(tr("stats_title", name=name))
        self.name_label.setText(name)
        if changed:
            self._gen += 1                         # drop a load of the old path still in flight
            self._loading = self._pending = False
            self._stats, self._samples, self._error = None, [], False
            self.header_label.setText(tr("stats_loading"))
            self.header_label.show()
        self.reload()

    # ------------------------------------------------------------ loading
    def reload(self) -> None:
        if self._closed:
            return
        if self._loading:                          # coalesce: reload again once the current one ends
            self._pending = True
            return
        self._loading = True
        self._gen += 1
        self.refresh_button.setEnabled(False)
        gen, path, bridge = self._gen, self.csv_path, self._bridge
        if self._stats is None:
            self.header_label.setText(tr("stats_loading"))

        def work() -> None:
            ok = True
            try:
                samples = log_stats.read_samples(path)
                stats = log_stats.compute_stats(samples)
            except Exception:
                log.exception("statistics could not be computed")
                ok = False
                samples, stats = [], log_stats.compute_stats([])
            try:
                bridge.loaded.emit(gen, samples, stats, ok)
            except RuntimeError:                   # window already destroyed
                pass

        threading.Thread(target=work, daemon=True, name="tbw-stats").start()

    def _on_loaded(self, gen: int, samples, stats, ok: bool = True) -> None:
        if self._closed or gen != self._gen:
            return
        self._loading = False
        self.refresh_button.setEnabled(True)
        self._loaded_at = time.monotonic()
        self._samples, self._stats, self._error = samples, stats, not ok
        self.refresh_view()
        if self._pending:
            self._pending = False
            self.reload()

    def set_range(self, rid: str) -> None:
        if rid in RANGES:
            self._range = rid
            self.range_buttons[rid].setChecked(True)
            self.refresh_view()

    # ------------------------------------------------------------ view
    def has_data(self) -> bool:
        return self._stats is not None and not self._error and self._stats.samples >= MIN_SAMPLES

    def refresh_view(self) -> None:
        st = self._stats
        if st is None:
            return
        if not self.has_data():
            if self._error:
                self.empty_label.setText(tr("stats_error"))
                self.header_label.hide()
            else:
                self.empty_label.setText(tr("stats_empty", n=st.samples, min=MIN_SAMPLES))
                if st.first is not None and st.samples:
                    self.header_label.setText(tr("stats_header", since=format_when(st.first),
                                                 hours=format_duration(st.hours_logged)))
                    self.header_label.show()
                else:
                    self.header_label.hide()
            self.scroll.hide()
            self.empty_label.show()
            return
        self.empty_label.hide()
        self.scroll.show()
        self.header_label.show()
        self.header_label.setText(tr("stats_header", since=format_when(st.first),
                                     hours=format_duration(st.hours_logged)))
        inferred = sum(1 for c in st.charges if not c.observed)
        observed = st.charge_count - inferred
        self.cards["charges"].set(str(st.charge_count) if st.charge_count or st.samples else DASH,
                                  tr("stats_charges_detail", obs=observed, est=inferred) if inferred else "")
        self.cards["runtime"].set(format_duration(st.est_full_runtime_h))
        self.cards["between"].set(format_duration(st.avg_between_charges_h))
        self.cards["rate"].set(format_rate(st.avg_discharge_rate))
        gain = (tr("stats_gain_detail", gain=format_pct(st.avg_charge_gain_pct, signed=True))
                if st.avg_charge_gain_pct is not None else "")
        self.cards["charge_time"].set(format_duration(st.avg_charge_duration_h), gain)
        self.cards["last_charge"].set(format_when(st.last_charge_end))
        self.cards["cycles"].set(format_cycles(st.equivalent_cycles, st.charge_count))

        now = time.time()
        secs = RANGES[self._range]
        since = None if secs is None else now - secs
        t0 = since if since is not None else (st.first or now)
        t1 = now if since is not None else (st.last or now)
        self.level_chart.set_data(log_stats.level_series(self._samples, since),
                                  log_stats.charging_spans(self._samples, since), t0, t1)
        days = [d for d in st.days if since is None or d.day >= _dt.datetime.fromtimestamp(since).date()]
        self.drop_chart.set_data([(d.day, d.drop_pct) for d in days])
        self.charges_chart.set_data([(d.day, d.charges) for d in days])

    # ------------------------------------------------------------ events
    def showEvent(self, event) -> None:
        self._closed = False
        self._timer.start()
        super().showEvent(event)
        if (self._stats is not None and not self._loading
                and time.monotonic() - self._loaded_at > STALE_RELOAD_S):
            self.reload()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._gen += 1                              # drop any load still in flight
        self._loading = False
        self._pending = False
        self.refresh_button.setEnabled(True)
        self._timer.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def shutdown(self) -> None:
        self._closed = True
        self._gen += 1
        self._timer.stop()
        self.close()


class StatsWindows:
    """One StatsWindow per device key: opening again raises and refreshes the existing one."""

    def __init__(self) -> None:
        self._windows: dict[str, StatsWindow] = {}

    def open(self, key: str, name: str, csv_path: Path,
             open_folder: Callable[[], None] | None = None) -> StatsWindow:
        win = self._windows.get(key)
        if win is None:
            win = StatsWindow(name, csv_path, open_folder)
            self._windows[key] = win
        else:
            win.set_device(name, csv_path, open_folder)
        win.show()
        if win.isMinimized():
            win.showNormal()
        win.raise_()
        win.activateWindow()
        return win

    def close_key(self, key: str) -> None:
        """Close (and drop) the window of one device, if it is open."""
        w = self._windows.pop(key, None)
        if w is not None:
            try:
                w.shutdown()
            except RuntimeError:
                pass

    def close_all(self) -> None:
        for w in self._windows.values():
            try:
                w.shutdown()
            except RuntimeError:
                pass
        self._windows.clear()
