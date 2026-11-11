"""Sign the SHA256SUMS.txt of a DRAFT GitHub release and (optionally) publish it. Maintainer-only.

    python tools/signing/sign_release.py vX.Y.Z [--key PATH] [--publish]

Steps (needs the GitHub CLI `gh`, logged in with write access to the repository):
 1. download SHA256SUMS.txt and both .exe files of the draft release into a temp folder;
 2. recompute the SHA-256 of the .exe files and check them against SHA256SUMS.txt (abort on mismatch);
 3. sign SHA256SUMS.txt with the private key and verify the signature with the derived public key;
 4. upload SHA256SUMS.txt.sig (`gh release upload --clobber`);
 5. with --publish: `gh release edit vX.Y.Z --draft=false`.

The private key is read from the key file (default %USERPROFILE%\\.tbw-signing\\tbw_release_ed25519.key)
and is never printed or logged. Signature format: base64 of the 64 raw bytes, one line.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from taskbar_battery_widget import ed25519, updater  # noqa: E402

_TAG_RE = re.compile(r"\Av(\d{1,4})\.(\d{1,4})\.(\d{1,4})\Z")
_HEX_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")


class SignError(Exception):
    pass


def default_key_path() -> Path:
    import os
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".tbw-signing" / "tbw_release_ed25519.key"


def gh(*args: str) -> None:
    """Run the GitHub CLI; on failure raise SignError with its stderr (never anything secret)."""
    try:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignError(f"could not run gh: {exc}") from exc
    if res.returncode != 0:
        raise SignError(f"gh {args[0]} {args[1]} failed (exit {res.returncode}): {res.stderr.strip()}")


def read_seed(path: Path) -> bytes:
    try:
        text = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise SignError(f"cannot read the key file {path}: {type(exc).__name__}") from exc
    if not _HEX_RE.match(text):
        raise SignError("the key file does not contain a 64-hex-character seed")
    return bytes.fromhex(text)


def sign_release(tag: str, key_path: Path, publish: bool = False, repo: str = updater.REPO) -> str:
    """Returns the public key (hex) that verified the signature. Raises SignError on any problem."""
    m = _TAG_RE.match(tag)
    if not m:
        raise SignError("tag must look like vX.Y.Z")
    version = ".".join(m.groups())
    exes = [updater.installer_asset_name(version), f"TaskbarBatteryWidget-portable-{version}.exe"]
    seed = read_seed(key_path)
    pub = ed25519.public_from_seed(seed)

    with tempfile.TemporaryDirectory(prefix="tbw-sign-") as tmp:
        tmpdir = Path(tmp)
        gh("release", "download", tag, "--repo", repo, "--dir", str(tmpdir), "--clobber",
           "--pattern", updater.SUMS_NAME, *[a for n in exes for a in ("--pattern", n)])
        sums_path = tmpdir / updater.SUMS_NAME
        for p in [sums_path, *(tmpdir / n for n in exes)]:
            if not p.is_file():
                raise SignError(f"{p.name} was not downloaded from the release")
        sums_bytes = sums_path.read_bytes()
        sums = sums_bytes.decode("utf-8", errors="replace")
        for n in exes:
            want = updater.expected_hash(sums, n)
            got = hashlib.sha256((tmpdir / n).read_bytes()).hexdigest()
            if want is None:
                raise SignError(f"{n} is not listed (exactly once) in {updater.SUMS_NAME}")
            if want != got:
                raise SignError(f"hash mismatch for {n}: SHA256SUMS.txt says {want}, the file is {got}")

        sig = ed25519.sign(seed, sums_bytes)
        if not ed25519.verify(pub, sums_bytes, sig):
            raise SignError("internal error: the new signature does not verify")
        sig_path = tmpdir / updater.SIG_NAME
        sig_path.write_text(ed25519.encode_signature(sig), encoding="ascii", newline="\n")
        gh("release", "upload", tag, str(sig_path), "--repo", repo, "--clobber")
        if publish:
            gh("release", "edit", tag, "--repo", repo, "--draft=false")
    return pub.hex()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sign and optionally publish a draft release.")
    ap.add_argument("tag", help="release tag, e.g. v1.4.0")
    ap.add_argument("--key", type=Path, default=None, help="private key file (default: ~/.tbw-signing/...)")
    ap.add_argument("--publish", action="store_true", help="publish the draft after uploading the signature")
    args = ap.parse_args(argv)
    try:
        pub = sign_release(args.tag, args.key or default_key_path(), args.publish)
    except SignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Signed {updater.SUMS_NAME} of {args.tag}; verified with public key {pub}")
    if pub not in {k.lower() for k in updater.PUBLIC_KEYS}:
        print("WARNING: this public key is NOT in PUBLIC_KEYS (updater.py): installed apps will reject it.",
              file=sys.stderr)
    print("Published." if args.publish else "Uploaded as DRAFT. Publish with --publish when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
