import base64
import json
from pathlib import Path

import pytest

from taskbar_battery_widget import ed25519

# RFC 8032 section 7.1 (TEST 1, 2, 3, 1024, SHA(abc)), extracted from the RFC text.
VECTORS = json.loads((Path(__file__).parent / "rfc8032_vectors.json").read_text())


@pytest.mark.parametrize("name", list(VECTORS))
def test_rfc8032_vectors(name):
    v = VECTORS[name]
    seed, pub, msg, sig = (bytes.fromhex(v[k]) for k in ("SECRET", "PUBLIC", "MESSAGE", "SIGNATURE"))
    assert ed25519.public_from_seed(seed) == pub
    assert ed25519.sign(seed, msg) == sig
    assert ed25519.verify(pub, msg, sig)


def _vec(name="2"):
    v = VECTORS[name]
    return (bytes.fromhex(v["PUBLIC"]), bytes.fromhex(v["MESSAGE"]), bytes.fromhex(v["SIGNATURE"]))


def test_one_bit_flipped_anywhere_is_rejected():
    pub, msg, sig = _vec("3")
    for i in range(len(sig)):
        bad = bytearray(sig)
        bad[i] ^= 0x01
        assert not ed25519.verify(pub, msg, bytes(bad)), i
    bad_msg = bytearray(msg)
    bad_msg[0] ^= 0x80
    assert not ed25519.verify(pub, bytes(bad_msg), sig)
    bad_pub = bytearray(pub)
    bad_pub[5] ^= 0x01
    assert not ed25519.verify(bytes(bad_pub), msg, sig)


def test_truncated_or_extended_signature_and_bad_lengths():
    pub, msg, sig = _vec()
    assert not ed25519.verify(pub, msg, sig[:63])
    assert not ed25519.verify(pub, msg, sig + b"\x00")
    assert not ed25519.verify(pub, msg, b"")
    assert not ed25519.verify(pub[:31], msg, sig)
    assert not ed25519.verify(pub + b"\x00", msg, sig)


def test_invalid_public_keys():
    _, msg, sig = _vec()
    assert not ed25519.verify(b"\x00" * 32, msg, sig)
    # y = p (non-canonical encoding of 0) / y >= p
    assert not ed25519.verify((ed25519.P).to_bytes(32, "little"), msg, sig)
    assert not ed25519.verify(b"\xff" * 32, msg, sig)
    # x = 0 with the sign bit set is non-canonical (y = 1 -> identity)
    assert not ed25519.verify((1 | (1 << 255)).to_bytes(32, "little"), msg, sig)
    # a y that is not on the curve
    assert not ed25519.verify((2).to_bytes(32, "little"), msg, sig)
    assert not ed25519.verify(None, msg, sig)          # never raises


def test_non_canonical_scalar_s_rejected():
    pub, msg, sig = _vec()
    s = int.from_bytes(sig[32:], "little")
    malleable = sig[:32] + (s + ed25519.L).to_bytes(32, "little")     # same point, S >= L
    assert (s + ed25519.L) < 2 ** 256
    assert not ed25519.verify(pub, msg, malleable)
    assert not ed25519.verify(pub, msg, sig[:32] + ed25519.L.to_bytes(32, "little"))


def test_sign_requires_32_byte_seed():
    with pytest.raises(ValueError):
        ed25519.sign(b"short", b"x")


def test_signature_file_format_roundtrip():
    _, _, sig = _vec()
    text = ed25519.encode_signature(sig)
    assert text.endswith("\n") and text.count("\n") == 1
    assert ed25519.decode_signature(text.encode()) == sig
    assert ed25519.decode_signature(text.strip().encode()) == sig
    for bad in (b"", b"!!!!", base64.b64encode(sig[:63]), base64.b64encode(sig + b"x"), sig,
                text.encode() * 2, b"\xff\xfe"):
        assert ed25519.decode_signature(bad) is None


def test_cli_verify(tmp_path, capsys):
    pub, msg, sig = _vec()
    (tmp_path / "m").write_bytes(msg)
    (tmp_path / "s").write_text(ed25519.encode_signature(sig))
    assert ed25519._main(["x", "verify", pub.hex(), str(tmp_path / "m"), str(tmp_path / "s")]) == 0
    assert "OK" in capsys.readouterr().out
    (tmp_path / "m").write_bytes(msg + b"!")
    assert ed25519._main(["x", "verify", pub.hex(), str(tmp_path / "m"), str(tmp_path / "s")]) == 1
    assert ed25519._main(["x", "verify", "zz", str(tmp_path / "m"), str(tmp_path / "s")]) == 2
    assert ed25519._main(["x"]) == 2
