"""Animated custom icons: import limits, stored strip + timings, frame selection, name validation, cleanup."""
import json

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from taskbar_battery_widget import custom_icons as ci
from taskbar_battery_widget.config import clean_custom_icons

from tests._anim_data import BLUE, GREEN, RED, make_gif, make_webp_anim, webp_still


@pytest.fixture(autouse=True)
def _icons(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    ci.clear_cache()
    yield
    ci.clear_cache()


def put(tmp_path, name, data):
    f = tmp_path / name
    f.write_bytes(data)
    return f


def sidecar(name):
    return json.loads((ci.icons_dir() / ci.sidecar_name(name)).read_text())


def sheet(name):
    img = QImage(str(ci.icons_dir() / name))
    assert not img.isNull()
    return img


def rgb(img, x, y):
    c = QColor.fromRgba(img.pixel(x, y))
    return c.red(), c.green(), c.blue(), c.alpha()


# ------------------------------------------------------------------ import
def test_animated_gif_becomes_strip_with_timings(tmp_path):
    name = ci.import_image(put(tmp_path, "a.gif", make_gif(16, 16, [100, 200, 300])))
    assert name.endswith(".anim.png") and ci.safe_name(name) and ci.is_animated(name)
    assert sidecar(name) == {"v": 1, "delays": [100, 200, 300]}
    img = sheet(name)
    assert (img.width(), img.height()) == (64 * 3, 64)
    assert rgb(img, 32, 32) == (255, 0, 0, 255)
    assert rgb(img, 64 + 32, 32) == (0, 255, 0, 255)
    assert rgb(img, 128 + 32, 32) == (0, 0, 255, 255)
    # the original GIF is not kept: only the strip and its timings are in the folder
    assert sorted(f.name for f in ci.icons_dir().iterdir()) == sorted([name, ci.sidecar_name(name)])
    assert ci.usable(name)


def test_frames_are_fitted_and_centred_with_transparency(tmp_path):
    name = ci.import_image(put(tmp_path, "w.gif", make_gif(32, 16, [100, 100])))
    img = sheet(name)
    assert rgb(img, 32, 32)[3] == 255           # centre is covered
    assert rgb(img, 32, 2)[3] == 0              # a 2:1 frame leaves transparent bands above and below
    assert rgb(img, 32, 61)[3] == 0


def test_delays_are_clamped(tmp_path):
    name = ci.import_image(put(tmp_path, "d.gif", make_gif(16, 16, [30, 5000, 100])))
    assert sidecar(name)["delays"] == [ci.MIN_FRAME_MS, ci.MAX_FRAME_MS, 100]


def test_more_than_48_frames_are_subsampled_keeping_the_duration(tmp_path):
    name = ci.import_image(put(tmp_path, "m.gif", make_gif(16, 16, [100] * 60)))
    delays = sidecar(name)["delays"]
    assert len(delays) == ci.MAX_ANIM_FRAMES == sheet(name).width() // 64
    assert sum(delays) == 6000                  # each kept frame lasts as long as the frames it stands for
    assert all(ci.MIN_FRAME_MS <= d <= ci.MAX_FRAME_MS for d in delays)
    img = sheet(name)
    assert rgb(img, 32, 32) == (255, 0, 0, 255)             # frame 0 is the first source frame


def test_exactly_48_frames_are_all_kept(tmp_path):
    name = ci.import_image(put(tmp_path, "e.gif", make_gif(16, 16, [100] * 48)))
    assert sidecar(name)["delays"] == [100] * 48


def test_over_200_frames_rejected(tmp_path):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(put(tmp_path, "long.gif", make_gif(8, 8, [66] * 201)))
    assert e.value.code == ci.ERR_ANIM_LONG
    assert not ci.icons_dir().exists() or not list(ci.icons_dir().iterdir())


def test_exactly_200_frames_of_66ms_exceed_the_duration(tmp_path):
    # 200 x 66 ms = 13.2 s > 10 s
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(put(tmp_path, "t.gif", make_gif(8, 8, [60] * 200)))
    assert e.value.code == ci.ERR_ANIM_LONG


def test_duration_over_10_seconds_rejected_and_10_accepted(tmp_path):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(put(tmp_path, "l.gif", make_gif(8, 8, [2000] * 6)))
    assert e.value.code == ci.ERR_ANIM_LONG
    name = ci.import_image(put(tmp_path, "ok.gif", make_gif(8, 8, [2000] * 5)))
    assert sum(sidecar(name)["delays"]) == 10_000


def test_oversize_dimensions_rejected_before_decoding(tmp_path):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(put(tmp_path, "big.gif", make_gif(2049, 4, [100, 100])))
    assert e.value.code == ci.ERR_DIMENSIONS


def test_pixel_budget_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "MAX_ANIM_PIXELS", 16 * 16 * 3 - 1)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(put(tmp_path, "px.gif", make_gif(16, 16, [100, 100, 100])))
    assert e.value.code == ci.ERR_DIMENSIONS


