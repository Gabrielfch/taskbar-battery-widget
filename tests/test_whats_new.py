"""Release notes: sanitising, safe display, the About area, the confirmation and the start-up decision."""
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QMessageBox

from taskbar_battery_widget import __version__, about, app, updater, whats_new
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language, tr

PAGE = "https://github.com/Gabrielfch/taskbar-battery-widget/releases/tag/v9.9.9"
BODY = ("## Taskbar Battery Widget 9.9.9\n\n### New\n- **Statistics.** Charges and charts.\n\n"
        "![shot](https://example.com/a.png)\n\n### Fixed\n- Things.\n")


@pytest.fixture(autouse=True)
def _lang():
    set_language("en")
    yield
    set_language("en")


def _api(**extra):
    data = {"tag_name": "v9.9.9", "draft": False, "prerelease": False, "assets": []}
    data.update(extra)
    return data


# ------------------------------------------------------------------ Release.body
def test_release_body_is_kept_and_cleaned():
    rel = updater.parse_release(_api(body="Hello\r\nWorld\x00\x07\x1b[31m\u202e!\u2028ok\ttab"))
    assert rel.body == "Hello\nWorld[31m!ok\ttab"
    assert "\x00" not in rel.body and "\r" not in rel.body


@pytest.mark.parametrize("value", [None, 5, ["x"], {"a": 1}, b"bytes"])
def test_release_body_non_string_is_empty(value):
    assert updater.parse_release(_api(body=value)).body == ""
    assert updater.parse_release(_api()).body == ""


def test_release_body_is_capped():
    rel = updater.parse_release(_api(body="a" * 50_000))
    assert len(rel.body) == updater.MAX_BODY_CHARS == 20_000
    rel = updater.parse_release(_api(body="\x00" * 100_000 + "end"))
    assert rel.body == "end"


# ------------------------------------------------------------------ images
@pytest.mark.parametrize("md", [
    "a ![alt](https://x.test/i.png) b", "a ![](file:///C:/secret.png) b", '![t](https://x.test/a(1).png "title")',
    "a ![alt][ref] b\n\n[ref]: https://x.test/i.png", 'a <img src="https://x.test/i.png" width=3> b',
    "a <IMG SRC=x> b"])
def test_strip_images(md):
    out = whats_new.strip_images(md)
    assert "![" not in out and "<img" not in out.lower() and "x.test" not in out and "secret" not in out
    assert out.startswith("a") or out == ""


def test_strip_images_keeps_text_and_links():
    md = "Text [link](https://github.com/x) and **bold**"
    assert whats_new.strip_images(md) == md
    assert whats_new.strip_images(None) == ""


def test_browser_has_no_image_and_does_not_load_remote_resources(qapp):
    view = whats_new.make_notes_browser(BODY)
    assert view.isReadOnly() and not view.openLinks()
    assert "shot" not in view.toPlainText() and "example.com" not in view.toMarkdown()
    # QTextBrowser only reads local resources: an http(s) image is never fetched
    got = view.loadResource(QTextDocument.ResourceType.ImageResource.value, QUrl("https://example.com/a.png"))
    assert not got


