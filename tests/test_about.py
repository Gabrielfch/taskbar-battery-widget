import pytest

from taskbar_battery_widget import __version__, about, i18n, updater
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language, tr
from taskbar_battery_widget.tray import TrayController

REL = updater.Release("9.9.9", "v9.9.9", "https://github.com/Gabrielfch/taskbar-battery-widget/releases/tag/v9.9.9",
                      {})


@pytest.fixture(autouse=True)
def _lang():
    set_language("en")
    yield
    set_language("en")


def _dlg(qapp, tmp_path, **kw):
    return about.AboutDialog(Config(tmp_path / "c.json"), **kw)


def test_dialog_texts_and_links(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    assert d.windowTitle() == "About Taskbar Battery Widget"
    assert __version__ in d.version_label.text()
    assert d.links["license"] == "https://github.com/Gabrielfch/taskbar-battery-widget/blob/main/LICENSE"
    assert d.links["notices"].endswith("/blob/main/THIRD_PARTY_NOTICES.md")
    assert d.links["repo"] == "https://github.com/Gabrielfch/taskbar-battery-widget"
    assert d.links["releases"].endswith("/releases")
    assert d.links["device_report"].endswith("/issues/new?template=device_report.yml")
    for url in d.links.values():
        assert url in d.license_label.text() + d.links_label.text()
    assert str(d.logs_dir) == d.logs_edit.text()
    info = d.tech_edit.toPlainText()
    assert __version__ in info and "Python" in info and "Windows build" in info and "from source" in info
    assert d.check_button.text() == "Check for updates"
    assert not d.auto_check.isChecked()
    assert d.check_button.accessibleName() or d.check_button.text()
    assert not d.notes_button.isVisibleTo(d)


def test_dialog_spanish(qapp, tmp_path):
    set_language("es")
    d = _dlg(qapp, tmp_path)
    assert d.windowTitle() == "Acerca de Taskbar Battery Widget"
    assert d.check_button.text() == "Buscar actualizaciones"
    assert "una vez al día" in d.auto_check.text()
    assert "Avisos de terceros" in d.links_label.text()


def test_copy_button(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    d.copy_button.click()
    assert qapp.clipboard().text() == d.tech_edit.toPlainText()


def test_auto_checkbox_persists(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    d.auto_check.setChecked(True)
    assert Config(tmp_path / "c.json").get("update_check_auto") is True


def test_new_release_shows_buttons_and_notes_url(qapp, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(about, "open_url", lambda u: opened.append(u))
    d = _dlg(qapp, tmp_path, release=REL)
    d.show()
    assert "9.9.9" in d.status_label.text()
    assert d.notes_button.isVisible() and not d.install_button.isVisible()      # source copy
    d.notes_button.click()
    assert opened == [REL.page_url]
    d.close()


def test_install_not_installed_only_opens_page(qapp, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(about, "open_url", lambda u: opened.append(u))
    monkeypatch.setattr(updater, "prepare_update", lambda *a, **k: pytest.fail("must not download"))
    d = _dlg(qapp, tmp_path, release=REL)
    d.install_button.click()
    assert opened == [REL.page_url]


KEY = "ab" * 32


def _signed_rel():
    return updater.Release("9.9.9", "v9.9.9", "p", {"SHA256SUMS.txt": "x", "SHA256SUMS.txt.sig": "z",
                                                    updater.installer_asset_name("9.9.9"): "y"})


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "installed")
    monkeypatch.setattr(updater, "PUBLIC_KEYS", (KEY,))


def test_install_button_only_when_installed_and_verifiable(qapp, tmp_path, monkeypatch, installed):
    d = _dlg(qapp, tmp_path, release=_signed_rel())
    d.show()
    assert d.install_button.isVisible() and d.notes_button.isVisible()
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ())                  # no embedded key: page only
    d.set_release(_signed_rel())
    assert not d.install_button.isVisible() and d.notes_button.isVisible()
    assert d.hint_label.isVisible() and d.hint_label.text() == tr("upd_err_unverified")
    monkeypatch.setattr(updater, "PUBLIC_KEYS", (KEY,))
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "portable")
    d.set_release(_signed_rel())
    assert not d.install_button.isVisible() and d.notes_button.isVisible()
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "installed")
    unsigned = updater.Release("9.9.9", "v9.9.9", "p", {"SHA256SUMS.txt": "x",
                                                        updater.installer_asset_name("9.9.9"): "y"})
    d.set_release(unsigned)
    assert not d.install_button.isVisible()
    d.close()


def test_unverified_failure_leaves_only_release_page(qapp, tmp_path, installed):
    d = _dlg(qapp, tmp_path, release=_signed_rel())
    d.show()
    d._on_prepare_failed("upd_err_unverified")
    assert d.status_label.text() == tr("upd_err_unverified")
    assert not d.install_button.isVisible() and d.notes_button.isVisible()
    d.close()


def test_close_and_hide_cancel_download(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    d.show()
    assert not d._cancel.is_set()
    d.hide()
    assert d._cancel.is_set()
    d._cancel.clear()
    d.show()
    d.close()
    assert d._cancel.is_set()


def test_second_confirmation_before_launch_defaults_to_no(qapp, tmp_path, monkeypatch):
    d = _dlg(qapp, tmp_path)
    seen = {}
    real = about.QMessageBox

    class Box:
        Icon, StandardButton = real.Icon, real.StandardButton

        def __init__(self, icon, title, text, buttons, parent):
            seen.update(title=title, text=text)

        def setDefaultButton(self, b):
            seen["default"] = b

        def exec(self):
            return real.StandardButton.No

    monkeypatch.setattr(about, "QMessageBox", Box)
    assert d._confirm_launch() is False
    assert seen["default"] == Box.StandardButton.No
    assert seen["text"] == tr("upd_confirm2") and "?" in seen["text"]


def test_declining_second_confirmation_does_not_launch_and_deletes(qapp, tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(updater, "launch_installer", lambda p, **k: launched.append(p))
    d = _dlg(qapp, tmp_path)
    monkeypatch.setattr(d, "_confirm_launch", lambda: False)
    folder = tmp_path / "TBW-update-x"
    folder.mkdir()
    exe = folder / "x.exe"
    exe.write_bytes(b"x")
    quits = []
    d.quit_requested.connect(lambda: quits.append(1))
    d._on_prepared(exe)
    assert launched == [] and quits == [] and not folder.exists()


@pytest.fixture
def sync_spawn(monkeypatch):
    """Run worker functions inline (signals then arrive directly in the test thread)."""
    monkeypatch.setattr(about, "_spawn", lambda fn: fn())
    monkeypatch.setattr(updater, "reverify_installer", lambda p: None)


def test_launch_refusal_by_updater_is_reported(qapp, tmp_path, monkeypatch, sync_spawn):
    def boom(p, **k):
        raise updater.UpdateError("upd_err_hash")
    monkeypatch.setattr(updater, "launch_installer", boom)
    d = _dlg(qapp, tmp_path)
    monkeypatch.setattr(d, "_confirm_launch", lambda: True)
    quits = []
    d.quit_requested.connect(lambda: quits.append(1))
    d._on_prepared(tmp_path / "x.exe")
    assert quits == [] and d.status_label.text() == tr("upd_err_hash")


def test_close_button_translated_and_links_readable_on_dark(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    assert d.close_button.text() == "Close"
    set_language("es")
    assert _dlg(qapp, tmp_path).close_button.text() == "Cerrar"
    from PySide6.QtGui import QColor, QPalette
    set_language("en")
    d = _dlg(qapp, tmp_path)
    pal = d.links_label.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    d.links_label.setPalette(pal)
    d._style_links(d.links_label)
    assert d.links_label.palette().color(QPalette.ColorRole.Link).lightness() > 128


def test_install_requires_confirmation(qapp, tmp_path, monkeypatch, installed):
    rel = _signed_rel()
    monkeypatch.setattr(updater, "prepare_update", lambda *a, **k: pytest.fail("no consent"))
    d = _dlg(qapp, tmp_path, release=rel)
    monkeypatch.setattr(d, "_confirm_install", lambda v: False)
    d.install_button.click()
    assert not d._busy


def test_prepared_launches_installer_and_quits(qapp, tmp_path, monkeypatch, sync_spawn):
    launched = []
    monkeypatch.setattr(updater, "launch_installer", lambda p, **k: launched.append((p, k)))
    d = _dlg(qapp, tmp_path)
    monkeypatch.setattr(d, "_confirm_launch", lambda: True)
    quits = []
    d.quit_requested.connect(lambda: quits.append(1))
    d._on_prepared(tmp_path / "x.exe")
    assert launched == [(tmp_path / "x.exe", {"rehash": False})] and quits == [1]


def test_check_failure_message(qapp, tmp_path):
    d = _dlg(qapp, tmp_path)
    d._on_check_failed("upd_err_network")
    assert d.status_label.text() == tr("upd_err_network") and d.check_button.isEnabled()


def test_tray_menu_has_about_before_quit(qapp, tmp_path):
    for lang, text in (("en", "About Taskbar Battery Widget…"), ("es", "Acerca de Taskbar Battery Widget…")):
        set_language(lang)
        t = TrayController(Config(tmp_path / "c.json"))
        texts = [a.text() for a in t.menu.actions() if not a.isSeparator()]
        assert texts[-2] == text and texts[-1] == tr("quit")


def test_auto_checker_notifies_once(qapp, tmp_path):
    cfg = Config(tmp_path / "c.json")
    cfg.set("update_check_auto", True)
    notes = []

    class T:
        def set_available_release(self, r): self.r = r
        def notify(self, a, b, info=False): notes.append((a, b))

    ch = about.AutoUpdateChecker(cfg, T())
    ch._on_checked(REL)
    ch._on_checked(REL)
    assert len(notes) == 1 and "9.9.9" in notes[0][1]
    assert cfg.get("update_last_check") > 0


def test_i18n_keys_complete():
    for k in i18n.EN:
        assert k in i18n.ES, k
    for k in i18n.ES:
        assert k in i18n.EN, k
    for k in ("menu_about", "about_check", "about_auto", "upd_confirm", "upd_err_hash", "upd_confirm2",
              "upd_err_unverified", "upd_err_mode", "upd_err_timeout", "upd_close"):
        assert i18n.EN[k] and i18n.ES[k]
    assert "{version}" in i18n.EN["upd_confirm"] and "{version}" in i18n.ES["upd_confirm"]


def test_rehash_runs_in_worker_not_in_ui_thread(qapp, tmp_path, monkeypatch):
    seen = {}
    spawned = []
    monkeypatch.setattr(about, "_spawn", lambda fn: spawned.append(fn))
    monkeypatch.setattr(updater, "reverify_installer", lambda p: seen.setdefault("re", p))
    monkeypatch.setattr(updater, "launch_installer", lambda p, **k: seen.setdefault("launch", p))
    d = _dlg(qapp, tmp_path)
    monkeypatch.setattr(d, "_confirm_launch", lambda: True)
    d._on_prepared(tmp_path / "x.exe")
    assert seen == {} and len(spawned) == 1              # nothing heavy ran in the calling (UI) thread
    spawned[0]()
    assert seen["re"] == tmp_path / "x.exe" and seen["launch"] == tmp_path / "x.exe"


def test_popen_oserror_is_reported_and_ui_restored(qapp, tmp_path, monkeypatch, sync_spawn):
    def boom(p, **k):
        raise OSError("nope")
    monkeypatch.setattr(updater, "launch_installer", boom)
    d = _dlg(qapp, tmp_path)
    monkeypatch.setattr(d, "_confirm_launch", lambda: True)
    d._busy = True
    d._on_prepared(tmp_path / "x.exe")
    assert not d._busy and d.status_label.text() == tr("upd_err_launch")


class _FakeHide:
    def __init__(self, spontaneous):
        self._s = spontaneous

    def spontaneous(self):
        return self._s


def test_hide_cancels_only_when_not_spontaneous(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "hideEvent", lambda self, e: None)
    d = _dlg(qapp, tmp_path)
    d.hideEvent(_FakeHide(True))                          # minimizing
    assert not d._cancel.is_set()
    d.hideEvent(_FakeHide(False))                         # programmatic hide()
    assert d._cancel.is_set()


def test_close_cancels_download(qapp, tmp_path):
    from PySide6.QtGui import QCloseEvent
    d = _dlg(qapp, tmp_path)
    d.closeEvent(QCloseEvent())
    assert d._cancel.is_set()


def test_set_release_while_busy_is_deferred(qapp, tmp_path, monkeypatch, installed):
    d = _dlg(qapp, tmp_path)
    d._busy = True
    d.install_button.setEnabled(False)
    d._set_status("downloading")
    d.set_release(_signed_rel())
    assert d._release is None and d.status_label.text() == "downloading"
    assert not d.install_button.isEnabled() and d._pending_release is not None
    d._end_install_ui()                                   # operation over: the pending release is applied
    assert d._release is not None and d._pending_release is None
    assert d.status_label.text() == tr("upd_new", version=d._release.version)
