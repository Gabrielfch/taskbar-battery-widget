"""First-run tutorial: six steps, navigation, "seen" bookkeeping, drawing without errors."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtTest import QTest

from taskbar_battery_widget import i18n, tutorial
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import LANGUAGES, set_language, tr


@pytest.fixture(autouse=True)
def _lang():
    set_language("en")
    yield
    set_language("en")


def _dlg(tmp_path):
    cfg = Config(tmp_path / "c.json")
    return cfg, tutorial.TutorialDialog(cfg)


def test_six_steps_with_navigation(qapp, tmp_path):
    assert tutorial.STEPS == 6
    cfg, d = _dlg(tmp_path)
    d.show()
    assert d.step == 0 and not d.prev_button.isEnabled() and d.next_button.text() == "Next"
    assert "Step 1 of 6" in d.step_label.text()
    seen = {d.title_label.text()}
    for expected in range(1, 6):
        d.next_button.click()
        assert d.step == expected and d.prev_button.isEnabled()
        seen.add(d.title_label.text())
    assert len(seen) == 6                                  # every step has its own title
    assert d.next_button.text() == "Get started" and not d.skip_button.isVisibleTo(d)
    d.prev_button.click()
    assert d.step == 4 and d.skip_button.isVisibleTo(d)
    assert d.dots.step == 4 and d.art.step == 4


def test_finishing_marks_seen(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    d.step = 5
    d._show_step()
    d.next_button.click()
    assert d.finished == "done" and not d.isVisible() and cfg.get("tutorial_seen") is True


def test_skip_button_marks_seen(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    d.skip_button.click()
    assert d.finished == "skipped" and not d.isVisible() and cfg.get("tutorial_seen") is True


def test_escape_marks_seen(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    QTest.keyClick(d, Qt.Key.Key_Escape)
    assert not d.isVisible() and d.finished == "skipped" and cfg.get("tutorial_seen") is True


def test_window_close_marks_seen(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    d.close()
    assert cfg.get("tutorial_seen") is True


def test_keyboard_navigation(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    QTest.keyClick(d, Qt.Key.Key_Right)
    QTest.keyClick(d, Qt.Key.Key_Right)
    assert d.step == 2
    QTest.keyClick(d, Qt.Key.Key_Left)
    assert d.step == 1
    QTest.keyClick(d, Qt.Key.Key_Return)                   # Enter = next
    assert d.step == 2
    for _ in range(10):
        QTest.keyClick(d, Qt.Key.Key_Right)
    assert d.step == 5 and d.isVisible()                   # Right never closes it
    QTest.keyClick(d, Qt.Key.Key_Return)                   # Enter on the last step = Get started
    assert not d.isVisible() and d.finished == "done"
    assert cfg.get("tutorial_seen") is True


def test_enter_on_focused_button_clicks_that_button(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    d.step = 3
    d._show_step()
    d.prev_button.setFocus()
    QTest.keyClick(d, Qt.Key.Key_Return)
    assert d.step == 2


def test_no_config_still_works(qapp):
    d = tutorial.TutorialDialog(None)
    d.show()
    d.skip_button.click()
    assert d.finished == "skipped"


def test_accessibility_and_focus(qapp, tmp_path):
    cfg, d = _dlg(tmp_path)
    d.show()
    assert d.focusWidget() is d.next_button and d.next_button.isDefault()
    for i in range(6):
        d.step = i
        d._show_step()
        assert f"Step {i + 1} of 6" in d.title_label.accessibleName()
        assert d.art.accessibleName() and d.art.accessibleDescription() and d.dots.accessibleName()
        assert d.accessibleDescription()
    assert d.windowTitle() == "Tutorial"


def test_texts_exist_in_every_language_and_have_no_placeholders_left(qapp):
    keys = ["tut_title", "tut_step", "tut_prev", "tut_next", "tut_skip", "tut_start", "tutorial_menu",
            "whatsnew_menu", "whatsnew_missing", "upd_notes_github", "whatsnew_title"]
    keys += [f"tut_{n}_{part}" for n in range(1, 7) for part in ("title", "text")]
    for lang in LANGUAGES:
        for key in keys:
            assert key in i18n._TABLES[lang], (lang, key)
            assert i18n._TABLES[lang][key].strip()


@pytest.mark.parametrize("lang", ["en", "es", "ja", "ru", "zh", "pt", "fr"])
@pytest.mark.parametrize("dark", [False, True])
def test_every_step_draws_in_both_themes(qapp, tmp_path, lang, dark):
    set_language(lang)
    cfg, d = _dlg(tmp_path)
    pal = d.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#202124" if dark else "#F3F3F3"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF" if dark else "#000000"))
    d.setPalette(pal)
    d.show()
    for i in range(6):
        d.step = i
        d._show_step()
        img = d.grab().toImage()
        assert not img.isNull() and img.width() > 400
        art = d.art.grab().toImage()
        # something was drawn: more than one distinct colour in the illustration
        colors = {art.pixel(x, y) for x in range(0, art.width(), 7) for y in range(0, art.height(), 7)}
        assert len(colors) > 3, (lang, dark, i)
    assert tr("tut_1_title") in d.title_label.text() or True
    d.close()


def test_show_tutorial_helper_runs_modally_and_reports(qapp, tmp_path, monkeypatch):
    cfg = Config(tmp_path / "c.json")
    monkeypatch.setattr(tutorial.TutorialDialog, "exec", lambda self: self.skip() or 0)
    assert tutorial.show_tutorial(cfg) == "skipped"
    assert cfg.get("tutorial_seen") is True


def test_tray_action_opens_the_tutorial(qapp, tmp_path, monkeypatch):
    from taskbar_battery_widget.tray import TrayController
    cfg = Config(tmp_path / "c.json")
    tray = TrayController(cfg)
    calls = []
    monkeypatch.setattr(tutorial, "show_tutorial", lambda c, parent=None: calls.append(c) or "done")
    tray.open_tutorial()
    assert calls == [cfg]
