# Release notes

One file per release, named after the git tag: `docs/release-notes/vX.Y.Z.md`.
The release workflow uses it as the GitHub release body; if it is missing, GitHub generates notes automatically.

## Releasing

1. Bump `__version__` in `taskbar_battery_widget/__init__.py` (the only place; the build derives everything else from it).
2. Write `docs/release-notes/vX.Y.Z.md`, then commit.
3. `git tag vX.Y.Z`
4. `git push origin vX.Y.Z`

The workflow checks that the tag matches `__version__`, runs the tests, builds the installer and portable exe with
`packaging/build.ps1`, attaches both plus `SHA256SUMS.txt`, and publishes build provenance attestations.
Verify a download with `gh attestation verify <file> --repo <owner>/<repo>` or `sha256sum -c SHA256SUMS.txt`.
