"""Ed25519 (RFC 8032) in pure Python: verification for the updater, signing for the release tool.

This follows the RFC 8032 section 6 reference implementation (extended coordinates). It is NOT
constant-time and must not be used to sign on shared/hostile machines at scale; it exists so the app
can verify release signatures with no third-party dependency, and so the maintainer's offline
``tools/signing`` scripts and the tests can sign. ``verify`` rejects non-canonical encodings
(y >= p, x = 0 with the sign bit set, S >= L) and points that are not on the curve.

Command line (manual verification of a release)::

    python -m taskbar_battery_widget.ed25519 verify <public-key-hex> SHA256SUMS.txt SHA256SUMS.txt.sig
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import sys

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)

_GY = 4 * pow(5, P - 2, P) % P


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % P)
_IDENT = (0, 1, 1, 0)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * _D % P
    D = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _mul(s: int, pt):
    q = _IDENT
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _compress(pt) -> bytes:
    zinv = pow(pt[2], P - 2, P)
    x, y = pt[0] * zinv % P, pt[1] * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _expand(seed: bytes):
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(bytes(seed)).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_from_seed(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = _sha512_int(prefix + message) % L
    R = _compress(_mul(r, _G))
    k = _sha512_int(R + pub + message) % L
    s = (r + k * a) % L
    return R + int.to_bytes(s, 32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid, canonical signature. Never raises on malformed input."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        A = _decompress(bytes(public_key))
        R = _decompress(bytes(signature[:32]))
        if A is None or R is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        k = _sha512_int(bytes(signature[:32]) + bytes(public_key) + bytes(message)) % L
        return _equal(_mul(s, _G), _add(R, _mul(k, A)))
    except Exception:
        return False


# ------------------------------------------------------------------ signature file format
def encode_signature(sig: bytes) -> str:
    """Release format: base64 (standard alphabet, padded) of the 64 raw signature bytes, one line + LF."""
    return base64.b64encode(sig).decode("ascii") + "\n"


def decode_signature(data: bytes) -> bytes | None:
    """Parse the ``.sig`` asset: a single base64 line of exactly 64 bytes. None when malformed."""
    try:
        text = bytes(data).decode("ascii").strip()
        if not text or "\n" in text or "\r" in text:
            return None
        raw = base64.b64decode(text, validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return None
    return raw if len(raw) == 64 else None


def _main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] != "verify":
        print("usage: python -m taskbar_battery_widget.ed25519 verify <public-key-hex> <message-file> "
              "<signature-file>", file=sys.stderr)
        return 2
    try:
        pub = bytes.fromhex(argv[2])
        with open(argv[3], "rb") as f:
            msg = f.read()
        with open(argv[4], "rb") as f:
            sig = decode_signature(f.read())
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if sig is not None and verify(pub, msg, sig):
        print("OK: valid signature")
        return 0
    print("FAIL: invalid signature", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
