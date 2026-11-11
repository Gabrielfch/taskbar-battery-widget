"""Regression tests for the HID/scan review round (ids, cache, budgets, validation)."""
import threading
import time
from types import SimpleNamespace

from taskbar_battery_widget import dualsense, hidpp, razer, sony, wireless
from taskbar_battery_widget.models import Device


def dev(id_, name="Kbd", kind="keyboard", battery=50, connected=True, charging=False, link="2.4ghz"):
    return Device(id=id_, name=name, kind=kind, battery=battery, connected=connected,
                  charging=charging, link=link)


# ---- 3: two controllers with the same PID ----------------------------------------------------
PATH_A = r"\\?\hid#vid_054c&pid_0ce6#7&aaaa&0&0000#{x}"
PATH_B = r"\\?\hid#vid_054c&pid_0ce6#7&bbbb&0&0000#{x}"


class Pad:
    def __init__(self, reports):
        self.q = list(reports)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def drain(self):
        pass

    def read(self, timeout_ms=0):
        if self.q:
            return self.q.pop(0)
        time.sleep(0.002)
        return None


def _pad_info(path, pid=0x0CE6):
    return SimpleNamespace(vid=0x054C, pid=pid, path=path, usage_page=1, usage=5, in_len=64,
                           out_len=63, feat_len=64, product="Wireless Controller")


def _usb_report(status):
    r = bytearray(64)
    r[0], r[53] = 1, status
    return bytes(r)


def test_two_dualsense_with_same_pid_are_two_devices():
    pads = {PATH_A: Pad([_usb_report(0x11)]), PATH_B: Pad([_usb_report(0x05)])}
    out = dualsense.read_dualsense([_pad_info(PATH_B), _pad_info(PATH_A)], time.monotonic() + 2.0,
                                   handle_factory=lambda p, in_len=64: pads[p])
    assert len(out) == 2
    ids = sorted(d.id for d in out)
    assert len(set(ids)) == 2 and ids[0] == "sony:0ce6"
    assert len(ids[1].split(":")) == 3 and len(ids[1].split(":")[2]) == 6
    first = next(d for d in out if d.id == "sony:0ce6")
    assert first.battery == 15                     # the first path (sorted) keeps the plain id


def _ds4_report(status):
    r = bytearray(64)
    r[0], r[30] = 1, status
    return bytes(r)


def test_two_ds4_with_same_pid_are_two_devices():
    infos = [_pad_info(PATH_A, 0x05C4), _pad_info(PATH_B, 0x05C4)]
    pads = {PATH_A: Pad([_ds4_report(0x14)]), PATH_B: Pad([_ds4_report(0x04)])}
    out = sony.read(infos, time.monotonic() + 2.0, handle_factory=lambda p, in_len=64: pads[p])
    assert len(out) == 2 and len({d.id for d in out}) == 2
    assert out[0].id == "sony:05c4" and out[1].id.startswith("sony:05c4:")


def test_three_part_sony_ids_in_wireless_helpers():
    assert wireless.dongle_key("sony:0ce6:a1b2c3") == "054c:0ce6"
    assert wireless.dongle_key("sony:0ce6") == "054c:0ce6"
    assert wireless.is_experimental_id("sony:05c4:a1b2c3") is True       # DS4: experimental reader
    assert wireless.is_experimental_id("sony:0ce6:a1b2c3") is False      # DualSense: verified
    assert wireless.is_experimental_id("sony:05c4") is True


# ---- 4: carried battery keeps the fresh charging flag ------------------------------------------
def test_cached_battery_does_not_overwrite_fresh_charging():
    cache = {}
    wireless.apply_cache(cache, [dev("logi:c539:01", battery=60)], {"046d:c539"}, 0.0)
    out = wireless.apply_cache(cache, [dev("logi:c539:01", battery=None, charging=True)],
                               {"046d:c539"}, 10.0)
    assert (out[0].battery, out[0].charging) == (60, True)


# ---- 5: containment only merges when a reading is missing ---------------------------------------
def test_merge_two_readings_with_contained_names_stay_separate():
    bt = [dev("bt1", "MX Master 3", "mouse", 80, link="bluetooth")]
    wl = [dev("logi:c52b:01", "MX Master 3S", "mouse", 40)]
    assert [d.id for d in wireless.merge_devices(bt, wl)] == ["bt1", "logi:c52b:01"]
    bt_none = [dev("bt1", "MX Master 3", "mouse", None, link="bluetooth")]
    assert [d.id for d in wireless.merge_devices(bt_none, wl)] == ["logi:c52b:01"]
    same = [dev("logi:c52b:01", "mx  master 3", "mouse", 40)]
    assert [d.id for d in wireless.merge_devices(bt, same)] == ["bt1"]   # equal names: same device


# ---- 6: total scan budget ------------------------------------------------------------------------
def test_total_budget_is_below_the_monitor_limit():
    assert wireless.TOTAL_BUDGET_S < 3.6
    assert wireless.ENUM_RETRY_BUDGET_S < wireless.ENUM_BUDGET_S


