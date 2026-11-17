from taskbar_battery_widget.charge import ChargeEstimator as _Real


def ChargeEstimator():
    # the classic rules, without the settling period after (re)connect (tested below)
    return _Real(settle_s=0)


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


def test_reconnect_placeholder_jump_is_not_charging():
    """Regression: Xbox controller over Bluetooth reported 50 % on connect and 73 % four seconds later."""
    e = _Real()
    assert e.update("x", 50, 0) is False
    assert e.update("x", 73, 4) is False
    assert e.update("x", 73, 600) is False          # flat afterwards: still not charging


def test_rise_in_settling_period_only_moves_baseline():
    e = _Real()
    e.update("x", 50, 0)
    e.update("x", 53, 60)                            # within 120 s of connecting
    assert e.update("x", 54, 300) is False           # +1 from the new baseline 53
    assert e.update("x", 55, 400) is True            # +2: a real charge is still detected


def test_reconnect_starts_a_new_settling_period():
    e = _Real()
    e.update("x", 50, 0)
    e.update("x", None, 1000)                        # disconnected
    e.update("x", 40, 2000)
    assert e.update("x", 70, 2003) is False


def test_impossible_jump_after_settling_is_not_charging():
    e = _Real()
    e.update("x", 40, 0)
    e.update("x", 40, 600)
    assert e.update("x", 60, 630) is False           # +20 in 30 s
    assert e.update("x", 62, 900) is True            # then a normal rise counts


def test_big_rise_over_a_long_gap_still_counts():
    e = _Real()
    e.update("x", 40, 0)
    e.update("x", 40, 600)
    assert e.update("x", 55, 1500) is True           # +15 over 15 min is plausible
