"""Small dialog to pick the high / medium / low battery colours of the "custom" palette."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout)

from .colors import CUSTOM_DEFAULTS, CUSTOM_KEYS, custom_palette, pill_backgrounds
from .i18n import tr

# (config key, colour band it paints, sample text, preview pill background kind)
_SAMPLES = (("high", "green", "85 %", "normal"), ("medium", "amber", "45 %", "normal"),
            ("low", "red", "15 %", "low"))


def _rgb_css(c: QColor) -> str:
    return f"rgb({c.red()},{c.green()},{c.blue()})"


class CustomColorsDialog(QDialog):
    def __init__(self, current: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("custom_colors_title"))
        self._colors = dict(CUSTOM_DEFAULTS)
        if isinstance(current, dict):
            for k in CUSTOM_KEYS:
                if QColor(str(current.get(k, ""))).isValid():
                    self._colors[k] = QColor(current[k]).name().upper()
        self._buttons: dict[str, QPushButton] = {}
        self._pills: list[tuple[QLabel, str, str, bool]] = []

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        for key in CUSTOM_KEYS:
            b = QPushButton(tr("custom_" + key))
            b.clicked.connect(lambda _c=False, k=key: self._pick(k))
            row.addWidget(b)
            self._buttons[key] = b
        root.addLayout(row)

        grid = QGridLayout()
        for col, dark in enumerate((True, False)):
            title = QLabel(tr("custom_preview_dark" if dark else "custom_preview_light"))
            grid.addWidget(title, 0, col)
            for i, (key, band, text, kind) in enumerate(_SAMPLES):
                lbl = QLabel(text)
                lbl.setMinimumWidth(90)
                grid.addWidget(lbl, i + 1, col)
                self._pills.append((lbl, band, kind, dark))
        root.addLayout(grid)

        box = QDialogButtonBox()
        self._reset = box.addButton(tr("custom_reset"), QDialogButtonBox.ButtonRole.ResetRole)
        self._cancel = box.addButton(tr("custom_cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self._ok = box.addButton(tr("custom_ok"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._reset.clicked.connect(self.reset)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        self._refresh()

    def colors(self) -> dict:
        return dict(self._colors)

    def set_color(self, key: str, value: str) -> None:
        c = QColor(value)
        if key in self._colors and c.isValid():
            self._colors[key] = c.name().upper()
            self._refresh()

    def reset(self) -> None:
        self._colors = dict(CUSTOM_DEFAULTS)
        self._refresh()

    def _pick(self, key: str) -> None:
        c = QColorDialog.getColor(QColor(self._colors[key]), self, tr("custom_pick", name=tr("custom_" + key)))
        if c.isValid():
            self.set_color(key, c.name())

    def _refresh(self) -> None:
        pal = custom_palette(self._colors)
        for key, b in self._buttons.items():
            b.setStyleSheet(f"border-left: 14px solid {self._colors[key]}; padding: 4px 10px;")
        for lbl, band, kind, dark in self._pills:
            fg = QColor(*pal[band][0 if dark else 1])
            bg = pill_backgrounds(dark)[kind]
            lbl.setStyleSheet(f"background: {_rgb_css(bg)}; color: {_rgb_css(fg)}; "
                              "padding: 3px 8px; border-radius: 6px;")