def test_only_https_links_are_opened(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(whats_new.QDesktopServices, "openUrl", lambda u: opened.append(u.toString()))
    for url in ("https://github.com/x", "file:///C:/Windows/System32/calc.exe", "http://x.test", "ms-settings:",
                "javascript:alert(1)"):
        whats_new.open_safe_link(QUrl(url))
    assert opened == ["https://github.com/x"]


def test_summary_is_plain_and_short(qapp):
    s = whats_new.summary(BODY)
    assert "Statistics" in s and "**" not in s and "#" not in s and "shot" not in s
    long = whats_new.summary("word " * 1000, limit=100)
    assert len(long) <= 101 and long.endswith("…")


# ------------------------------------------------------------------ About dialog
def _dlg(tmp_path, rel):
    return about.AboutDialog(Config(tmp_path / "c.json"), release=rel)


def test_about_shows_news_of_the_new_version(qapp, tmp_path):
    d = _dlg(tmp_path, updater.Release("9.9.9", "v9.9.9", PAGE, {}, BODY))
    d.show()
    assert d.notes_title.isVisibleTo(d) and d.notes_browser.isVisibleTo(d)
    assert not d.notes_link.isVisibleTo(d)
    assert d.notes_title.text().count("9.9.9") == 1 and "new in 9.9.9" in d.notes_title.text()
    assert "Statistics" in d.notes_browser.toPlainText() and "example.com" not in d.notes_browser.toMarkdown()
    assert d.notes_browser.isReadOnly()
    d.set_release(None)                                   # cleared again
    assert not d.notes_browser.isVisibleTo(d) and not d.notes_title.isVisibleTo(d)
    d.close()


def test_about_without_body_links_to_github(qapp, tmp_path):
    d = _dlg(tmp_path, updater.Release("9.9.9", "v9.9.9", PAGE, {}))
    d.show()
    assert d.notes_link.isVisibleTo(d) and not d.notes_browser.isVisibleTo(d)
    assert "See the release notes on GitHub" in d.notes_link.text() and PAGE in d.notes_link.text()
    assert d.links["notes_release"] == PAGE
    d.close()


def test_about_without_release_has_no_news_area(qapp, tmp_path):
    d = _dlg(tmp_path, None)
    d.show()
    assert not (d.notes_title.isVisibleTo(d) or d.notes_browser.isVisibleTo(d) or d.notes_link.isVisibleTo(d))
    d.close()


def _capture_confirm(monkeypatch):
    seen = {}

    def fake_exec(self):
        seen["text"], seen["info"] = self.text(), self.informativeText()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return seen


def test_install_confirmation_summarises_the_notes(qapp, tmp_path, monkeypatch):
    d = _dlg(tmp_path, updater.Release("9.9.9", "v9.9.9", PAGE, {}, BODY))
    seen = _capture_confirm(monkeypatch)
    assert d._confirm_install("9.9.9") is False
    assert "9.9.9" in seen["text"]
    assert "What's new in 9.9.9" in seen["info"] and "Statistics" in seen["info"]
    assert "example.com" not in seen["info"] and "![" not in seen["info"]


def test_install_confirmation_links_to_github_without_notes(qapp, tmp_path, monkeypatch):
    d = _dlg(tmp_path, updater.Release("9.9.9", "v9.9.9", PAGE, {}))
    seen = _capture_confirm(monkeypatch)
    d._confirm_install("9.9.9")
    assert "See the release notes on GitHub" in seen["info"] and PAGE in seen["info"]


# ------------------------------------------------------------------ local notes
def _notes_dir(tmp_path, **files):
    folder = tmp_path / "notes"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    return [folder]


def test_local_notes_read_clean_and_missing(tmp_path):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "## Two\r\n\x00ok"})
    assert whats_new.local_notes("2.0.0", dirs) == "## Two\nok"
    assert whats_new.local_notes("v2.0.0", dirs) == "## Two\nok"
    assert whats_new.local_notes("3.0.0", dirs) == ""
    assert whats_new.local_notes("2.0.0", [tmp_path / "missing"]) == ""


@pytest.mark.parametrize("bad", ["../../secret", "2.0", "x", "", None, "2.0.0/../1.0.0", "2.0.0.md"])
def test_local_notes_rejects_odd_versions(tmp_path, bad):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "x"})
    assert whats_new.local_notes(bad, dirs) == ""


def test_the_running_version_ships_its_notes():
    """The installer bundles docs/release-notes/v<version>.md (spec datas): it must exist for each release."""
    assert whats_new.local_notes(__version__)


# ------------------------------------------------------------------ last_seen_version
def _cfg(tmp_path, existing: bool, **values):
    path = tmp_path / "c.json"
    if existing:
        path.write_text("{}", encoding="utf-8")
    cfg = Config(path)
    for k, v in values.items():
        cfg.set(k, v)
    return cfg


def test_config_new_keys_defaults_and_validation(tmp_path):
    cfg = _cfg(tmp_path, False)
    assert cfg.is_new and cfg.get("last_seen_version") == "" and cfg.get("tutorial_seen") is False
    for bad in ("1.2", "v1.2.3", "1.2.3.4", 5, None, "x" * 50):
        with pytest.raises(ValueError):
            cfg.set("last_seen_version", bad)
    cfg.set("last_seen_version", "1.8.0")
    assert Config(cfg.path).get("last_seen_version") == "1.8.0" and not Config(cfg.path).is_new


def test_first_install_shows_tutorial_not_news(tmp_path):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "News"})
    cfg = _cfg(tmp_path, False)
    plan = whats_new.plan_startup(cfg, "2.0.0", dirs)
    assert plan.tutorial and not plan.show_notes and plan.notes == ""
    assert cfg.get("last_seen_version") == "2.0.0"


