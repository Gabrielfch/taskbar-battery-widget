"""Update check and verified download (stdlib only; no third-party network code).

The app only talks to the network when the user asks (or opts in to the daily automatic check):
one HTTPS GET to the GitHub releases API with a generic User-Agent carrying the app version.
Installers are only fetched from this repository's release assets, over HTTPS, size-limited and
verified against the release's SHA256SUMS.txt before they are ever executed.

Authenticity: SHA256SUMS.txt must carry a valid Ed25519 signature (asset ``SHA256SUMS.txt.sig``) made
with one of the embedded PUBLIC_KEYS, checked BEFORE any hash in it is trusted. Without embedded keys,
without a signature or with a bad one the update is refused (the user is sent to the release page).

Signature file format: one line holding the standard base64 (padded) of the 64 raw bytes of the
Ed25519 signature (RFC 8032) over the exact bytes of SHA256SUMS.txt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__, ed25519

log = logging.getLogger(__name__)

REPO = "Gabrielfch/taskbar-battery-widget"
REPO_URL = f"https://github.com/{REPO}"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"
APP_DIR_NAME = "Taskbar Battery Widget"
SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"

# Ed25519 public keys (64 hex chars each) accepted for release signatures. Several keys allow rotation:
# ship the new key in a release signed with the old one, later drop the old one.
# Left empty until the maintainer generates the keys: updates are then refused (manual download only).
PUBLIC_KEYS: tuple[str, ...] = (
    # filled by tools/signing/gen_keys.py output
    "3a19dbb213885211c87bada3f95d562199a5087d2a42b46891f37fa7dd3f50dd",  # primary release key
    "8706ea9a037f5002b639d38dbce83d4bb1ffce1ad145d56dddb0fe446d909f81",  # backup key (kept offline)
)

TIMEOUT_S = 10
DOWNLOAD_TIMEOUT_S = 30
MAX_INSTALLER_BYTES = 60 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024
MAX_SIG_BYTES = 1024
TOTAL_DEADLINE_S = 10 * 60          # whole prepare_update, on top of the per-operation timeout
STALE_UPDATE_AGE_S = 24 * 3600
UPDATE_DIR_PREFIX = "TBW-update-"
MAX_API_BYTES = 2 * 1024 * 1024
CHECK_INTERVAL_S = 24 * 3600

ASSET_HOST = "github.com"
REDIRECT_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com"})

_VERSION_RE = re.compile(r"\Av?(\d{1,4})\.(\d{1,4})\.(\d{1,4})\Z")
_SUMS_LINE_RE = re.compile(r"\A([0-9a-fA-F]{64}) [ *](.+)\Z")


class UpdateError(Exception):
    """``key`` is an i18n key (``upd_err_*``) describing the failure to the user."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class Cancelled(UpdateError):
    def __init__(self):
        super().__init__("upd_err_cancelled")


# ------------------------------------------------------------------ versions
def parse_version(text) -> tuple[int, int, int] | None:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3); anything else (pre-release suffixes included) -> None."""
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.match(text)
    return tuple(int(g) for g in m.groups()) if m else None      # type: ignore[return-value]


def is_newer(candidate, current=__version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# ------------------------------------------------------------------ release model
@dataclass(frozen=True)
class Release:
    version: str                     # "1.3.1"
    tag: str                         # "v1.3.1"
    page_url: str                    # release page on GitHub
    assets: dict = field(default_factory=dict)       # name -> validated download URL

    @property
    def installer_name(self) -> str:
        return installer_asset_name(self.version)

    @property
    def can_install(self) -> bool:
        """Everything needed for a verified in-app update is present (including a way to verify it)."""
        return (self.installer_name in self.assets and SUMS_NAME in self.assets
                and SIG_NAME in self.assets and bool(usable_public_keys()))


def installer_asset_name(version: str) -> str:
    return f"TaskbarBatteryWidget-Setup-{version}.exe"


def release_page_url(tag: str) -> str:
    return f"{REPO_URL}/releases/tag/{tag}"


def is_valid_asset_url(url, tag: str, name: str) -> bool:
    """Exact match of https://github.com/<repo>/releases/download/<tag>/<name> (no query/fragment)."""
    if not isinstance(url, str) or not url.isascii() or any(c in url for c in " \t\r\n\\"):
        return False
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    if u.scheme != "https" or u.hostname != ASSET_HOST or port not in (None, 443):
        return False
    if u.username or u.password or u.query or u.fragment:
        return False
    return u.path == f"/{REPO}/releases/download/{tag}/{name}"


