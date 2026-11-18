"""Shared fake HID++ receiver used by test_hidpp and test_hardening."""
from types import SimpleNamespace


def info(usage, path, pid=0xC539, page=0xFF00, out=None, product="USB Receiver"):
    out = out if out is not None else (7 if usage == 1 else 20)
    return SimpleNamespace(path=path, vid=0x046D, pid=pid, usage_page=page, usage=usage,
                           in_len=out, out_len=out, feat_len=0, product=product)


# ---- fake device driving Channel / scan_logitech ----
class FakeDongle:
    """Receiver with one device at index 1: HID++ 2.0 root + name + 0x1001 battery feature."""

    def __init__(self, battery_mv=3905, asleep=False):
        self.long_q, self.short_q = [], []
        self.mv = battery_mv
        self.asleep = asleep
        self.writes = []

    def handle(self, path, in_len=64):
        return FakeHandle(self, path.endswith("long"))

    def on_write(self, pkt):
        self.writes.append(bytes(pkt))
        dev, feat, fs = pkt[1], pkt[2], pkt[3]
        fn = fs >> 4
        if dev != 1:
            self.short_q.append(bytes([0x10, dev, 0x8F, feat, fs, 0x09, 0]))
            return
        if self.asleep:
            return
        hdr = bytes([0x11, dev])
        if feat == 0:
            if fn == 1:
                body = bytes([4, 2, pkt[6]])
            else:
                fid = (pkt[4] << 8) | pkt[5]
                body = bytes([{0x0005: 1, 0x1001: 2}.get(fid, 0), 0, 0])
        elif feat == 1:     # name feature
            name = b"G915 TEST KEYBOARD"
            if fn == 0:
                body = bytes([len(name)])
            elif fn == 1:
                body = name[pkt[4]:pkt[4] + 16]
            else:
                body = bytes([0])
        elif feat == 2:
            body = bytes([self.mv >> 8, self.mv & 0xFF, 0])
        else:
            return
        self.long_q.append((hdr + bytes([feat, fs]) + body).ljust(20, b"\x00"))


class FakeHandle:
    def __init__(self, dongle, is_long):
        self.d, self.is_long = dongle, is_long

    def write(self, pkt, timeout_ms=1000):
        self.d.on_write(pkt)
        return True

    def read(self, timeout_ms=0):
        q = self.d.long_q if self.is_long else self.d.short_q
        return q.pop(0) if q else None

    def drain(self):
        pass

    def close(self):
        pass


def receiver_infos():
    return [info(1, r"\\?\HID#VID_046D&PID_C539&MI_02&Col01#9&aa&0&0000#short"),
            info(2, r"\\?\HID#VID_046D&PID_C539&MI_02&Col02#9&aa&0&0001#long")]
