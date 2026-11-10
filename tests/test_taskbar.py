from taskbar_battery_widget import taskbar as tb


def info(**kw):
    base = dict(rect=(0, 1032, 1920, 1080), tray_rect=(1602, 1032, 1920, 1080),
                edge="bottom", autohide=False, monitor_rect=(0, 0, 1920, 1080))
    base.update(kw)
    return tb.TaskbarInfo(**base)


def test_anchor_bottom_is_left_of_tray_and_centered():
    r = tb.anchor_widget_rect(info(), 200, 40, gap=4)
    assert r[2] == 1602 - 4
    assert r[2] - r[0] == 200
    assert r[1] == 1032 + (48 - 40) // 2 and r[3] - r[1] == 40


def test_anchor_clamps_height_to_taskbar():
    r = tb.anchor_widget_rect(info(), 100, 90)
    assert r[3] - r[1] == 48


def test_anchor_vertical_stacks_above_tray():
    i = info(rect=(1872, 0, 1920, 1080), tray_rect=(1872, 900, 1920, 1080), edge="right")
    r = tb.anchor_widget_rect(i, 40, 100, gap=4)
    assert r[3] == 900 - 4 and r[3] - r[1] == 100
    assert 1872 <= r[0] and r[2] <= 1920


def test_physical_to_logical_scaling_and_origin():
    x, y, w, h = tb.physical_to_logical_rect((3000, 1500, 3300, 1560), (2880, 0), (1920, 0), 1.5)
    assert (x, y, w, h) == (1920 + 80, 1000, 200, 40)


def test_tucked_detection():
    mon = (0, 0, 1920, 1080)
    assert tb.is_tucked((0, 1078, 1920, 1126), mon, "bottom", True)
    assert not tb.is_tucked((0, 1032, 1920, 1080), mon, "bottom", True)
    assert not tb.is_tucked((0, 1078, 1920, 1126), mon, "bottom", False)


def test_derive_edge_and_fallback_tray():
    mon = (0, 0, 1920, 1080)
    assert tb.derive_edge((0, 1032, 1920, 1080), mon) == "bottom"
    assert tb.derive_edge((0, 0, 1920, 48), mon) == "top"
    assert tb.derive_edge((0, 0, 48, 1080), mon) == "left"
    assert tb.derive_edge((1872, 0, 1920, 1080), mon) == "right"
    assert tb.fallback_tray_rect((0, 1032, 1920, 1080), "bottom") == (1620, 1032, 1920, 1080)


def test_covers_monitor():
    mon = (0, 0, 1920, 1080)
    assert tb.covers_monitor((0, 0, 1920, 1080), mon)
    assert not tb.covers_monitor((0, 0, 1920, 1032), mon)


def test_live_calls_do_not_crash():
    assert isinstance(tb.taskbar_uses_light_theme(), bool)
    i = tb.get_taskbar_info()
    if i:
        assert isinstance(tb.is_fullscreen_foreground(i.monitor_rect), bool)


def test_fullscreen_state_mapping():
    assert tb.state_is_fullscreen(None) is None
    for s in (2, 3, 4):
        assert tb.state_is_fullscreen(s) is True
    for s in (1, 5, 6, 7):
        assert tb.state_is_fullscreen(s) is False


def test_fullscreen_uses_shell_state_over_heuristic(monkeypatch):
    mon = (0, 0, 1920, 1080)
    monkeypatch.setattr(tb, "query_user_notification_state", lambda: 5)
    monkeypatch.setattr(tb, "_covers_fallback", lambda m: True)   # maximized + autohide look
    assert tb.is_fullscreen_foreground(mon) is False
    monkeypatch.setattr(tb, "query_user_notification_state", lambda: 3)
    assert tb.is_fullscreen_foreground(mon) is True
    monkeypatch.setattr(tb, "query_user_notification_state", lambda: None)
    assert tb.is_fullscreen_foreground(mon) is True   # fallback
