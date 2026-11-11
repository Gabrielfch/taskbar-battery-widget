"""Generate the Ed25519 release-signing key pair (run ONCE, on the maintainer's PC).

    python tools/signing/gen_keys.py [KEY_PATH] [--force]

* The private seed (32 random bytes, hex) is written to KEY_PATH, which must be OUTSIDE the repository
  (default: %USERPROFILE%\\.tbw-signing\\tbw_release_ed25519.key). It is never printed.
* An existing file is never overwritten unless --force is given.
* On Windows the file and its folder are restricted to the current user with icacls.
* Only the PUBLIC key is printed, together with the exact line to paste into
  taskbar_battery_widget/updater.py (PUBLIC_KEYS).

Keep a backup of the key file in a password manager or on an offline USB drive: if it is lost you can no
longer sign releases for the apps that embed this public key.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from taskbar_battery_widget import ed25519  # noqa: E402


def default_key_path() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".tbw-signing" / "tbw_release_ed25519.key"


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def restrict_permissions(path: Path) -> str:
    """Limit ``path`` to the current user (icacls). Returns a human-readable outcome."""
    user = os.environ.get("USERNAME")
    if sys.platform != "win32" or not user:
        try:
            os.chmod(path, 0o600)
            return "permissions set to 0600"
        except OSError as exc:
            return f"could not restrict permissions ({exc})"
    try:
        res = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run icacls ({exc})"
    if res.returncode != 0:
        return f"icacls failed (exit {res.returncode}); restrict the file permissions manually"
    return f"access restricted to {user} with icacls"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the release-signing Ed25519 key pair.")
    ap.add_argument("key_path", nargs="?", type=Path, default=None,
                    help="where to store the private key (default: %%USERPROFILE%%\\.tbw-signing\\...)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing key file")
    args = ap.parse_args(argv)

    path = (args.key_path or default_key_path()).expanduser()
    if _inside_repo(path):
        print(f"error: {path} is inside the repository; choose a path outside it.", file=sys.stderr)
        return 2
    if path.exists() and not args.force:
        print(f"error: {path} already exists; refusing to overwrite (use --force to replace it).",
              file=sys.stderr)
        return 1

    seed = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if args.force else os.O_EXCL)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        print(f"error: {path} already exists; refusing to overwrite.", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
        f.write(seed.hex() + "\n")
    outcomes = [restrict_permissions(path.parent), restrict_permissions(path)]

    pub = ed25519.public_from_seed(seed).hex()
    del seed
    print(f"Private key saved to: {path}")
    for o in outcomes:
        print(f"  - {o}")
    print()
    print("Public key (hex):")
    print(f"  {pub}")
    print()
    print("Paste this line inside PUBLIC_KEYS in taskbar_battery_widget/updater.py:")
    print(f'    "{pub}",')
    print()
    print("IMPORTANT: keep a backup copy of the key file in a password manager or on an offline USB drive.")
    print("Never commit it, never share it. If it leaks or is lost, rotate the key (see SECURITY.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