def parse_release(data) -> Release | None:
    """Turn the API JSON into a Release; None for drafts, prereleases, bad tags or bad shapes."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    ver = parse_version(tag)
    if ver is None or not tag.startswith("v"):
        return None
    version = ".".join(map(str, ver))
    if tag != f"v{version}":
        return None                  # leading zeros etc.
    assets = {}
    raw = data.get("assets")
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        name, url = a.get("name"), a.get("browser_download_url")
        if name in (SUMS_NAME, SIG_NAME, installer_asset_name(version)) and is_valid_asset_url(url, tag, name):
            assets[name] = url
    return Release(version=version, tag=tag, page_url=release_page_url(tag), assets=assets)


# ------------------------------------------------------------------ network plumbing
def user_agent() -> str:
    return f"TaskbarBatteryWidget/{__version__}"


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to HTTPS GitHub hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or (u.hostname or "").lower() not in REDIRECT_HOSTS:
            raise urllib.error.URLError("redirect to a disallowed location")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener():
    """HTTPS-only opener with certificate verification, safe redirects and no cookies."""
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _SafeRedirect())


def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": user_agent(), "Accept": accept}, method="GET")


def _final_url_ok(resp, hosts=REDIRECT_HOSTS) -> bool:
    try:
        final = resp.geturl()
    except Exception:
        return False                 # cannot tell where the data came from: do not trust it
    if not isinstance(final, str):
        return True
    u = urllib.parse.urlsplit(final)
    return u.scheme == "https" and (u.hostname or "").lower() in hosts


def fetch_latest(opener=None) -> Release | None:
    """Latest stable release, or None when there is none usable. Raises UpdateError on network errors."""
    opener = opener or _build_opener()
    try:
        with opener.open(_request(API_URL, "application/vnd.github+json"), timeout=TIMEOUT_S) as resp:
            if not _final_url_ok(resp, REDIRECT_HOSTS | {"api.github.com"}):
                raise UpdateError("upd_err_network")
            body = resp.read(MAX_API_BYTES + 1)
    except UpdateError:
        raise
    except Exception as exc:                       # URLError, HTTPError, ssl, timeout, ...
        raise UpdateError("upd_err_network") from exc
    if len(body) > MAX_API_BYTES:
        raise UpdateError("upd_err_network")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("upd_err_network") from exc
    return parse_release(data)


def check(opener=None, current: str = __version__) -> Release | None:
    """Release newer than ``current`` (None when up to date). Raises UpdateError on failures."""
    rel = fetch_latest(opener)
    newer = rel is not None and is_newer(rel.version, current)
    log.info("update check done (newer=%s)", newer)
    if newer:
        log.info("update found: %s", rel.version)
        return rel
    return None


# ------------------------------------------------------------------ install mode
def install_mode(frozen: bool | None = None, exe: str | None = None) -> str:
    """'installed' (frozen under %LOCALAPPDATA%\\Programs\\<app>), 'portable' (other frozen) or 'source'."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return "portable"
    try:
        target = Path(os.path.realpath(Path(base) / "Programs" / APP_DIR_NAME))
        Path(os.path.realpath(exe or sys.executable)).relative_to(target)
    except (ValueError, OSError):
        return "portable"
    return "installed"


def install_mode_key() -> str:
    return {"installed": "mode_installed", "portable": "mode_portable"}.get(install_mode(), "mode_source")


# ------------------------------------------------------------------ automatic check policy
def should_auto_check(cfg, now: float | None = None) -> bool:
    if not cfg.get("update_check_auto"):
        return False
    now = time.time() if now is None else now
    last = cfg.get("update_last_check")
    return not isinstance(last, int) or last > now or now - last >= CHECK_INTERVAL_S


def record_check(cfg, now: float | None = None) -> None:
    try:
        cfg.set("update_last_check", int(time.time() if now is None else now))
    except ValueError:
        pass


# ------------------------------------------------------------------ download + verify
def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise UpdateError("upd_err_timeout")


