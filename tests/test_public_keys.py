"""Guard the embedded release-signing public keys against typos: a bad key silently disables updates."""
from taskbar_battery_widget import ed25519, updater


def test_embedded_public_keys_are_valid_and_distinct():
    keys = updater.usable_public_keys()
    assert len(keys) == len(updater.PUBLIC_KEYS) >= 1, "every PUBLIC_KEYS entry must be 32 bytes of hex"
    assert len(set(keys)) == len(keys), "duplicate public key"
    for k in keys:
        assert ed25519._decompress(k) is not None, f"{k.hex()} is not a valid Ed25519 point"