def test_single_frame_gif_stays_static(tmp_path):
    name = ci.import_image(put(tmp_path, "one.gif", make_gif(16, 16, [100])))
    assert name.endswith(".gif") and not ci.is_animated(name)
    assert [f.name for f in ci.icons_dir().iterdir()] == [name]
    assert ci.pixmap_for(name, 16, 1.0) is not None


def test_truncated_gif_is_unreadable_or_static_never_a_crash(tmp_path):
    data = make_gif(16, 16, [100, 100, 100])
    try:
        name = ci.import_image(put(tmp_path, "cut.gif", data[:60]))
    except ci.IconImportError as e:
        assert e.code in (ci.ERR_UNREADABLE, ci.ERR_ANIM_LONG)
    else:
        assert ci.safe_name(name)


def test_animated_webp_becomes_strip(tmp_path):
    name = ci.import_image(put(tmp_path, "a.webp", make_webp_anim([RED, GREEN, BLUE], [100, 200, 300])))
    assert ci.is_animated(name)
    assert sidecar(name)["delays"] == [100, 200, 300]
    assert sheet(name).width() == 192


def test_still_webp_is_stored_as_png(tmp_path):
    name = ci.import_image(put(tmp_path, "s.webp", webp_still(RED, 64)))
    assert name.endswith(".png") and not ci.is_animated(name)
    assert ci.usable(name)


def test_import_is_deduplicated(tmp_path):
    a = ci.import_image(put(tmp_path, "a.gif", make_gif(16, 16, [100, 100])))
    b = ci.import_image(put(tmp_path, "b.gif", make_gif(16, 16, [100, 100])))
    assert a == b and len(list(ci.icons_dir().iterdir())) == 2


def test_different_timings_give_different_names(tmp_path):
    a = ci.import_image(put(tmp_path, "a.gif", make_gif(16, 16, [100, 100])))
    b = ci.import_image(put(tmp_path, "b.gif", make_gif(16, 16, [100, 200])))
    assert a != b


# ------------------------------------------------------------------ names
GOOD = "0123456789abcdef"


@pytest.mark.parametrize("name", [GOOD + ".png", GOOD + ".gif", GOOD + ".jpeg", GOOD + ".anim.png"])
def test_safe_names_accepted(name):
    assert ci.safe_name(name)


@pytest.mark.parametrize("name", [
    GOOD + ".anim.json", GOOD + ".anim.PNG", GOOD + ".ANIM.png", GOOD + ".anim.png.png", GOOD + ".anim.gif",
    GOOD + ".anim", GOOD + "anim.png", GOOD + ".anim.png ", " " + GOOD + ".anim.png", GOOD + ".anim.png\n",
    GOOD[:15] + ".anim.png", GOOD + "0.anim.png", GOOD.upper() + ".anim.png", "../" + GOOD + ".anim.png",
    "x/" + GOOD + ".anim.png", GOOD + "\\.anim.png", GOOD + ".webp", GOOD + ".svg", GOOD + ".tmp",
    GOOD + ".anim.png.tmp", ".anim.png", "", None, 5, b"x.anim.png"])
def test_malicious_names_rejected(name):
    assert not ci.safe_name(name)
    assert not ci.is_animated(name)


