"""Unit tests for the Compx 2.4 GHz keyboard receiver battery reader (fake handles only)."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import compx, device_history, hid_win, wireless
from taskbar_battery_widget.models import visibility_key

# Real reports captured from an AULA F75 receiver (88 %, then 95 % and 100 % on the cable).
CAPTURED = [
    ("130A010004055811000000000000000000000090", 88),
    ("130A01000405581000000000000000000000008F", 88),
    ("130A010004055801000000000000000000000080", 88),
    ("130A010004055F01000000000000000000000087", 95),
    ("130A010004055D11000000000000000000000095", 93),
    ("130A01000405640100000000000000000000008C", 100),
]


def push(pct: int, flags: int = 0x01) -> bytes:
    b = bytearray(20)
    b[:6] = compx.POWER_HEADER
    b[6], b[7] = pct & 0xFF, flags
    b[19] = sum(b[:19]) & 0xFF
    return bytes(b)


def f75_info(path="hid#vid_3554&pid_fa09&mi_02&col01#8&1&0&0000", product="2.4G Wireless Receiver",
             vid=0x3554, pid=0xFA09, page=0xFF02, usage=0x0002, in_len=20, out_len=20):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, feat_len=0, product=product)


class FakeHandle:
    """Hands out queued input reports; records any write attempt."""

    def __init__(self, reports=()):
        self.queue: list[bytes] = list(reports)
        self.sent: list[bytes] = []
        self.opened = 0
        self.closed = threading.Event()

    def __enter__(self):
        self.opened += 1
        return self

    def __exit__(self, *a):
        self.closed.set()

    def write(self, data, timeout_ms=500):
        self.sent.append(bytes(data))
        return True

    def read(self, timeout_ms=0):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(0.005)
        return None


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    compx.stop_all()
    monkeypatch.setattr(compx, "LAST_KNOWN_PATH", device_history.last_known_path(tmp_path))
    yield
    compx.stop_all()


def scan(h, infos=None, budget=1.0):
    return compx.read(infos if infos is not None else [f75_info()], time.monotonic() + budget, None,
                      handle_factory=lambda path, in_len=0: h)


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def scan_until(h, cond, infos=None):
    out = []
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        out = scan(h, infos)
        if cond(out):
            return out
        time.sleep(0.01)
    return out


def test_vendor_ids_and_pid_narrowing():
    assert compx.VENDOR_IDS == (0x3554,)
    assert 0x3554 in wireless.EXTRA_VENDOR_IDS
    assert hid_win._path_matches_vendor(r"\\?\hid#vid_3554&pid_fa09&mi_01&col01#x", (0x3554,))
    assert not hid_win._path_matches_vendor(r"\\?\hid#vid_3554&pid_f58a&mi_01&col01#x", (0x3554,))


@pytest.mark.parametrize("hexs,pct", CAPTURED)
def test_captured_reports_parse(hexs, pct):
    assert compx.parse_power_report(bytes.fromhex(hexs)) == pct


@pytest.mark.parametrize("bad", [
    None, b"", push(50)[:19],
    push(50)[:19] + b"\x00",                                   # bad checksum
    bytes((0x13, 0x0B)) + push(50)[2:],                        # other status type
    bytes(20),                                                 # GetInputReport idle answer
    push(0), push(101), push(0xFF),
])
def test_bad_or_unrelated_reports_are_ignored(bad):
    assert compx.parse_power_report(bad) is None


def test_listener_reports_last_pushed_level_and_never_writes():
    h = FakeHandle([bytes(20), push(88, 0x11), push(88, 0x10), push(95)])
    out = scan_until(h, lambda o: o and o[0].battery == 95)
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.name, d.kind, d.battery, d.charging, d.connected, d.link, d.source) == (
        "compx:fa09", compx.DEFAULT_NAME, "keyboard", 95, False, True, "2.4ghz", "hid")
    assert d.updated is not None
    assert h.sent == [] and h.opened == 1


def test_listed_without_level_until_first_push():
    h = FakeHandle()
    out = scan_until(h, lambda o: bool(o))
    assert len(out) == 1 and out[0].battery is None and out[0].updated is None
    h.queue.append(push(70))
    out = scan_until(h, lambda o: o and o[0].battery == 70)
    assert out[0].battery == 70


def test_one_listener_per_receiver():
    h = FakeHandle([push(60)])
    scan_until(h, lambda o: o and o[0].battery == 60)
    for _ in range(5):
        scan(h)
    assert h.opened == 1


def test_listener_stops_when_receiver_disappears():
    h = FakeHandle([push(60)])
    scan_until(h, lambda o: o and o[0].battery == 60)
    assert scan(h, infos=[]) == []
    assert h.closed.wait(3.0)


def test_unopenable_collection_is_not_listed_and_never_raises():
    calls = []

    def boom(path, in_len=0):
        calls.append(path)
        raise OSError("x")
    for _ in range(3):
        assert compx.read([f75_info()], time.monotonic() + 1, None, handle_factory=boom) == []
        time.sleep(0.05)
    assert len(calls) == 1                      # no busy retry right after a failure
    assert compx.read(None, time.monotonic() + 1) == []


def test_restarted_listener_keeps_last_level(monkeypatch):
    class Dies(FakeHandle):
        def read(self, timeout_ms=0):
            if self.queue:
                return self.queue.pop(0)
            raise OSError("unplugged")
    monkeypatch.setattr(compx, "RETRY_DELAY_S", 0.0)
    first = Dies([push(77)])
    scan(first)
    assert wait_for(lambda: compx._listeners and compx._listeners[f75_info().path].failed_at)
    h = FakeHandle()
    out = scan_until(h, lambda o: bool(o))
    assert out[0].battery == 77


def test_write_is_impossible():
    with pytest.raises(ValueError):
        compx._ListenOnly(FakeHandle()).write(push(50))


def test_cancel_or_deadline_skips_everything():
    h = FakeHandle([push(50)])
    ev = threading.Event()
    ev.set()
    assert compx.read([f75_info()], time.monotonic() + 1, ev, handle_factory=lambda p, in_len=0: h) == []
    assert compx.read([f75_info()], time.monotonic() - 1, None, handle_factory=lambda p, in_len=0: h) == []
    assert h.opened == 0


@pytest.mark.parametrize("kw", [
    {"pid": 0xFA0A},
    {"vid": 0x258A},                    # the F75 on its cable
    {"page": 0x000C, "usage": 1, "in_len": 3, "out_len": 0},   # consumer collection
    {"page": 0xFF04, "in_len": 0, "out_len": 0},               # feature-only vendor collection
    {"usage": 0x0001},
    {"in_len": 64, "out_len": 64},
    {"path": "hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&00023554_pid&fa09#x"},  # Bluetooth
])
def test_collection_selection(kw):
    h = FakeHandle([push(50)])
    assert scan(h, infos=[f75_info(**kw)]) == []
    time.sleep(0.05)
    assert h.opened == 0


def test_product_name_kept_when_specific():
    assert compx._display_name("AULA F75") == "AULA F75"
    assert compx._display_name("2.4G Wireless Receiver") == compx.DEFAULT_NAME


def test_two_receivers_get_distinct_ids():
    handles = {"hid#a": FakeHandle([push(50)]), "hid#b": FakeHandle([push(60)])}
    infos = [f75_info(path="hid#a"), f75_info(path="hid#b")]
    out = []
    end = time.monotonic() + 2
    while time.monotonic() < end and not (len(out) == 2 and all(d.battery for d in out)):
        out = compx.read(infos, time.monotonic() + 1, None,
                         handle_factory=lambda p, in_len=0: handles[p])
        time.sleep(0.01)
    assert [d.battery for d in out] == [50, 60]
    assert out[0].id == "compx:fa09" and out[1].id.startswith("compx:fa09:")


def test_change_callback_fires_only_when_the_level_changes():
    calls = []
    cb = lambda: calls.append(1)  # noqa: E731
    compx.add_change_callback(cb)
    compx.add_change_callback(cb)               # registering twice is a no-op
    try:
        h = FakeHandle([push(88, 0x11), push(88, 0x10), push(88), push(90)])
        scan_until(h, lambda o: o and o[0].battery == 90)
        assert wait_for(lambda: len(calls) == 2)
        time.sleep(0.05)
        assert len(calls) == 2                  # 88 once, 90 once; repeats of 88 are ignored
    finally:
        compx.remove_change_callback(cb)
    compx.remove_change_callback(cb)            # removing twice is harmless
    assert compx._callbacks == []


def test_failing_callback_does_not_kill_the_listener():
    def bad():
        raise RuntimeError("x")
    compx.add_change_callback(bad)
    try:
        h = FakeHandle([push(50)])
        scan_until(h, lambda o: o and o[0].battery == 50)
        h.queue.append(push(49))
        out = scan_until(h, lambda o: o and o[0].battery == 49)
        assert out[0].battery == 49
    finally:
        compx.remove_change_callback(bad)


def _restart_app():
    """Simulate a new run: no listeners."""
    compx.stop_all()


def _save_via_history(devices):
    """What the app does with every scan: device_history records live readings in last_known.json."""
    hist = device_history.DeviceHistory(base=compx.LAST_KNOWN_PATH.parent)
    hist.update(devices)
    assert compx.LAST_KNOWN_PATH.exists()


def _write_last_known(battery, ts, dev_id="compx:fa09"):
    compx.LAST_KNOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"name": "2.4 GHz keyboard", "kind": "keyboard", "link": "2.4ghz", "battery": battery,
             "charging": False, "ts": ts, "disc": None, "seen": ts}
    compx.LAST_KNOWN_PATH.write_text(json.dumps({"devices": {visibility_key(dev_id): entry}}))


def test_level_survives_an_app_restart():
    out = scan_until(FakeHandle([push(83)]), lambda o: o and o[0].battery == 83)
    _save_via_history(out)
    _restart_app()
    out = scan_until(FakeHandle(), lambda o: bool(o))
    assert out[0].battery == 83 and out[0].connected and out[0].updated is not None


def test_fresh_push_replaces_the_remembered_level():
    _write_last_known(83, time.time() - 60)
    h = FakeHandle()
    scan_until(h, lambda o: o and o[0].battery == 83)
    h.queue.append(push(100))
    assert scan_until(h, lambda o: o and o[0].battery == 100)[0].battery == 100


@pytest.mark.parametrize("content", [
    None,                                       # no file
    "not json",
    '{"devices": []}',
    '{"%s": {"battery": 70}}',                  # not under "devices"
])
def test_missing_or_corrupt_cache_is_ignored(content):
    if content is not None:
        compx.LAST_KNOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        compx.LAST_KNOWN_PATH.write_text(content.replace("%s", visibility_key("compx:fa09"))
                                         if "%s" in content else content)
    assert scan_until(FakeHandle(), lambda o: bool(o))[0].battery is None


@pytest.mark.parametrize("battery,age", [(70, compx.REMEMBER_S + 60), (300, 60), (None, 60), (0, 60)])
def test_old_or_invalid_saved_level_is_ignored(battery, age):
    _write_last_known(battery, time.time() - age)
    assert scan_until(FakeHandle(), lambda o: bool(o))[0].battery is None


def test_other_devices_in_the_cache_are_not_used():
    _write_last_known(70, time.time() - 60, dev_id="logi:c539:01")
    assert scan_until(FakeHandle(), lambda o: bool(o))[0].battery is None


def test_reader_never_writes_the_cache():
    scan_until(FakeHandle([push(61)]), lambda o: o and o[0].battery == 61)
    time.sleep(0.05)
    assert not compx.LAST_KNOWN_PATH.exists()


def test_saved_level_is_a_last_value_until_the_keyboard_pushes():
    # idle keeps the saved timestamp in last_known.json, so REMEMBER_S can still expire
    saved = time.time() - 6 * 3600
    _write_last_known(70, saved)
    out = scan_until(FakeHandle(), lambda o: bool(o))
    assert out[0].battery == 70 and out[0].connected and out[0].idle
    assert out[0].updated == pytest.approx(saved)


def test_a_push_makes_the_level_live_again():
    _write_last_known(70, time.time() - 6 * 3600)
    out = scan_until(FakeHandle([push(64)]), lambda o: o and o[0].battery == 64)
    assert not out[0].idle


def state(awake: bool) -> bytes:
    b = bytearray(20)
    b[:6] = compx.STATE_HEADER
    b[6] = 1 if awake else 0
    b[19] = sum(b[:19]) & 0xFF
    return bytes(b)


@pytest.mark.parametrize("hexs,want", [
    ("130A010004020000000000000000000000000024", False),   # fell asleep / left 2.4 GHz mode
    ("130A010004020100000000000000000000000025", True),    # woke / back on 2.4 GHz
    ("130A010004070000000000000000000000000029", None),    # unknown type 0x07
    ("130A010004020200000000000000000000000026", None),    # unknown state value
    ("130A010004020000000000000000000000000025", None),    # bad checksum
])
def test_captured_state_reports(hexs, want):
    assert compx.parse_state_report(bytes.fromhex(hexs)) is want
    assert compx.parse_power_report(bytes.fromhex(hexs)) is None


def test_sleep_and_wake_follow_the_keyboard():
    calls = []
    cb = lambda: calls.append(1)  # noqa: E731
    compx.add_change_callback(cb)
    try:
        h = FakeHandle([push(86)])
        scan_until(h, lambda o: o and o[0].battery == 86)
        assert scan(h)[0].connected
        n = len(calls)
        h.queue.append(state(False))
        out = scan_until(h, lambda o: o and not o[0].connected)
        assert out[0].battery == 86 and not out[0].connected and not out[0].idle
        assert len(calls) > n                       # sleeping refreshes the widget right away
        h.queue.append(state(True))
        out = scan_until(h, lambda o: o and o[0].connected)
        assert out[0].battery == 86 and out[0].connected
    finally:
        compx.remove_change_callback(cb)


def test_power_on_report_marks_awake():
    h = FakeHandle([push(86), state(False)])
    scan_until(h, lambda o: o and not o[0].connected)
    h.queue.extend([state(True), bytes.fromhex("130A010004070000000000000000000000000029"), push(85)])
    out = scan_until(h, lambda o: o and o[0].battery == 85 and o[0].connected)
    assert out[0].battery == 85 and out[0].connected


def test_level_push_alone_reconnects():
    h = FakeHandle([push(86), state(False)])
    scan_until(h, lambda o: o and not o[0].connected)
    h.queue.append(push(85))
    out = scan_until(h, lambda o: o and o[0].connected)
    assert out[0].battery == 85


def test_asleep_as_moon_when_configured(monkeypatch):
    monkeypatch.setattr(compx, "ASLEEP_AS_DISCONNECTED", False)
    h = FakeHandle([push(86), state(False)])
    out = scan_until(h, lambda o: o and o[0].idle)
    assert out[0].connected and out[0].idle and out[0].battery == 86


def _unknown(n: int) -> bytes:
    b = bytearray(20)
    b[0], b[1], b[2] = 0x13, 0x0B, n & 0xFF
    return bytes(b)


def test_unrecognized_reports_are_logged_once_and_capped(caplog, monkeypatch):
    monkeypatch.setattr(compx, "MAX_LOGGED_UNKNOWN", 3)
    caplog.set_level("INFO", logger=compx.__name__)
    h = FakeHandle([_unknown(1), _unknown(1), _unknown(2), push(88), _unknown(3), _unknown(4)])
    scan_until(h, lambda o: o and o[0].battery == 88)
    assert wait_for(lambda: not h.queue)
    lines = [r.getMessage() for r in caplog.records if "unrecognized report" in r.getMessage()]
    assert len(lines) == 3
    assert lines[0].endswith("13 0B 01" + " 00" * 17)
    assert not any("0A 01 00 04 05" in ln for ln in lines)      # recognized pushes are not logged
    assert h.sent == []
