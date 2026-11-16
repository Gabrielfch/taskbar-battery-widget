"""Release notes ("What's new"): the text shown before an update, and once after it.

* ``strip_images`` / ``summary`` / ``make_notes_browser``: safe display of a Markdown body (the notes of a
  release fetched from GitHub or the local ``docs/release-notes/v<version>.md`` shipped with the app).
  Images are removed from the text first: QTextBrowser only loads local resources, never http(s) ones,
  and a markdown image must not make it read a local file either.
* ``plan_startup``: decides at start-up which of the two guides to show (tutorial / what's new) and
  records ``last_seen_version`` / ``tutorial_seen`` so each one appears only once.
* ``local_notes``: the notes of an installed version, read without network.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QTextDocument
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from . import __version__, updater
from .dialogs import app_dialog_guard
from .i18n import tr

log = logging.getLogger(__name__)

NOTES_DIR_PARTS = ("docs", "release-notes")
MAX_NOTES_CHARS = updater.MAX_BODY_CHARS
SUMMARY_CHARS = 500

_IMG_INLINE_RE = re.compile(r"!\[[^\]]*\]\((?:[^()\s]|\([^()]*\))*(?:\s+\"[^\"]*\")?\)")
_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")
_HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_REF_DEF_RE = re.compile(r"^[ ]{0,3}\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)


def strip_images(markdown: str) -> str:
    """Markdown without images: ``![alt](url)``, ``![alt][ref]``, ``<img ...>`` and reference definitions."""
    if not isinstance(markdown, str):
        return ""
    text = _IMG_INLINE_RE.sub("", markdown)
    text = _IMG_REF_RE.sub("", text)
    text = _HTML_IMG_RE.sub("", text)
    text = _REF_DEF_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def summary(markdown: str, limit: int = SUMMARY_CHARS) -> str:
    """Plain-text start of the notes (no images, no markup), at most ``limit`` characters."""
    doc = QTextDocument()
    doc.setMarkdown(strip_images(markdown))
    lines = [ln.strip() for ln in doc.toPlainText().splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def open_safe_link(url: QUrl) -> None:
    """Only https links leave the app (notes could contain file:// or custom-scheme links)."""
    if url.scheme().lower() == "https":
        QDesktopServices.openUrl(url)


def make_notes_browser(markdown: str, parent: QWidget | None = None) -> QTextBrowser:
    """Read-only QTextBrowser with the notes; links open in the system browser (https only)."""
    view = QTextBrowser(parent)
    view.setReadOnly(True)
    view.setOpenLinks(False)                     # anchorClicked decides: https only
    view.anchorClicked.connect(open_safe_link)
    view.setMarkdown(strip_images(markdown))
    pal = view.palette()
    if pal.color(QPalette.ColorRole.Base).lightness() < 128:
        for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
            pal.setColor(role, QColor("#8ab4f8"))
        view.setPalette(pal)
    return view


# ------------------------------------------------------------------ local notes
def notes_dirs() -> list[Path]:
    """Folders searched for ``v<version>.md``: next to the bundle when frozen, the repo otherwise."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            dirs.append(Path(base).joinpath(*NOTES_DIR_PARTS))
        dirs.append(Path(sys.executable).parent.joinpath(*NOTES_DIR_PARTS))
    dirs.append(Path(__file__).resolve().parent.parent.joinpath(*NOTES_DIR_PARTS))
    return dirs


def local_notes(version: str = __version__, dirs: list[Path] | None = None) -> str:
    """Notes of ``version`` shipped with the app; "" when absent or unreadable (never raises)."""
    ver = updater.parse_version(version)
    if ver is None:
        return ""
    name = "v" + ".".join(map(str, ver)) + ".md"
    for folder in notes_dirs() if dirs is None else dirs:
        path = Path(folder) / name
        try:
            if path.is_file() and path.stat().st_size <= 4 * MAX_NOTES_CHARS:
                return updater.sanitize_body(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            log.info("could not read the local release notes")
    return ""


# ------------------------------------------------------------------ start-up decision
@dataclass(frozen=True)
class StartupPlan:
    tutorial: bool = False          # first run: show the tutorial
    notes: str = ""                 # just updated: notes of this version ("" = nothing to show)
    show_notes: bool = False


def plan_startup(cfg, current: str = __version__, dirs: list[Path] | None = None) -> StartupPlan:
    """What to show at start-up, and remember it.

    * first run (no settings file existed): tutorial, no news; ``last_seen_version`` is set;
    * run of a newer version than ``last_seen_version``: its notes once (nothing when there are none);
    * same version: nothing.
    ``last_seen_version`` is saved before anything is shown so a crash cannot make it repeat.
    """
    cur = updater.parse_version(current)
    if cur is None:
        return StartupPlan()
    seen = updater.parse_version(cfg.get("last_seen_version")) or (0, 0, 0)
    first_run = bool(getattr(cfg, "is_new", False)) and not cfg.get("tutorial_seen")
    if cur <= seen and not first_run:
        return StartupPlan()
    try:
        cfg.set("last_seen_version", ".".join(map(str, cur)))
    except ValueError:
        log.info("could not save last_seen_version")
    if first_run:
        return StartupPlan(tutorial=True)
    notes = local_notes(current, dirs)
    return StartupPlan(notes=notes, show_notes=bool(notes))


# ------------------------------------------------------------------ dialog
class WhatsNewDialog(QDialog):
    """"What's new in X.Y.Z": read-only notes and a Close button."""

    def __init__(self, version: str, notes: str, parent=None):
        super().__init__(parent)
        title = tr("whatsnew_title", version=version)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 380)
        root = QVBoxLayout(self)
        head = QLabel(f"<b style='font-size:13pt'>{_escape(title)}</b>")
        head.setAccessibleName(title)
        root.addWidget(head)
        self.browser = make_notes_browser(notes, self)
        self.browser.setAccessibleName(title)
        root.addWidget(self.browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_button = QPushButton(tr("upd_close"))
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        root.addLayout(row)


def _escape(text: str) -> str:
    import html
    return html.escape(text)


def show_whats_new(version: str, notes: str, parent=None) -> None:
    """Modal 'What's new' dialog (guarded so the device panel stays open meanwhile)."""
    dlg = WhatsNewDialog(version, notes, parent)
    with app_dialog_guard():
        dlg.exec()