def test_sidecar_names_validated_separately():
    assert ci.safe_file(GOOD + ".anim.json") and ci.safe_file(GOOD + ".anim.png") and ci.safe_file(GOOD + ".gif")
    for bad in (GOOD + ".json", GOOD + ".anim.json.txt", "../" + GOOD + ".anim.json", GOOD + ".ANIM.json",
                GOOD[:15] + ".anim.json", None):
        assert not ci.safe_file(bad)
    assert ci.sidecar_name(GOOD + ".anim.png") == GOOD + ".anim.json"


def test_config_accepts_anim_name_and_drops_sidecar_reference():
    out = clean_custom_icons({"mouse": GOOD + ".anim.png", "keyboard": GOOD + ".anim.json",
                              "headset": "../x.anim.png"}, True)
    assert out == {"mouse": GOOD + ".anim.png"}


def test_set_maps_accepts_animated_names():
    ci.set_maps({"mouse": GOOD + ".anim.png", "tv": GOOD + ".anim.json"}, {})
    assert ci.file_for("mouse", "h:0000000000000000") == GOOD + ".anim.png"
    assert ci.file_for("tv", "h:0000000000000000") is None


# ------------------------------------------------------------------ cleanup
def test_cleanup_removes_strip_and_timings_together(tmp_path):
    keep = ci.import_image(put(tmp_path, "k.gif", make_gif(16, 16, [100, 100])))
    drop = ci.import_image(put(tmp_path, "d.gif", make_gif(16, 16, [100, 200])))
    (ci.icons_dir() / "notes.txt").write_text("mine")
    ci.cleanup_unused({keep})
    names = {f.name for f in ci.icons_dir().iterdir()}
    assert names == {keep, ci.sidecar_name(keep), "notes.txt"}
    assert drop not in names and ci.sidecar_name(drop) not in names
    ci.cleanup_unused(set())
    assert {f.name for f in ci.icons_dir().iterdir()} == {"notes.txt"}


def test_cleanup_removes_orphan_sidecar(tmp_path):
    ci.icons_dir().mkdir(parents=True)
    orphan = ci.icons_dir() / (GOOD + ".anim.json")
    orphan.write_text("{}")
    ci.cleanup_unused({GOOD + ".anim.png"})        # its strip is "used" but absent: the sidecar is kept with the name
    assert orphan.exists()
    ci.cleanup_unused(set())
    assert not orphan.exists()


# ------------------------------------------------------------------ frames
def test_frame_for_changes_with_time_and_is_first_when_paused(tmp_path):
    name = ci.import_image(put(tmp_path, "a.gif", make_gif(16, 16, [100, 200, 300])))
    first = ci.frame_for(name, 0, 16, 1.0)
    second = ci.frame_for(name, 150, 16, 1.0)
    third = ci.frame_for(name, 450, 16, 1.0)
    px = [QColor(p.toImage().pixel(8, 8)).getRgb()[:3] for p in (first, second, third)]
    assert px == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert QColor(ci.frame_for(name, None, 16, 1.0).toImage().pixel(8, 8)).getRgb()[:3] == (255, 0, 0)
    assert QColor(ci.frame_for(name, 600, 16, 1.0).toImage().pixel(8, 8)).getRgb()[:3] == (255, 0, 0)   # loops
    assert QColor(ci.frame_for(name, 600 * 7 + 100, 16, 1.0).toImage().pixel(8, 8)).getRgb()[:3] == (0, 255, 0)


def test_frame_boundaries(tmp_path):
    name = ci.import_image(put(tmp_path, "b.gif", make_gif(16, 16, [100, 200])))
    anim = ci._anim_for(name)
    assert [ci.frame_index(anim, t) for t in (0, 99, 100, 299, 300, 399)] == [0, 0, 1, 1, 0, 0]
    assert ci.ms_to_next_frame(name, 0) == 100 and ci.ms_to_next_frame(name, 99) == 1
    assert ci.ms_to_next_frame(name, 100) == 200 and ci.ms_to_next_frame(name, 299) == 1
    assert ci.ms_to_next_frame(GOOD + ".png", 0) is None


