"""Unit tests for the AULA F108 Pro 2.4 GHz dongle battery reader (fake handles only)."""
import threading
import time
from types import SimpleNamespace

import pytest

from taskbar_battery_widget import aula, wireless

REQ_HEX = "002001000000000000000000000000000000000000000000000000000000000021"
LIVE_82 = bytes.fromhex("002001005200000000000000000000000000000000000000000000000000000073")
LIVE_81 = bytes.fromhex("002001005100000000000000000000000000000000000000000000000000000072")


def make_reply(pct: int) -> bytes:
    b = bytearray(33)
    b[1], b[2], b[3], b[4] = 0x20, 0x01, 0x00, pct & 0xFF
    b[32] = sum(b[1:32]) & 0xFF
    return bytes(b)


def f108_info(path="hid#vid_05ac&pid_024f&mi_03#7&1&0&0000", product="F108Pro Dongle",
              vid=0x05AC, pid=0x024F, page=0xFF60, usage=0x0061, in_len=33, out_len=33):
    return SimpleNamespace(vid=vid, pid=pid, path=path, usage_page=page, usage=usage,
                           in_len=in_len, out_len=out_len, feat_len=0, product=product)


class FakeAulaHandle:
    """Scripted handle: each write pops one item (bytes, list[bytes], or None) from `script`."""

    def __init__(self, script):
        self.script = list(script)
        self.queue: list[bytes] = []
        self.sent: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        self.queue.clear()

    def write(self, data, timeout_ms=500):
        self.sent.append(bytes(data))
        if self.script:
            nxt = self.script.pop(0)
            if isinstance(nxt, list):
                self.queue.extend(nxt)
            elif nxt is not None:
                self.queue.append(nxt)
        return True

    def read(self, timeout_ms=0):
        return self.queue.pop(0) if self.queue else None


def test_vendor_ids():
    assert aula.VENDOR_IDS == (0x05AC,)


def test_build_request_and_whitelist():
    req = aula.build_request()
    assert len(req) == 33 and req.hex() == REQ_HEX
    for bad in (0x00, 0x05, 0x07, 0x09, 0x0C, 0x10, 0x12, 0xFF):
        with pytest.raises(ValueError):
            aula.build_request(bad)
    raw = FakeAulaHandle([])
    ro = aula._ReadOnly(raw)
    for bad_pkt in (b"\x00\x05\x10" + bytes(30), bytes(33), b""):
        with pytest.raises(ValueError):
            ro.write(bad_pkt)
    assert raw.sent == []


def test_parse_live_replies_and_rejections():
    assert aula.parse_battery_reply(LIVE_82) == ("ok", 82)
    assert aula.parse_battery_reply(LIVE_81) == ("ok", 81)
    assert aula.parse_battery_reply(make_reply(100)) == ("ok", 100)
    assert aula.parse_battery_reply(make_reply(1)) == ("ok", 1)
    assert aula.parse_battery_reply(make_reply(0)) == ("unknown", None)
    assert aula.parse_battery_reply(make_reply(101)) is None
    assert aula.parse_battery_reply(make_reply(0xFF)) is None
    assert aula.parse_battery_reply(None) is None
    assert aula.parse_battery_reply(LIVE_82[:20]) is None

    bad_sum = bytearray(LIVE_82)
    bad_sum[32] ^= 0x01
    assert aula.parse_battery_reply(bytes(bad_sum)) is None

    bad_echo = bytearray(LIVE_82)
    bad_echo[1] = 0x05
    bad_echo[32] = sum(bad_echo[1:32]) & 0xFF
    assert aula.parse_battery_reply(bytes(bad_echo)) is None

    bad_rid = bytearray(LIVE_82)
    bad_rid[0] = 0x01
    assert aula.parse_battery_reply(bytes(bad_rid)) is None


def test_read_awake_and_lightly_sleeping_keyboard():
    # Awake: answers on attempt 1 after skipping an unrelated input report
    noise = bytearray(33)
    noise[1], noise[32] = 0x05, 0x05
    h1 = FakeAulaHandle([[bytes(noise), LIVE_81]])
    out = aula.read([f108_info()], time.monotonic() + 3, None,
                    handle_factory=lambda path, in_len=33: h1)
    assert len(out) == 1
    d = out[0]
    assert (d.id, d.name, d.kind, d.battery, d.charging, d.connected, d.link, d.source) == (
        "aula:024f", "AULA F108 Pro", "keyboard", 81, False, True, "2.4ghz", "hid"
    )
    assert len(h1.sent) == 1

    # Light sleep: attempt 1 times out waking the RF link, attempt 2 returns live %
    h2 = FakeAulaHandle([None, LIVE_82])
    out2 = aula.read([f108_info()], time.monotonic() + 3, None,
                     handle_factory=lambda path, in_len=33: h2)
    assert len(out2) == 1 and out2[0].battery == 82 and len(h2.sent) == 2