def _download(url: str, dest: Path, limit: int, opener, progress: Callable[[int, int], None] | None,
              cancel: threading.Event | None, deadline: float | None = None) -> None:
    try:
        _check_deadline(deadline)
        with opener.open(_request(url, "application/octet-stream"), timeout=DOWNLOAD_TIMEOUT_S) as resp:
            if not _final_url_ok(resp):
                raise UpdateError("upd_err_url")
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (ValueError, AttributeError):
                total = 0
            if total > limit:
                raise UpdateError("upd_err_size")
            done = 0
            with open(dest, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    _check_deadline(deadline)
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > limit:
                        raise UpdateError("upd_err_size")
                    f.write(chunk)
                    if progress:
                        progress(done, total)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("upd_err_network") from exc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def expected_hash(sums_text: str, name: str) -> str | None:
    """SHA-256 for exactly ``name`` from a ``sha256sum`` style file (None when absent/ambiguous)."""
    found = set()
    for line in sums_text.splitlines():
        m = _SUMS_LINE_RE.match(line)
        if m and m.group(2) == name:
            found.add(m.group(1).lower())
    return found.pop() if len(found) == 1 else None


def verify_file(path: Path, sums_text: str, name: str) -> bool:
    want = expected_hash(sums_text, name)
    return want is not None and sha256_file(path) == want


def usable_public_keys() -> list[bytes]:
    """The embedded PUBLIC_KEYS that are well-formed (32 bytes of hex)."""
    keys = []
    for k in PUBLIC_KEYS:
        try:
            b = bytes.fromhex(k)
        except (ValueError, TypeError):
            continue
        if len(b) == 32:
            keys.append(b)
    return keys


def verify_sums_signature(sums: bytes, sig_file: bytes, keys: list[bytes] | None = None) -> bool:
    """True when ``sig_file`` (base64 line) is a valid signature of ``sums`` by any embedded key."""
    keys = usable_public_keys() if keys is None else keys
    sig = ed25519.decode_signature(sig_file)
    return bool(keys) and sig is not None and any(ed25519.verify(k, sums, sig) for k in keys)


# (SHA-256, size, mtime_ns) of installers that prepare_update verified, kept only in memory so
# launch_installer can detect a swap of the file between verification and execution (the temp folder is not
# a trust boundary).
_verified: dict[str, tuple[str, int, int]] = {}


def _stat_sig(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def prepare_update(rel: Release, progress=None, cancel=None, opener=None,
                   workdir: Path | None = None) -> Path:
    """Download SHA256SUMS(+.sig) + installer of ``rel`` into a private temp folder and verify them.

    Order: signature of SHA256SUMS.txt first (no hash is trusted before that), then the installer
    hash. Returns the verified installer path. On any failure the folder is deleted and UpdateError
    raised. The folder comes from ``tempfile.mkdtemp``: since Python 3.12 it is created with a private
    ACL on Windows (only the current user), so other local users cannot swap files inside it.
    """
    if install_mode() != "installed":
        raise UpdateError("upd_err_mode")
    if not usable_public_keys():
        raise UpdateError("upd_err_unverified")
    name = rel.installer_name
    for n in (SUMS_NAME, name):
        if not is_valid_asset_url(rel.assets.get(n), rel.tag, n):
            raise UpdateError("upd_err_url")
    if SIG_NAME not in rel.assets:
        raise UpdateError("upd_err_unverified")
    if not is_valid_asset_url(rel.assets.get(SIG_NAME), rel.tag, SIG_NAME):
        raise UpdateError("upd_err_url")
    opener = opener or _build_opener()
    deadline = time.monotonic() + TOTAL_DEADLINE_S
    folder = Path(tempfile.mkdtemp(prefix=UPDATE_DIR_PREFIX, dir=workdir))
    try:
        sums_path, sig_path, exe_path = folder / SUMS_NAME, folder / SIG_NAME, folder / name
        _download(rel.assets[SUMS_NAME], sums_path, MAX_SUMS_BYTES, opener, None, cancel, deadline)
        _download(rel.assets[SIG_NAME], sig_path, MAX_SIG_BYTES, opener, None, cancel, deadline)
        sums_bytes = sums_path.read_bytes()
        if not verify_sums_signature(sums_bytes, sig_path.read_bytes()):
            log.info("update download failed: bad signature")
            raise UpdateError("upd_err_unverified")
        sums = sums_bytes.decode("utf-8", errors="replace")
        want = expected_hash(sums, name)
        if want is None:
            log.info("update download failed: no checksum")
            raise UpdateError("upd_err_nosum")
        _download(rel.assets[name], exe_path, MAX_INSTALLER_BYTES, opener, progress, cancel, deadline)
        got = sha256_file(exe_path)
        if got != want:
            log.info("update download failed: checksum mismatch")
            raise UpdateError("upd_err_hash")
        size, mtime = _stat_sig(exe_path)
        _verified[str(exe_path)] = (got, size, mtime)
        log.info("update download verified: %s", rel.version)
        return exe_path
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def installer_args() -> list[str]:
    # /LAUNCHAPP=1 is read by installer.iss: it relaunches the app after a silent install
    # (Qt apps do not register with Restart Manager, so /RESTARTAPPLICATIONS would not bring it back).
    return ["/SILENT", "/CLOSEAPPLICATIONS", "/LAUNCHAPP=1"]


def _system32() -> str:
    return str(Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:\\Windows") / "System32")


def reverify_installer(path: Path) -> None:
    """Full re-hash of a previously verified installer. CPU/disk heavy: call it from a worker thread.

    Raises UpdateError('upd_err_hash') when the file is unknown, missing, changed or being modified.
    On success the stored size/mtime snapshot is refreshed so ``launch_installer(rehash=False)`` can do a
    cheap final check.
    """
    key = str(path)
    entry = _verified.get(key)
    if entry is None:
        log.info("installer not verified; not launching")
        raise UpdateError("upd_err_hash")
    try:
        before = _stat_sig(path)
        got = sha256_file(Path(path))
        after = _stat_sig(path)
    except OSError as exc:
        raise UpdateError("upd_err_hash") from exc
    if got != entry[0] or before != after:
        log.info("installer changed since verification; not launching")
        raise UpdateError("upd_err_hash")
    _verified[key] = (entry[0], *after)


def discard_update(path: Path) -> None:
    """Delete the folder of a downloaded installer and forget its verification."""
    _verified.pop(str(path), None)
    shutil.rmtree(Path(path).parent, ignore_errors=True)


def launch_installer(path: Path, rehash: bool = True) -> None:
    """Start the (already verified) Inno installer detached from this process.

    Re-checks the install mode and, right before ``Popen``, that the file is still the one
    ``prepare_update`` verified: with ``rehash`` (default) by hashing it again; with ``rehash=False`` only by
    size + mtime against the snapshot taken by ``reverify_installer`` (which the caller must have run in a
    worker thread just before, so the UI thread never hashes tens of MB). Raises UpdateError otherwise.
    If ``Popen`` fails, the download folder is deleted and the verification forgotten before re-raising.
    """
    if install_mode() != "installed":
        raise UpdateError("upd_err_mode")
    key = str(path)
    if key not in _verified or not Path(path).is_file():
        log.info("installer changed or not verified; not launching")
        raise UpdateError("upd_err_hash")
    if rehash:
        reverify_installer(Path(path))
    try:
        unchanged = _stat_sig(Path(path)) == _verified[key][1:]
    except OSError:
        unchanged = False
    if not unchanged:
        log.info("installer changed or not verified; not launching")
        raise UpdateError("upd_err_hash")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([str(path), *installer_args()], close_fds=True, creationflags=flags,
                         cwd=_system32())
    except BaseException:
        discard_update(Path(path))
        raise


# ------------------------------------------------------------------ stale download cleanup
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse(entry: os.DirEntry) -> bool:
    """Symlink, junction or any reparse point: never followed."""
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def cleanup_old_updates(root: Path | None = None, now: float | None = None,
                        max_age_s: float = STALE_UPDATE_AGE_S) -> int:
    """Delete ``TBW-update-*`` folders older than a day left in the temp dir by interrupted updates.

    A link/junction/reparse point with that name is never followed or removed. Inside an old folder,
    links are unlinked as links (their target is left alone) before the rest is deleted.
    Returns how many folders were removed.
    """
    root = Path(root) if root is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for e in entries:
        if not e.name.startswith(UPDATE_DIR_PREFIX):
            continue
        try:
            if _is_reparse(e) or not e.is_dir(follow_symlinks=False):
                continue
            if now - e.stat(follow_symlinks=False).st_mtime < max_age_s:
                continue
            for child in list(os.scandir(e.path)):
                if _is_reparse(child):
                    for remove in (os.unlink, os.rmdir):      # file/file-symlink, then dir link/junction
                        try:
                            remove(child.path)
                            break
                        except OSError:
                            continue
            shutil.rmtree(e.path, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed
