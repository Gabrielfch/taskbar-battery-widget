"""'About' dialog and the update-check plumbing around it (Qt side; the logic lives in updater.py)."""
from __future__ import annotations

import html
import logging
import platform
import shutil
import sys
import threading
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from . import __version__, updater
from .i18n import tr
from .icons import make_tray_icon

log = logging.getLogger(__name__)

REPO_URL = updater.REPO_URL
LICENSE_URL = f"{REPO_URL}/blob/main/LICENSE"
NOTICES_URL = f"{REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md"
RELEASES_URL = updater.RELEASES_URL
DEVICE_REPORT_URL = f"{REPO_URL}/issues/new?template=device_report.yml"

AUTO_FIRST_DELAY_MS = 60_000
AUTO_TICK_MS = 6 * 3600 * 1000


def local_notices_path() -> Path | None:
    """THIRD_PARTY_NOTICES.md installed next to the exe (packaged builds only)."""
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).parent / "THIRD_PARTY_NOTICES.md"
    return p if p.is_file() else None


def windows_build() -> str:
    try:
        return str(sys.getwindowsversion().build)        # type: ignore[attr-defined]
    except AttributeError:
        return platform.version() or "?"


def tech_info() -> str:
    from PySide6.QtCore import qVersion
    rows = [
        (tr("tech_app"), __version__),
        (tr("tech_python"), platform.python_version()),
        (tr("tech_qt"), f"{qVersion()} / {pyside_version}"),
        (tr("tech_windows"), windows_build()),
        (tr("tech_install"), tr(updater.install_mode_key())),
    ]
    return "\n".join(f"{k}: {v}" for k, v in rows)


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class _Bridge(QObject):
    """Signals emitted from worker threads and delivered (queued) in the Qt thread."""
    checked = Signal(object)          # Release | None
    check_failed = Signal(str)        # i18n key
    progress = Signal(int, int)
    prepared = Signal(object)         # Path of the verified installer
    prepare_failed = Signal(str)


def _spawn(fn) -> threading.Thread:
    t = threading.Thread(target=fn, daemon=True, name="tbw-update")
    t.start()
    return t


def start_check(bridge: _Bridge) -> threading.Thread:
    def work() -> None:
        try:
            bridge.checked.emit(updater.check())
        except updater.UpdateError as exc:
            log.info("update check failed (%s)", exc.key)
            bridge.check_failed.emit(exc.key)
        except Exception:
            log.info("update check failed (unexpected)")
            bridge.check_failed.emit("upd_err_network")
    return _spawn(work)


class AboutDialog(QDialog):
    quit_requested = Signal()

    def __init__(self, cfg, parent=None, release: updater.Release | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self._release = None
        self._cancel = threading.Event()
        self._busy = False
        self._bridge = _Bridge(self)
        self._bridge.checked.connect(self._on_checked)
        self._bridge.check_failed.connect(self._on_check_failed)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.prepared.connect(self._on_prepared)
        self._bridge.prepare_failed.connect(self._on_prepare_failed)
        self.links: dict[str, str] = {}
        self.setWindowTitle(tr("about_title"))
        self.setModal(False)
        self.setMinimumWidth(480)
        self._build()
        if release is not None:
            self.set_release(release)

    # ------------------------------------------------------------------ layout
    def _label(self, text: str = "", name: str | None = None, wrap: bool = True) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(wrap)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        if name:
            lbl.setAccessibleName(name)
        return lbl

    def _style_links(self, lbl: QLabel) -> None:
        """Readable link colour on dark themes (the default dark-blue link is unreadable there)."""
        pal = lbl.palette()
        if pal.color(QPalette.ColorRole.Window).lightness() < 128:
            for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
                pal.setColor(role, QColor("#8ab4f8"))
            lbl.setPalette(pal)

    def _link(self, key: str, url: str, text: str) -> str:
        self.links[key] = url
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.icon_label = QLabel()
        dark_bg = QGuiApplication.palette().window().color().lightness() < 128
        self.icon_label.setPixmap(make_tray_icon(80, dark_bg).pixmap(64, 64))
        self.icon_label.setAccessibleName(tr("app_name"))
        head.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        self.name_label = QLabel(f"<b style='font-size:15pt'>{html.escape(tr('app_name'))}</b>")
        self.name_label.setAccessibleName(tr("app_name"))
        self.version_label = self._label(tr("about_version", version=__version__), tr("about_version",
                                                                                      version=__version__))
        titles.addWidget(self.name_label)
        titles.addWidget(self.version_label)
        head.addLayout(titles, 1)
        root.addLayout(head)

        self.desc_label = self._label(tr("about_description"), tr("about_description"))
        root.addWidget(self.desc_label)

        lic = self._link("license", LICENSE_URL, tr("about_license_link"))
        self.license_label = self._label(tr("about_license", link=lic), tr("about_license_link"))
        self.license_label.setTextFormat(Qt.TextFormat.RichText)
        self.license_label.linkActivated.connect(open_url)
        self._style_links(self.license_label)
        root.addWidget(self.license_label)

        parts = [self._link("notices", NOTICES_URL, tr("about_notices"))]
        local = local_notices_path()
        if local is not None:
            parts.append(self._link("notices_local", local.as_uri(), tr("about_notices_local")))
        parts += [self._link("repo", REPO_URL, tr("about_repo")),
                  self._link("releases", RELEASES_URL, tr("about_releases")),
                  self._link("device_report", DEVICE_REPORT_URL, tr("about_report_device"))]
        self.links_label = self._label("<br>".join(parts), tr("about_links"))
        self.links_label.setTextFormat(Qt.TextFormat.RichText)
        self.links_label.linkActivated.connect(open_url)
        self._style_links(self.links_label)
        root.addWidget(self.links_label)

        from . import device_history
        self.logs_dir = device_history.logs_dir()
        row = QHBoxLayout()
        cap = QLabel(tr("about_logs") + ":")
        self.logs_edit = QLineEdit(str(self.logs_dir))
        self.logs_edit.setReadOnly(True)
        self.logs_edit.setAccessibleName(tr("about_logs"))
        cap.setBuddy(self.logs_edit)
        self.logs_button = QPushButton(tr("about_open_folder"))
        self.logs_button.clicked.connect(self.open_logs_folder)
        row.addWidget(cap)
        row.addWidget(self.logs_edit, 1)
        row.addWidget(self.logs_button)
        root.addLayout(row)

        cap2 = QLabel(tr("about_tech") + ":")
        self.tech_edit = QPlainTextEdit(tech_info())
        self.tech_edit.setReadOnly(True)
        self.tech_edit.setFixedHeight(100)
        self.tech_edit.setAccessibleName(tr("about_tech"))
        cap2.setBuddy(self.tech_edit)
        self.copy_button = QPushButton(tr("about_copy"))
        self.copy_button.setAccessibleName(tr("about_copy") + " - " + tr("about_tech"))
        self.copy_button.clicked.connect(self.copy_tech_info)
        root.addWidget(cap2)
        root.addWidget(self.tech_edit)
        root.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignRight)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        self.status_label = self._label("", "status")
        self.status_label.setAccessibleName(tr("about_check"))
        root.addWidget(self.status_label)
        self.hint_label = self._label("")
        self.hint_label.hide()
        root.addWidget(self.hint_label)
        self.progress = QProgressBar()
        self.progress.setAccessibleName(tr("upd_downloading", version=""))
        self.progress.hide()
        root.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.check_button = QPushButton(tr("about_check"))
        self.check_button.clicked.connect(self.check_now)
        self.notes_button = QPushButton(tr("upd_notes"))
        self.notes_button.clicked.connect(self.open_release_page)
        self.install_button = QPushButton(tr("upd_install"))
        self.install_button.clicked.connect(self.install_update)
        self.cancel_button = QPushButton(tr("upd_cancel"))
        self.cancel_button.clicked.connect(self.cancel_install)
        for b in (self.notes_button, self.install_button, self.cancel_button):
            b.hide()
        for b in (self.check_button, self.notes_button, self.install_button, self.cancel_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.auto_check = QCheckBox(tr("about_auto"))
        self.auto_check.setChecked(bool(self._cfg.get("update_check_auto")))
        self.auto_check.toggled.connect(self._set_auto)
        root.addWidget(self.auto_check)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton(tr("upd_close"))
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.close)
        close_row.addWidget(self.close_button)
        root.addLayout(close_row)

        self.setTabOrder(self.logs_button, self.tech_edit)
        self.setTabOrder(self.tech_edit, self.copy_button)
        self.setTabOrder(self.copy_button, self.check_button)

    # ------------------------------------------------------------------ simple actions
    def open_logs_folder(self) -> None:
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create the logs folder")
            return
        open_url(self.logs_dir.as_uri())

    def copy_tech_info(self) -> None:
        QGuiApplication.clipboard().setText(self.tech_edit.toPlainText())
        self.hint_label.setText(tr("about_copied"))
        self.hint_label.show()

    def _set_auto(self, checked: bool) -> None:
        try:
            self._cfg.set("update_check_auto", bool(checked))
        except ValueError:
            log.exception("could not save the update setting")

    # ------------------------------------------------------------------ update state
    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setAccessibleDescription(text)

    def set_release(self, rel: updater.Release | None) -> None:
        """Show (or clear) a known newer release."""
        self._release = rel
        show = rel is not None
        self.notes_button.setVisible(show)
        self.install_button.setVisible(False)
        self.hint_label.setVisible(False)
        if rel is None:
            return
        self._set_status(tr("upd_new", version=rel.version))
        self.notes_button.setAccessibleName(f"{tr('upd_notes')} {rel.version}")
        self.install_button.setAccessibleName(f"{tr('upd_install')} {rel.version}")
        if updater.install_mode() != "installed":
            # portable / source copies cannot update themselves: only "View release notes"
            self.hint_label.setText(tr("upd_portable_hint"))
            self.hint_label.show()
        elif not rel.can_install:
            # no signed checksums (or no embedded key): authenticity cannot be verified
            self.hint_label.setText(tr("upd_err_unverified"))
            self.hint_label.show()
        else:
            self.install_button.setVisible(True)

    def check_now(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.check_button.setEnabled(False)
        self._set_status(tr("upd_checking"))
        start_check(self._bridge)

    def _on_checked(self, rel) -> None:
        self._busy = False
        self.check_button.setEnabled(True)
        if rel is None:
            self.set_release(None)
            self._set_status(tr("upd_uptodate"))
        else:
            self.set_release(rel)

    def _on_check_failed(self, key: str) -> None:
        self._busy = False
        self.check_button.setEnabled(True)
        self._set_status(tr(key))

    def open_release_page(self) -> None:
        if self._release is not None:
            open_url(self._release.page_url)

    # ------------------------------------------------------------------ install
    def _confirm_install(self, version: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, tr("upd_confirm_title"),
                          tr("upd_confirm", version=version),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _confirm_launch(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, tr("upd_confirm2_title"), tr("upd_confirm2"),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def install_update(self) -> None:
        rel = self._release
        if rel is None or self._busy:
            return
        if updater.install_mode() != "installed" or not rel.can_install:
            self.open_release_page()
            return
        if not self._confirm_install(rel.version):
            return
        self._busy = True
        self._cancel.clear()
        for b in (self.check_button, self.install_button, self.notes_button):
            b.setEnabled(False)
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        self._set_status(tr("upd_downloading", version=rel.version))
        bridge, cancel = self._bridge, self._cancel

        def work() -> None:
            try:
                path = updater.prepare_update(rel, progress=bridge.progress.emit, cancel=cancel)
                bridge.prepared.emit(path)
            except updater.UpdateError as exc:
                bridge.prepare_failed.emit(exc.key)
            except Exception:
                log.info("update download failed (unexpected)")
                bridge.prepare_failed.emit("upd_err_network")
        _spawn(work)

    def cancel_install(self) -> None:
        self._cancel.set()
        self.cancel_button.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, done * 100 // total))

    def _end_install_ui(self) -> None:
        self._busy = False
        self.progress.hide()
        self.cancel_button.hide()
        for b in (self.check_button, self.install_button, self.notes_button):
            b.setEnabled(True)

    def _on_prepare_failed(self, key: str) -> None:
        self._end_install_ui()
        self._set_status(tr(key))
        if key in ("upd_err_unverified", "upd_err_mode"):         # only the release page is left
            self.install_button.setVisible(False)
            self.notes_button.setVisible(self._release is not None)

    def _on_prepared(self, path) -> None:
        if self._cancel.is_set():                       # cancelled just as the download finished
            shutil.rmtree(Path(path).parent, ignore_errors=True)
            self._on_prepare_failed("upd_err_cancelled")
            return
        if not self._confirm_launch():                  # second, explicit consent after verification
            shutil.rmtree(Path(path).parent, ignore_errors=True)
            self._on_prepare_failed("upd_err_cancelled")
            return
        self._set_status(tr("upd_launching"))
        try:
            updater.launch_installer(Path(path))
        except updater.UpdateError as exc:
            log.info("installer not launched (%s)", exc.key)
            shutil.rmtree(Path(path).parent, ignore_errors=True)
            self._on_prepare_failed(exc.key)
            return
        except OSError:
            log.exception("could not launch the installer")
            self._end_install_ui()
            self._set_status(tr("upd_err_launch"))
            return
        log.info("installer launched")
        self.quit_requested.emit()

    def shutdown(self) -> None:
        self._cancel.set()

    def closeEvent(self, event) -> None:
        self._cancel.set()                              # closing the dialog aborts a running download
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel.set()
        super().hideEvent(event)


class AutoUpdateChecker(QObject):
    """Silent daily check: first run 60 s after start, then re-evaluated every few hours."""
    release_found = Signal(object)

    def __init__(self, cfg, tray, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._tray = tray
        self._busy = False
        self._notified: str | None = None
        self._bridge = _Bridge(self)
        self._bridge.checked.connect(self._on_checked)
        self._bridge.check_failed.connect(self._on_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_TICK_MS)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        _spawn(self._cleanup)
        QTimer.singleShot(AUTO_FIRST_DELAY_MS, self.tick)
        self._timer.start()

    @staticmethod
    def _cleanup() -> None:
        try:
            n = updater.cleanup_old_updates()
            if n:
                log.info("removed %d stale update folder(s)", n)
        except Exception:
            log.info("stale update cleanup failed")

    def tick(self) -> None:
        if self._busy or not updater.should_auto_check(self._cfg):
            return
        self._busy = True
        start_check(self._bridge)

    def _on_checked(self, rel) -> None:
        self._busy = False
        updater.record_check(self._cfg)
        if rel is None:
            return
        self._tray.set_available_release(rel)
        self.release_found.emit(rel)
        if self._notified != rel.version:
            self._notified = rel.version
            self._tray.notify(tr("upd_notify_title"), tr("upd_notify_body", version=rel.version),
                              info=True)

    def _on_failed(self, key: str) -> None:
        self._busy = False
        log.info("automatic update check failed (%s)", key)