def test_frames_are_cached_per_size_and_dpr(tmp_path):
    name = ci.import_image(put(tmp_path, "c.gif", make_gif(16, 16, [100, 200])))
    a = ci.frame_for(name, 0, 16, 1.0)
    assert ci.frame_for(name, 50, 16, 1.0) is a                # same frame, same object: no re-scaling
    big = ci.frame_for(name, 0, 16, 2.0)
    assert (a.width(), big.width()) == (16, 32) and big.devicePixelRatio() == 2.0
    assert len(ci._anims) == 1                                 # the strip is decoded once


def test_pixmap_for_an_animation_is_its_first_frame(tmp_path):
    name = ci.import_image(put(tmp_path, "p.gif", make_gif(16, 16, [100, 200, 300])))
    assert QColor(ci.pixmap_for(name, 16, 1.0).toImage().pixel(8, 8)).getRgb()[:3] == (255, 0, 0)


def test_draw_custom_icon_uses_the_given_time(tmp_path):
    name = ci.import_image(put(tmp_path, "dr.gif", make_gif(16, 16, [100, 200, 300])))

    def draw(now):
        pm = QPixmap(16, 16)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        assert ci.draw_custom_icon(p, QRectF(0, 0, 16, 16), name, 1.0, now)
        p.end()
        return QColor(pm.toImage().pixel(8, 8)).getRgb()[:3]

    assert draw(None) == (255, 0, 0) and draw(150) == (0, 255, 0) and draw(450) == (0, 0, 255)


def test_sheet_memory_for_48_frames(tmp_path):
    name = ci.import_image(put(tmp_path, "mem.gif", make_gif(16, 16, [100] * 48)))
    anim = ci._anim_for(name)
    assert anim.sheet.sizeInBytes() == 48 * 64 * 64 * 4        # 786 432 bytes


# ------------------------------------------------------------------ damaged files
@pytest.mark.parametrize("meta", [
    "not json", "[]", "{}", '{"v":2,"delays":[100,100]}', '{"v":1,"delays":[100]}', '{"v":1,"delays":[100,100,100]}',
    '{"v":1,"delays":[100,"x"]}', '{"v":1,"delays":[100,true]}', '{"v":1,"delays":[100,10]}',
    '{"v":1,"delays":[100,5000]}', '{"v":1,"delays":[100,100],"x":1}', '{"v":true,"delays":[100,100]}',
    '{"v":1,"delays":[2000,2000,2000,2000,2000,2000]}', "x" * 5000])
def test_bad_sidecar_makes_the_icon_unusable(tmp_path, meta):
    name = ci.import_image(put(tmp_path, "s.gif", make_gif(16, 16, [100, 100])))
    (ci.icons_dir() / ci.sidecar_name(name)).write_text(meta)
    ci.clear_cache()
    assert not ci.usable(name)
    assert ci.frame_for(name, 0, 16, 1.0) is None and ci.pixmap_for(name, 16, 1.0) is None


def test_missing_sidecar_or_strip_or_wrong_sheet_size(tmp_path):
    name = ci.import_image(put(tmp_path, "s.gif", make_gif(16, 16, [100, 100])))
    (ci.icons_dir() / ci.sidecar_name(name)).write_text(json.dumps({"v": 1, "delays": [100, 100, 100]}))
    ci.clear_cache()
    assert not ci.usable(name)                     # 3 timings for a 2-frame strip
    (ci.icons_dir() / ci.sidecar_name(name)).unlink()
    ci.clear_cache()
    assert not ci.usable(name)
    (ci.icons_dir() / name).write_bytes(b"not a png")
    (ci.icons_dir() / ci.sidecar_name(name)).write_text(json.dumps({"v": 1, "delays": [100, 100]}))
    ci.clear_cache()
    assert not ci.usable(name)


def test_strip_with_an_arbitrary_size_is_refused(tmp_path):
    name = ci.import_image(put(tmp_path, "s.gif", make_gif(16, 16, [100, 100])))
    big = QImage(64 * 2, 2000, QImage.Format.Format_ARGB32)
    big.fill(QColor("red"))
    assert big.save(str(ci.icons_dir() / name))
    ci.clear_cache()
    assert not ci.usable(name)