def test_scan_with_hung_enumeration_and_retry_stays_within_total_budget(monkeypatch):
    from taskbar_battery_widget import hid_win
    release = threading.Event()
    real = wireless.__dict__["scan_wireless"]
    wireless.reset_cache()
    monkeypatch.setattr(wireless, "_hid_enabled", True)
    monkeypatch.setattr(hid_win, "enumerate_hid", lambda v=None: release.wait(10) or [])
    # a core dongle was seen a moment ago, so the empty/hung result triggers the retry path
    wireless._last_present = {"046d:c539"}
    wireless._last_present_ts = time.monotonic()
    try:
        t = time.monotonic()
        real()
        assert time.monotonic() - t < wireless.TOTAL_BUDGET_S + 0.3
    finally:
        release.set()
        wireless._inflight.clear()
        wireless.reset_cache()


# ---- 7: cached feature index is revalidated ------------------------------------------------------
class Scripted:
    def __init__(self, responder):
        self.responder, self.q, self.writes = responder, [], []

    def write(self, pkt, timeout_ms=1000):
        self.writes.append(bytes(pkt))
        self.q.extend(self.responder(bytes(pkt)))
        return True

    def read(self, timeout_ms=0):
        return self.q.pop(0) if self.q else None

    def drain(self):
        pass

    def close(self):
        pass


def _reply(pkt, params):
    return (bytes([0x11, pkt[1], pkt[2], pkt[3]]) + params).ljust(20, b"\x00")


_GROUP = {"short": None, "long": SimpleNamespace(path="long")}


def test_cached_battery_index_is_revalidated_and_replaced():
    fid = hidpp.F_UNIFIED_BATTERY

    def responder(pkt):
        if pkt[2] == 0:                                   # Root.getFeature(fid) -> now index 7
            return [_reply(pkt, bytes([7, 0, 0]))]
        return [_reply(pkt, bytes([72, 4, 0, 0]))]

    h = Scripted(responder)
    known = {"battery": (fid, 5)}
    with hidpp.Channel(_GROUP, lambda path, n=64: h) as ch:
        res = ch.read_battery(1, known)
    assert res == (72, False)
    assert h.writes[0][2] == 0                            # revalidation came first
    assert [w[2] for w in h.writes[1:]] == [7]            # never talked to the stale index 5
    assert known["battery"] == (fid, 7)


def test_cached_battery_index_dropped_when_feature_vanished():
    h = Scripted(lambda pkt: [_reply(pkt, bytes([0, 0, 0]))])     # getFeature -> 0: unsupported
    known = {"battery": (hidpp.F_UNIFIED_BATTERY, 5)}
    with hidpp.Channel(_GROUP, lambda path, n=64: h) as ch:
        assert ch.read_battery(1, known) is None
    assert "battery" not in known
    assert all(w[2] == 0 for w in h.writes)


# ---- 8: a hung group thread is not duplicated ---------------------------------------------------
def test_hung_hidpp_group_is_not_relaunched(monkeypatch):
    hidpp._group_inflight.clear()
    release = threading.Event()
    starts = []

    def scan_group(g, hf, cancel, suffix):
        starts.append(1)
        release.wait(10)
        return []

    monkeypatch.setattr(hidpp, "GROUP_BUDGET_S", 0.1)
    monkeypatch.setattr(hidpp, "_scan_group", scan_group)
    monkeypatch.setattr(hidpp, "find_hidpp_groups",
                        lambda infos: [{"key": "k", "vid": 0x046D, "pid": 0xC539}])
    try:
        assert hidpp.scan_logitech([object()]) == []
        assert hidpp.scan_logitech([object()]) == []      # the previous cycle's thread is still hung
        assert len(starts) == 1
        release.set()
        time.sleep(0.3)
        hidpp.scan_logitech([object()])
        assert len(starts) == 2                           # free again: a new thread starts
    finally:
        release.set()
        hidpp._group_inflight.clear()


# ---- 9: transient 0 % ----------------------------------------------------------------------------
class OneLevel:
    """Razer feature-report fake answering a fixed battery level byte (0..255)."""

    def __init__(self, level):
        self.level, self.last = level, None

    def set_feature(self, data):
        self.last = bytes(data)
        return True

    def get_feature(self, rid, n):
        tid, cmd = self.last[2], self.last[8]
        pkt = bytearray(90)
        pkt[0], pkt[1], pkt[5], pkt[6], pkt[7] = 2, tid, 2, 7, cmd
        pkt[9] = self.level if cmd == 0x80 else 0
        pkt[88] = razer.crc(bytes(pkt))
        return b"\x00" + bytes(pkt)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _rinfo(path="p"):
    return SimpleNamespace(path=path, vid=0x1532, pid=0x00A7, usage_page=0xFF00, usage=2,
                           in_len=0, out_len=0, feat_len=91, product="Razer Viper Ultimate Dongle")


def test_razer_zero_after_nonzero_is_no_reading(monkeypatch):
    monkeypatch.setattr(razer, "SET_GET_DELAY_S", 0)
    razer._last.clear()
    [d] = razer.scan_razer([_rinfo()], lambda p: OneLevel(128))
    assert d.battery == 50
    [d] = razer.scan_razer([_rinfo()], lambda p: OneLevel(0))
    assert d.battery is None                              # transient 0 % ignored
    [d] = razer.scan_razer([_rinfo()], lambda p: OneLevel(128))
    assert d.battery == 50
    razer._last.clear()
    [d] = razer.scan_razer([_rinfo("other")], lambda p: OneLevel(0))
    assert d.battery == 0                                 # nothing earlier: a real 0 %
