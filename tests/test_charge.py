from taskbar_battery_widget.charge import ChargeEstimator


def test_first_sample_not_charging():
    assert ChargeEstimator().update("a", 50, 0) is False


def test_rise_of_two_marks_charging():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    assert e.update("a", 52, 60) is True
    assert e.update("a", 52, 120) is True


def test_single_point_rise_not_enough():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    assert e.update("a", 51, 60) is False


def test_two_single_rises_accumulate_in_window():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    e.update("a", 51, 60)
    assert e.update("a", 52, 120) is True


def test_timeout_clears_without_rise():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    e.update("a", 53, 60)
    assert e.update("a", 53, 60 + 1799) is True
    assert e.update("a", 53, 60 + 1800) is False


def test_rise_extends_charging():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    e.update("a", 53, 60)
    assert e.update("a", 54, 1500) is True
    assert e.update("a", 54, 1500 + 1000) is True


def test_drop_clears():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    e.update("a", 53, 60)
    assert e.update("a", 52, 120) is False


def test_slow_rise_outside_window_does_not_trigger():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    assert e.update("a", 51, 2000) is False
    assert e.update("a", 52, 4000) is False


def test_none_level_resets_and_devices_independent():
    e = ChargeEstimator()
    e.update("a", 50, 0)
    e.update("b", 50, 0)
    e.update("a", 55, 10)
    assert e.update("a", None, 20) is False
    assert e.update("b", 50, 30) is False
