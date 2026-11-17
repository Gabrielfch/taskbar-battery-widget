"""Byte builders for animated test images (Qt cannot write animated GIF / WebP itself)."""
import struct

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage


def _lzw_pixels(indices):
    """GIF LZW with a 2-bit alphabet: a CLEAR code every two literals keeps the code width at 3 bits."""
    clear, eoi = 4, 5
    bits = []

    def put(code):
        bits.extend((code >> i) & 1 for i in range(3))

    put(clear)
    for n in range(0, len(indices), 2):
        if n:
            put(clear)
        for v in indices[n:n + 2]:
            put(v)
    put(eoi)
    out = bytearray()
    for i in range(0, len(bits), 8):
        out.append(sum(b << j for j, b in enumerate(bits[i:i + 8])))
    return bytes(out)


def make_gif(w, h, delays_ms):
    """GIF89a, one frame per delay; frame k is one flat colour (palette index 1 + k % 3: red, green, blue)."""
    out = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF1, 0, 0))
    out += bytes([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255])
    out += bytes([0x21, 0xFF, 0x0B]) + b"NETSCAPE2.0" + bytes([3, 1, 0, 0, 0])
    for k, d in enumerate(delays_ms):
        out += bytes([0x21, 0xF9, 4, 0x04]) + struct.pack("<H", d // 10) + bytes([0, 0])
        out += bytes([0x2C]) + struct.pack("<HHHHB", 0, 0, w, h, 0)
        data = _lzw_pixels([1 + k % 3] * (w * h))
        out.append(2)
        for i in range(0, len(data), 255):
            c = data[i:i + 255]
            out.append(len(c))
            out += c
        out.append(0)
    out.append(0x3B)
    return bytes(out)


def _chunks(d):
    i, out = 12, []
    while i < len(d):
        tag, n = d[i:i + 4], struct.unpack("<I", d[i + 4:i + 8])[0]
        out.append((tag, d[i + 8:i + 8 + n]))
        i += 8 + n + (n & 1)
    return out


def _chunk(tag, payload):
    return tag + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def _u24(v):
    return struct.pack("<I", v)[:3]


def webp_still(color, size=16):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(color)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert img.save(buf, "WEBP", 100)
    return bytes(buf.data())


def make_webp_anim(colors, delays_ms, size=16):
    """Animated WebP: the lossless bitstreams Qt writes for flat-colour stills, wrapped in ANMF chunks."""
    body = b"WEBP" + _chunk(b"VP8X", bytes([0x12, 0, 0, 0]) + _u24(size - 1) + _u24(size - 1))
    body += _chunk(b"ANIM", bytes(6))
    for c, d in zip(colors, delays_ms):
        inner = b"".join(_chunk(t, p) for t, p in _chunks(webp_still(c, size)) if t in (b"VP8L", b"VP8 ", b"ALPH"))
        body += _chunk(b"ANMF", _u24(0) + _u24(0) + _u24(size - 1) + _u24(size - 1) + _u24(d) + bytes([0]) + inner)
    return b"RIFF" + struct.pack("<I", len(body)) + body


RED, GREEN, BLUE = QColor(255, 0, 0), QColor(0, 255, 0), QColor(0, 0, 255)
