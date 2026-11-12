# Release notes

One file per release, named after the git tag: `docs/release-notes/vX.Y.Z.md`.
The release workflow uses it as the GitHub release body; if it is missing, GitHub generates notes automatically.

## Releasing

1. Bump `__version__` in `taskbar_battery_widget/__init__.py` (the only place; the build derives everything else from it).
2. Write `docs/release-notes/vX.Y.Z.md`, then commit.
3. `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
4. The workflow has two jobs. `build` (read-only permissions) checks that the tag matches `__version__`, runs the
   tests, builds the installer and portable exe with `packaging/build.ps1` and hands them over as a workflow
   artifact. `publish` (the only job with `contents: write`, `id-token: write`, `attestations: write`)
   re-checks the hashes, publishes build provenance attestations and creates the GitHub release as a
   **draft** with both exes plus `SHA256SUMS.txt` (no signature yet; the installed apps ignore drafts).
   `GH_TOKEN` is only exposed to the `gh release create` step.
5. On the maintainer's PC (where the private key lives, never in CI):
   ```powershell
   .\.venv\Scripts\python tools\signing\sign_release.py vX.Y.Z --publish
   ```
   Requires **GitHub CLI >= 2.49** (checked at start; `gh attestation verify` and `gh api --slurp`), logged in
   with write access. The script:
   - aborts before touching anything if the public key derived from your private key is not in
     `PUBLIC_KEYS` (`taskbar_battery_widget/updater.py`);
   - downloads the draft's assets with `gh release download <tag>`; if gh cannot find the draft by tag it
     falls back to `gh api repos/<owner>/<repo>/releases` (which lists drafts), locates it by `tag_name`
     and downloads the assets by id;
   - recomputes the SHA-256 of both exes and compares them with `SHA256SUMS.txt` (aborts on any difference);
   - runs `gh attestation verify <exe> --repo <owner>/<repo>` for both exes and aborts if it fails
     (`--skip-attestation` bypasses this: last resort only, you would be trusting the draft blindly);
   - signs `SHA256SUMS.txt`, verifies the signature with the derived public key and uploads
     `SHA256SUMS.txt.sig`;
   - downloads everything from the draft again and verifies signature (against the embedded keys, like the
     app) and hashes; only then, with `--publish`, publishes (`gh release edit vX.Y.Z --draft=false`).
     Without `--publish` the release stays a draft.

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
`actions/attest-build-provenance` v4.2.2, `actions/upload-artifact` v7.0.2, `actions/download-artifact`
v8.0.2 (all node24). Inno Setup is installed with
`choco install innosetup --version 6.7.1` (the newest 6.x on Chocolatey). Refresh these deliberately.

### Dependency hashes (deferred)
`requirements*.txt` pin top-level versions but are not installed with `--require-hashes`: that mode needs every
transitive dependency pinned with hashes for each platform/Python (win_amd64 cp314 wheels plus sdists), which
needs a lock tool (`pip-compile --generate-hashes`) that the project does not use yet. Left for later so that
`pip install -r requirements-dev.txt` keeps working unchanged.
