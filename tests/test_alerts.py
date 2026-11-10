from taskbar_battery_widget.alerts import AlertManager
from taskbar_battery_widget.config import Config
from taskbar_battery_widget.i18n import set_language
from taskbar_battery_widget.models import Device


def make(tmp_path, **settings):
    cfg = Config(tmp_path / "c.json")
    for k, v in settings.items():
        cfg.set(k, v)
    return cfg


def dev(level, **kw):
    d = dict(id="d1", name="Mouse", kind="mouse", battery=level, connected=True)
    d.update(kw)
    return Device(**d)


def setup_function(_):
    set_language("en")


def test_no_alert_above_threshold(tmp_path):
    am = AlertManager(make(tmp_path))
    assert am.check([dev(21)], 0) == []


def test_alert_at_threshold_with_text(tmp_path):
    am = AlertManager(make(tmp_path))
    [(title, body)] = am.check([dev(20)], 0)
    assert title == "Low battery" and "Mouse" in body and "20%" in body


def test_cooldown_blocks_then_repeats(tmp_path):
    am = AlertManager(make(tmp_path, notification_cooldown_min=60))
    assert len(am.check([dev(15)], 0)) == 1
    assert am.check([dev(14)], 3599) == []
    assert len(am.check([dev(13)], 3600)) == 1


def test_rearm_after_rising_above_threshold_plus_5(tmp_path):
    am = AlertManager(make(tmp_path))
    assert len(am.check([dev(10)], 0)) == 1
    assert am.check([dev(24)], 10) == []      # 24 <= 25: not re-armed yet
    assert am.check([dev(10)], 20) == []      # still in cooldown, not re-armed
    assert am.check([dev(26)], 30) == []      # > threshold+5: re-armed
    assert len(am.check([dev(10)], 40)) == 1  # fires again despite cooldown


def test_disabled_notifications(tmp_path):
    am = AlertManager(make(tmp_path, notifications_enabled=False))
    assert am.check([dev(5)], 0) == []


def test_skips_disconnected_unknown_and_charging(tmp_path):
    am = AlertManager(make(tmp_path))
    assert am.check([dev(5, connected=False), dev(None), dev(5, charging=True)], 0) == []


def test_custom_threshold_and_spanish(tmp_path):
    set_language("es")
    am = AlertManager(make(tmp_path, low_threshold=30))
    [(title, body)] = am.check([dev(30)], 0)
    assert title == "Batería baja" and "30%" in body


def test_per_device_state(tmp_path):
    am = AlertManager(make(tmp_path))
    assert len(am.check([dev(10, id="a"), dev(10, id="b")], 0)) == 2
    assert am.check([dev(10, id="a"), dev(10, id="b")], 5) == []
