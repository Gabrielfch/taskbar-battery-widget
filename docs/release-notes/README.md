# Release notes

One file per release, named after the git tag: `docs/release-notes/vX.Y.Z.md`.
The release workflow uses it as the GitHub release body; if it is missing, GitHub generates notes automatically.

## Releasing

1. Bump `__version__` in `taskbar_battery_widget/__init__.py` (the only place; the build derives everything else from it).
2. Write `docs/release-notes/vX.Y.Z.md`, then commit.
3. `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
4. The workflow checks that the tag matches `__version__`, runs the tests, builds the installer and portable exe
   with `packaging/build.ps1`, publishes build provenance attestations and creates the GitHub release as a
   **draft** with both exes plus `SHA256SUMS.txt` (no signature yet; the installed apps ignore drafts).
5. On the maintainer's PC (where the private key lives, never in CI):
   ```powershell
   .\.venv\Scripts\python tools\signing\sign_release.py vX.Y.Z --publish
   ```
   It downloads the draft's assets with `gh`, recomputes the SHA-256 of both exes and compares them with
   `SHA256SUMS.txt` (aborts on any difference), signs `SHA256SUMS.txt`, verifies the signature with the
   derived public key, uploads `SHA256SUMS.txt.sig` and, with `--publish`, publishes the release
   (`gh release edit vX.Y.Z --draft=false`). Without `--publish` the release stays a draft.

### One-time key setup
`python tools/signing/gen_keys.py` creates the Ed25519 key outside the repository
(`%USERPROFILE%\.tbw-signing\tbw_release_ed25519.key`), prints only the public key and the line to paste in
`PUBLIC_KEYS` (`taskbar_battery_widget/updater.py`). Ship that change in a release **before** relying on
in-app updates. Back the key file up in a password manager or an offline USB drive. For rotation, add the new
public key next to the old one, release, and drop the old one in a later release.

### Signature format and manual verification
`SHA256SUMS.txt.sig` is a single line: the standard base64 (padded) of the 64 raw bytes of the Ed25519
signature (RFC 8032) over the exact bytes of `SHA256SUMS.txt`. The app refuses to update unless this verifies
with one of the embedded public keys; the hashes in `SHA256SUMS.txt` are only trusted after that.

```powershell
python -m taskbar_battery_widget.ed25519 verify <public-key-hex> SHA256SUMS.txt SHA256SUMS.txt.sig
Get-FileHash .\TaskbarBatteryWidget-Setup-x.y.z.exe -Algorithm SHA256   # compare with SHA256SUMS.txt
```
Also possible: `gh attestation verify <file> --repo <owner>/<repo>` (build provenance).

### CI pinning
Actions are pinned by commit SHA (comment = version): `actions/checkout` v7.0.1, `actions/setup-python` v7.1.0,
`actions/attest-build-provenance` v4.2.2 (all node24). Inno Setup is installed with
`choco install innosetup --version 6.7.1` (the newest 6.x on Chocolatey). Refresh these deliberately.
