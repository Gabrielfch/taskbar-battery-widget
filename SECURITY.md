# Security policy

## Supported versions

Only the latest release receives fixes. Please update before reporting.

## Reporting a vulnerability

Use GitHub private vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue for security problems.

Include the app version, Windows version, the device involved (if any) and steps to reproduce. Do not
attach files containing personal data.

## What is relevant

- Any command sent to a device that is not a read/query command from the reader whitelist.
- Personal data in logs or stored files (MAC addresses, serial numbers, user names).
- Unexpected network access (the app is designed to use no network at all).
- Problems in how the installer or the portable executable is built or distributed.

## Update authenticity

In-app updates are accepted only if `SHA256SUMS.txt` carries a valid Ed25519 signature
(`SHA256SUMS.txt.sig`, base64 of the 64 raw bytes, one line) from one of the public keys embedded in
`taskbar_battery_widget/updater.py` (`PUBLIC_KEYS`), then the installer's SHA-256 must match and the file is
re-hashed right before it is launched. With no embedded key, no signature or a bad one, the app only offers
the release page. The private key lives only on the maintainer's PC (see `docs/release-notes/README.md`);
CI creates unsigned drafts. If the key is lost or leaked, report it/rotate: publish a release that embeds a new
public key (signed with the old key if still trusted) and announce it in the release notes. Check a release
by hand with `python -m taskbar_battery_widget.ed25519 verify <public-key-hex> SHA256SUMS.txt SHA256SUMS.txt.sig`.

Release-signing public keys (Ed25519, hex):

| Key | Public key |
| --- | --- |
| Primary (signs releases) | `3a19dbb213885211c87bada3f95d562199a5087d2a42b46891f37fa7dd3f50dd` |
| Backup (kept offline, used only to rotate) | `8706ea9a037f5002b639d38dbce83d4bb1ffce1ad145d56dddb0fe446d909f81` |

Releases built by CI also carry build provenance attestations:
`gh attestation verify <file>.exe --repo Gabrielfch/taskbar-battery-widget`.
