"""Dialog to pick custom icon images per device kind or per device."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from . import custom_icons
from .icons import draw_kind_icon
from .models import KINDS

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.ico *.gif)"
_PREVIEW = 28


class _Preview(QWidget):
    """Draws the default icon of a kind, or the custom image when one is set."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._file: str | None = None
        self.setFixedSize(QSize(_PREVIEW, _PREVIEW))

    def set_file(self, filename: str | None) -> None:
        self._file = filename
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if not (self._file and custom_icons.draw_custom_icon(p, rect, self._file)):
            draw_kind_icon(p, self._kind, rect, self.palette().windowText().color())
        p.end()


class CustomIconsDialog(QDialog):
    def __init__(self, by_kind: dict, by_device: dict, devices, tr=lambda c: c, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._by_kind = {k: v for k, v in (by_kind or {}).items() if custom_icons.safe_name(v)}
        self._by_device = {k: v for k, v in (by_device or {}).items() if custom_icons.safe_name(v)}
        self.setWindowTitle(tr("custom_icons_title"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        tabs = QTabWidget(self)
        lay.addWidget(tabs)
        self._rows: list[tuple[dict, str, _Preview, QPushButton]] = []
        tabs.addTab(self._build_tab(self._by_kind, [(k, tr("kind_" + k), k) for k in KINDS]),
                    tr("custom_icons_tab_kind"))
        tabs.addTab(self._build_tab(self._by_device, [(key, name, kind) for key, name, kind in (devices or [])]),
                    tr("custom_icons_tab_device"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _build_tab(self, target: dict, entries) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        col = QVBoxLayout(body)
        for key, label, kind in entries:
            row = QHBoxLayout()
            default = _Preview(kind)
            default.setAccessibleName(f"{label} - default")
            row.addWidget(default)
            name = QLabel(label)
            name.setMinimumWidth(120)
            row.addWidget(name, 1)
            custom = _Preview(kind)
            custom.setAccessibleName(f"{label} - custom")
            custom.set_file(target.get(key))
            row.addWidget(custom)
            choose = QPushButton(self._tr("custom_icons_choose"))
            remove = QPushButton(self._tr("custom_icons_remove"))
            choose.setAccessibleName(f"{self._tr('custom_icons_choose')}: {label}")
            remove.setAccessibleName(f"{self._tr('custom_icons_remove')}: {label}")
            name.setBuddy(choose)
            remove.setEnabled(key in target)
            choose.clicked.connect(lambda _=False, t=target, k=key, c=custom, r=remove: self._choose(t, k, c, r))
            remove.clicked.connect(lambda _=False, t=target, k=key, c=custom, r=remove: self._remove(t, k, c, r))
            row.addWidget(choose)
            row.addWidget(remove)
            col.addLayout(row)
            self._rows.append((target, key, custom, remove))
        col.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _choose(self, target: dict, key: str, preview: _Preview, remove: QPushButton) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._tr("custom_icons_choose"), "", IMAGE_FILTER)
        if not path:
            return
        try:
            name = custom_icons.import_image(path)
        except custom_icons.IconImportError as e:
            QMessageBox.warning(self, self._tr("custom_icons_title"), self._tr(e.code))
            return
        target[key] = name
        preview.set_file(name)
        remove.setEnabled(True)

    def _remove(self, target: dict, key: str, preview: _Preview, remove: QPushButton) -> None:
        target.pop(key, None)
        preview.set_file(None)
        remove.setEnabled(False)

    def result_maps(self) -> tuple[dict, dict]:
        return dict(self._by_kind), dict(self._by_device)