def test_sleeping_or_unknown_zero_yields_no_device_so_cache_holds():
    # Receiver answers 0 % ("unknown"): stops without retrying and returns [] (not 0 %)
    h_zero = FakeAulaHandle([make_reply(0), LIVE_81])
    assert aula.read([f108_info()], time.monotonic() + 3, None,
                     handle_factory=lambda path, in_len=33: h_zero) == []
    assert len(h_zero.sent) == 1

    # Complete silence across all attempts also returns []
    h_silent = FakeAulaHandle([None, None, None])
    assert aula.read([f108_info()], time.monotonic() + 3, None,
                     handle_factory=lambda path, in_len=33: h_silent) == []
    assert len(h_silent.sent) == aula.MAX_ATTEMPTS


def test_strict_product_and_collection_filtering_protects_other_05ac_keyboards():
    opened = []

    def factory(path, in_len=33):
        opened.append(path)
        return FakeAulaHandle([LIVE_81])

    unrelated = [
        f108_info(path="apple", product="Aluminum Keyboard"),             # real Apple keyboard
        f108_info(path="qmk", product="QMK VIA Keyboard"),                # QMK Raw HID on 05AC:024F
        f108_info(path="mid1", product="Custom F108Pro Keyboard"),        # contains F108Pro in the middle
        f108_info(path="mid2", product="My F108Pro Dongle"),              # prefix before F108Pro Dongle
        f108_info(path="sfx", product="F108Pro Dongle Clone"),            # suffix after F108Pro Dongle
        f108_info(path="empty", product=""),                              # missing product string
        f108_info(path="ff59", page=0xFF59, in_len=65, out_len=65),       # other vendor page of F108Pro
        f108_info(path="kbd", page=0x0001, usage=0x0006, in_len=9, out_len=2),
        f108_info(path="bt#bth&vid_05ac&pid_024f", product="F108Pro Dongle"),  # Bluetooth path
    ]
    assert aula.read(unrelated, time.monotonic() + 3, None, handle_factory=factory) == []
    assert opened == []

    # Exact "F108Pro Dongle" (case/whitespace-normalized) is accepted
    ok = aula.read([f108_info(path="ok", product="  f108pro   dongle  ")], time.monotonic() + 3,
                   None, handle_factory=factory)
    assert len(ok) == 1 and opened == ["ok"]


def test_enumeration_prefilter_narrows_05ac_to_usb_pid_024f():
    from taskbar_battery_widget import hid_win
    f = hid_win._path_matches_vendor
    assert f(r"\\?\hid#vid_05ac&pid_024f&mi_03#7&1&0&0000#{guid}", (0x05AC,)) is True
    assert f(r"\\?\hid#vid_05ac&pid_0265&col01#7&1&0&0000#{guid}", (0x05AC,)) is False  # Magic Trackpad
    assert f(r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_dev_vid&000205ac_pid&024f", (0x05AC,)) is False


def test_multiple_dongles_get_distinct_ids():
    p1 = r"\\?\hid#vid_05ac&pid_024f&mi_03#7&aaa&0&0000#{guid}"
    p2 = r"\\?\hid#vid_05ac&pid_024f&mi_03#7&bbb&0&0000#{guid}"
    handles = {p1: FakeAulaHandle([LIVE_82]), p2: FakeAulaHandle([LIVE_81])}
    out = aula.read([f108_info(path=p1), f108_info(path=p2)], time.monotonic() + 3, None,
                    handle_factory=lambda path, in_len=33: handles[path])
    assert len(out) == 2
    assert out[0].id == "aula:024f" and out[1].id.startswith("aula:024f:")
    assert wireless.dongle_key(out[0].id) == "05ac:024f"
    assert wireless.dongle_key(out[1].id) == "05ac:024f"


def test_deadline_and_cancel_before_write():
    raw = FakeAulaHandle([LIVE_81])
    ro = aula._ReadOnly(raw)
    assert aula._query_once(ro, time.monotonic() - 1, None) is None
    ev = threading.Event()
    ev.set()
    assert aula._query_once(ro, time.monotonic() + 3, ev) is None
    assert aula.read([f108_info()], time.monotonic() + 3, ev,
                     handle_factory=lambda path, in_len=33: raw) == []
    assert raw.sent == []