def test_update_shows_news_once(tmp_path):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "News"})
    cfg = _cfg(tmp_path, True, last_seen_version="1.9.0", tutorial_seen=True)
    plan = whats_new.plan_startup(cfg, "2.0.0", dirs)
    assert plan.show_notes and plan.notes == "News" and not plan.tutorial
    assert cfg.get("last_seen_version") == "2.0.0"
    again = whats_new.plan_startup(cfg, "2.0.0", dirs)
    assert not again.show_notes and not again.tutorial


def test_existing_user_without_last_seen_gets_news_not_tutorial(tmp_path):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "News"})
    cfg = _cfg(tmp_path, True)
    plan = whats_new.plan_startup(cfg, "2.0.0", dirs)
    assert plan.show_notes and not plan.tutorial


def test_same_or_older_version_shows_nothing(tmp_path):
    dirs = _notes_dir(tmp_path, **{"v2.0.0.md": "News", "v1.0.0.md": "Old"})
    cfg = _cfg(tmp_path, True, last_seen_version="2.0.0")
    assert whats_new.plan_startup(cfg, "2.0.0", dirs) == whats_new.StartupPlan()
    assert whats_new.plan_startup(cfg, "1.0.0", dirs) == whats_new.StartupPlan()     # downgrade
    assert cfg.get("last_seen_version") == "2.0.0"


def test_update_without_notes_does_not_fail(tmp_path):
    cfg = _cfg(tmp_path, True, last_seen_version="1.0.0")
    plan = whats_new.plan_startup(cfg, "2.0.0", _notes_dir(tmp_path))
    assert not plan.show_notes and not plan.tutorial and cfg.get("last_seen_version") == "2.0.0"


def test_plan_ignores_an_unparseable_version(tmp_path):
    cfg = _cfg(tmp_path, True)
    assert whats_new.plan_startup(cfg, "dev") == whats_new.StartupPlan()


# ------------------------------------------------------------------ wiring
class _Tray:
    def __init__(self):
        self.calls = []

    def open_tutorial(self):
        self.calls.append("tutorial")

    def open_whats_new(self, notes=None):
        self.calls.append(("news", notes))


def test_startup_guide_dispatch():
    t = _Tray()
    app.show_startup_guide(t, whats_new.StartupPlan(tutorial=True))
    app.show_startup_guide(t, whats_new.StartupPlan(notes="N", show_notes=True))
    app.show_startup_guide(t, whats_new.StartupPlan())
    assert t.calls == ["tutorial", ("news", "N")]

    class Broken(_Tray):
        def open_tutorial(self):
            raise RuntimeError("boom")

    app.show_startup_guide(Broken(), whats_new.StartupPlan(tutorial=True))     # never raises


def test_tray_whats_new_without_notes_shows_a_message(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget.tray import TrayController
    tray = TrayController(Config(tmp_path / "c.json"))
    monkeypatch.setattr(whats_new, "local_notes", lambda *a, **k: "")
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    tray.open_whats_new()
    assert shown == [tr("whatsnew_missing")]


def test_tray_whats_new_with_notes_opens_the_dialog(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget.tray import TrayController
    tray = TrayController(Config(tmp_path / "c.json"))
    seen = []
    monkeypatch.setattr(whats_new, "show_whats_new", lambda v, n, parent=None: seen.append((v, n)))
    tray.open_whats_new("# Notes")
    assert seen == [(__version__, "# Notes")]


def test_settings_has_tutorial_and_news_actions(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget.tray import TrayController
    tray = TrayController(Config(tmp_path / "c.json"))
    calls = []
    monkeypatch.setattr(tray, "open_tutorial", lambda: calls.append("t"))
    monkeypatch.setattr(tray, "open_whats_new", lambda notes=None: calls.append("n"))
    root = tray._settings.root
    tray._settings.find("tutorial").run()
    tray._settings.find("whats_new").run()
    assert calls == ["t", "n"] and [c.id for c in root.children][-2:] == ["tutorial", "whats_new"]


def test_whats_new_dialog_builds_in_every_language(qapp):
    from taskbar_battery_widget.i18n import LANGUAGES
    for lang in LANGUAGES:
        set_language(lang)
        dlg = whats_new.WhatsNewDialog("1.2.3", BODY)
        assert "1.2.3" in dlg.windowTitle() and "{" not in dlg.windowTitle()
        dlg.grab()
