import hashlib
import io
import json
import threading
import urllib.error

import pytest

from taskbar_battery_widget import ed25519, updater
from taskbar_battery_widget.config import Config

TAG = "v1.4.0"
EXE = "TaskbarBatteryWidget-Setup-1.4.0.exe"
BASE = f"https://github.com/Gabrielfch/taskbar-battery-widget/releases/download/{TAG}/"


SEED_A, SEED_B = bytes([1]) * 32, bytes([2]) * 32
PUB_A, PUB_B = (ed25519.public_from_seed(x).hex() for x in (SEED_A, SEED_B))
SIG = "SHA256SUMS.txt.sig"
REAL_INSTALL_MODE = updater.install_mode


@pytest.fixture(autouse=True)
def _signed_installed(monkeypatch):
    """Default environment: two embedded test keys, running as the installed copy."""
    monkeypatch.setattr(updater, "PUBLIC_KEYS", (PUB_A, PUB_B))
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "installed")
    updater._verified.clear()


def sig_for(sums_text, seed=SEED_A):
    return ed25519.encode_signature(ed25519.sign(seed, sums_text.encode()))


def api(tag=TAG, assets=True, **kw):
    d = {"tag_name": tag, "draft": False, "prerelease": False, "assets": []}
    if assets:
        d["assets"] = [{"name": EXE, "browser_download_url": BASE + EXE},
                       {"name": "SHA256SUMS.txt", "browser_download_url": BASE + "SHA256SUMS.txt"},
                       {"name": SIG, "browser_download_url": BASE + SIG}]
    d.update(kw)
    return d


# ---------------------------------------------------------------- versions
def test_parse_and_compare_versions():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.10.0") == (1, 10, 0)
    for bad in ("1.2", "v1.2.3-beta", "latest", "", None, 5, "v1.2.3.4", "1.2.x", " v1.2.3"):
        assert updater.parse_version(bad) is None
    assert updater.is_newer("v1.10.0", "1.9.9")
    assert not updater.is_newer("v1.3.0", "1.3.0")
    assert not updater.is_newer("v1.2.9", "1.3.0")
    assert not updater.is_newer("garbage", "1.3.0")


# ---------------------------------------------------------------- API parsing
def test_parse_release_normal():
    r = updater.parse_release(api())
    assert r.version == "1.4.0" and r.tag == TAG and r.can_install
    assert r.page_url == "https://github.com/Gabrielfch/taskbar-battery-widget/releases/tag/v1.4.0"


@pytest.mark.parametrize("extra", [{"prerelease": True}, {"draft": True}])
def test_parse_release_ignores_prerelease_and_draft(extra):
    assert updater.parse_release(api(**extra)) is None


def test_parse_release_without_assets_cannot_install():
    r = updater.parse_release(api(assets=False))
    assert r is not None and not r.can_install


def test_can_install_requires_signature_asset_and_keys(monkeypatch):
    d = api()
    d["assets"] = [a for a in d["assets"] if a["name"] != SIG]
    assert not updater.parse_release(d).can_install
    assert updater.parse_release(api()).can_install
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ())
    assert not updater.parse_release(api()).can_install
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ("zz", "ab" * 5))      # malformed keys are ignored
    assert not updater.parse_release(api()).can_install


@pytest.mark.parametrize("tag", ["1.4.0", "v1.4", "vNext", "v1.4.0-rc1", "v01.4.0", None, 3])
def test_parse_release_invalid_tag(tag):
    assert updater.parse_release(api(tag=tag)) is None


def test_parse_release_bad_shapes():
    assert updater.parse_release([]) is None
    assert updater.parse_release({"tag_name": TAG, "assets": "x"}).assets == {}


def test_parse_release_drops_foreign_asset_urls():
    d = api()
    d["assets"][0]["browser_download_url"] = "https://evil.example/" + EXE
    r = updater.parse_release(d)
    assert EXE not in r.assets and not r.can_install


# ---------------------------------------------------------------- URL validation
@pytest.mark.parametrize("url", [
    BASE.replace("https", "http") + EXE,
    BASE.replace("github.com", "evil.com") + EXE,
    BASE.replace("github.com", "github.com.evil.com") + EXE,
    BASE.replace("Gabrielfch", "other") + EXE,
    BASE.replace(TAG, "v1.4.1") + EXE,
    BASE + "other.exe",
    BASE + EXE + "?x=1",
    BASE + EXE + "#f",
    BASE + "../" + EXE,
    "https://user@github.com/Gabrielfch/taskbar-battery-widget/releases/download/v1.4.0/" + EXE,
    "https://github.com:8443/Gabrielfch/taskbar-battery-widget/releases/download/v1.4.0/" + EXE,
    None, 5, "",
])
def test_invalid_asset_urls(url):
    assert not updater.is_valid_asset_url(url, TAG, EXE)


def test_valid_asset_url():
    assert updater.is_valid_asset_url(BASE + EXE, TAG, EXE)


def test_redirect_to_foreign_host_rejected():
    h = updater._SafeRedirect()
    req = updater._request(BASE + EXE, "x")
    for bad in ("http://objects.githubusercontent.com/a", "https://evil.example/a",
                "https://objects.githubusercontent.com.evil.io/a"):
        with pytest.raises(urllib.error.URLError):
            h.redirect_request(req, io.BytesIO(), 302, "Found", {}, bad)
    ok = h.redirect_request(req, io.BytesIO(), 302, "Found", {},
                            "https://objects.githubusercontent.com/a")
    assert ok is not None


# ---------------------------------------------------------------- fake network
class FakeResp:
    def __init__(self, data, url="https://objects.githubusercontent.com/x", length=None):
        self._b = io.BytesIO(data)
        self._url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def read(self, n=-1):
        return self._b.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_header("User-agent"), timeout))
        r = self.routes[req.full_url]
        if isinstance(r, Exception):
            raise r
        return r


def test_fetch_latest_request_shape():
    op = FakeOpener({updater.API_URL: FakeResp(json.dumps(api()).encode(),
                                               url="https://api.github.com/x")})
    r = updater.fetch_latest(op)
    assert r.version == "1.4.0"
    url, ua, timeout = op.calls[0]
    assert url == updater.API_URL and ua.startswith("TaskbarBatteryWidget/") and timeout == 10


def test_fetch_latest_errors():
    for payload in (urllib.error.URLError("x"), TimeoutError()):
        with pytest.raises(updater.UpdateError):
            updater.fetch_latest(FakeOpener({updater.API_URL: payload}))
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest(FakeOpener({updater.API_URL: FakeResp(b"not json",
                                                                   url="https://api.github.com/x")}))


def test_check_only_returns_newer():
    def op(tag):
        return FakeOpener({updater.API_URL: FakeResp(json.dumps(api(tag=tag)).encode(),
                                                     url="https://api.github.com/x")})
    assert updater.check(op("v1.4.0"), current="1.3.0").version == "1.4.0"
    assert updater.check(op("v1.3.0"), current="1.3.0") is None
    assert updater.check(op("v1.2.0"), current="1.3.0") is None


# ---------------------------------------------------------------- sha256 / download
def sums_for(data, name=EXE, sep="  "):
    return f"{hashlib.sha256(data).hexdigest()}{sep}{name}\n"


def release():
    return updater.parse_release(api())


def routes(data, sums, length=None, sig=None):
    return {BASE + EXE: FakeResp(data, length=len(data) if length is None else length),
            BASE + "SHA256SUMS.txt": FakeResp(sums.encode()),
            BASE + SIG: FakeResp((sig_for(sums) if sig is None else sig).encode())}


def test_prepare_update_ok(tmp_path):
    data = b"installer-bytes" * 100
    seen = []
    p = updater.prepare_update(release(), progress=lambda d, t: seen.append((d, t)),
                               opener=FakeOpener(routes(data, sums_for(data))), workdir=tmp_path)
    assert p.read_bytes() == data and p.name == EXE and seen[-1][0] == len(data)


def test_prepare_update_binary_marker_format(tmp_path):
    data = b"abc"
    p = updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data, sep=" *"))),
                               workdir=tmp_path)
    assert p.exists()


def test_prepare_update_hash_mismatch_deletes(tmp_path):
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(routes(b"real", sums_for(b"other"))),
                               workdir=tmp_path)
    assert e.value.key == "upd_err_hash" and list(tmp_path.iterdir()) == []


def test_prepare_update_missing_checksum_line(tmp_path):
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(routes(b"x", sums_for(b"x", name="other.exe"))),
                               workdir=tmp_path)
    assert e.value.key == "upd_err_nosum" and list(tmp_path.iterdir()) == []


def test_expected_hash_requires_exact_name_and_unique():
    h = "a" * 64
    assert updater.expected_hash(f"{h}  {EXE}\n", EXE) == h
    assert updater.expected_hash(f"{h}  x{EXE}\n", EXE) is None
    assert updater.expected_hash(f"{h}  {EXE}\n{'b' * 64}  {EXE}\n", EXE) is None
    assert updater.expected_hash("zz  " + EXE, EXE) is None


def test_size_limit_by_header_and_by_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "MAX_INSTALLER_BYTES", 100)
    data = b"x" * 500
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data))), workdir=tmp_path)
    assert e.value.key == "upd_err_size"
    with pytest.raises(updater.UpdateError) as e:               # lying Content-Length
        updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data), length=10)),
                               workdir=tmp_path)
    assert e.value.key == "upd_err_size" and list(tmp_path.iterdir()) == []


def test_download_rejects_final_url_on_foreign_host(tmp_path):
    data = b"x"
    r = routes(data, sums_for(data))
    r[BASE + "SHA256SUMS.txt"] = FakeResp(sums_for(data).encode(), url="https://evil.example/s")
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(r), workdir=tmp_path)
    assert e.value.key == "upd_err_url"


def test_prepare_update_rejects_bad_asset_url(tmp_path):
    rel = updater.Release("1.4.0", TAG, "p", {EXE: "https://evil.example/" + EXE,
                                              "SHA256SUMS.txt": BASE + "SHA256SUMS.txt"})
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(rel, opener=FakeOpener({}), workdir=tmp_path)
    assert e.value.key == "upd_err_url"


def test_prepare_update_cancel(tmp_path):
    ev = threading.Event()
    ev.set()
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), cancel=ev, opener=FakeOpener(routes(b"x", sums_for(b"x"))),
                               workdir=tmp_path)
    assert e.value.key == "upd_err_cancelled" and list(tmp_path.iterdir()) == []


def test_installer_args():
    assert updater.installer_args() == ["/SILENT", "/CLOSEAPPLICATIONS", "/LAUNCHAPP=1"]


# ---------------------------------------------------------------- install mode
def test_install_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = tmp_path / "Programs" / "Taskbar Battery Widget" / "TaskbarBatteryWidget.exe"
    assert REAL_INSTALL_MODE(frozen=True, exe=str(inst)) == "installed"
    assert REAL_INSTALL_MODE(frozen=True, exe=str(tmp_path / "Downloads" / "x.exe")) == "portable"
    other = tmp_path / "Programs" / "Taskbar Battery Widget2" / "x.exe"
    assert REAL_INSTALL_MODE(frozen=True, exe=str(other)) == "portable"
    assert REAL_INSTALL_MODE(frozen=False, exe=str(inst)) == "source"


# ---------------------------------------------------------------- automatic check
def test_auto_check_off_by_default_and_daily(tmp_path):
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("update_check_auto") is False and cfg.get("update_last_check") == 0
    assert not updater.should_auto_check(cfg, 10**9)
    cfg.set("update_check_auto", True)
    assert updater.should_auto_check(cfg, 10**9)               # never checked
    updater.record_check(cfg, 10**9)
    assert not updater.should_auto_check(cfg, 10**9 + 3600)
    assert not updater.should_auto_check(cfg, 10**9 + 24 * 3600 - 1)
    assert updater.should_auto_check(cfg, 10**9 + 24 * 3600)
    assert updater.should_auto_check(cfg, 10**9 - 5)           # clock went backwards


def test_update_config_validation(tmp_path):
    cfg = Config(tmp_path / "c.json")
    for k, v in (("update_check_auto", 1), ("update_last_check", -1), ("update_last_check", True),
                 ("update_last_check", "x"), ("update_last_check", 10**12)):
        with pytest.raises(ValueError):
            cfg.set(k, v)
    (tmp_path / "d.json").write_text('{"update_check_auto": "yes", "update_last_check": -4}')
    c2 = Config(tmp_path / "d.json")
    assert c2.get("update_check_auto") is False and c2.get("update_last_check") == 0


# ---------------------------------------------------------------- signature
def test_signature_valid_with_second_of_two_keys(tmp_path):
    data = b"inst" * 50
    sums = sums_for(data)
    r = routes(data, sums, sig=sig_for(sums, SEED_B))
    p = updater.prepare_update(release(), opener=FakeOpener(r), workdir=tmp_path)
    assert p.read_bytes() == data


def test_signature_by_unknown_key_rejected(tmp_path):
    data = b"inst"
    sums = sums_for(data)
    r = routes(data, sums, sig=sig_for(sums, bytes([9]) * 32))
    op = FakeOpener(r)
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=op, workdir=tmp_path)
    assert e.value.key == "upd_err_unverified" and list(tmp_path.iterdir()) == []
    assert BASE + EXE not in [c[0] for c in op.calls]           # installer never downloaded


def test_signature_over_tampered_sums_rejected(tmp_path):
    data = b"inst"
    good, evil = sums_for(data), sums_for(b"evil")
    r = routes(data, evil, sig=sig_for(good))                   # signature belongs to other sums
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(r), workdir=tmp_path)
    assert e.value.key == "upd_err_unverified"


@pytest.mark.parametrize("sig", ["", "not base64!!", "AAAA\n", "A" * 4096])
def test_malformed_signature_rejected(tmp_path, sig):
    data = b"inst"
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data), sig=sig)),
                               workdir=tmp_path)
    assert e.value.key in ("upd_err_unverified", "upd_err_size") and list(tmp_path.iterdir()) == []


def test_missing_signature_asset_refuses(tmp_path):
    rel = updater.Release("1.4.0", TAG, "p", {EXE: BASE + EXE, "SHA256SUMS.txt": BASE + "SHA256SUMS.txt"})
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(rel, opener=FakeOpener({}), workdir=tmp_path)
    assert e.value.key == "upd_err_unverified" and list(tmp_path.iterdir()) == []


def test_no_embedded_keys_refuses_before_any_download(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ())
    op = FakeOpener({})
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=op, workdir=tmp_path)
    assert e.value.key == "upd_err_unverified" and op.calls == []


def test_signature_url_validated(tmp_path):
    rel = updater.Release("1.4.0", TAG, "p", {EXE: BASE + EXE, "SHA256SUMS.txt": BASE + "SHA256SUMS.txt",
                                              SIG: "https://evil.example/" + SIG})
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(rel, opener=FakeOpener({}), workdir=tmp_path)
    assert e.value.key == "upd_err_url"
    assert not updater.is_valid_asset_url(BASE + SIG + "?x=1", TAG, SIG)
    assert updater.is_valid_asset_url(BASE + SIG, TAG, SIG)


def test_signature_final_url_on_foreign_host_rejected(tmp_path):
    data = b"x"
    r = routes(data, sums_for(data))
    r[BASE + SIG] = FakeResp(sig_for(sums_for(data)).encode(), url="https://evil.example/s")
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(r), workdir=tmp_path)
    assert e.value.key == "upd_err_url"


# ---------------------------------------------------------------- mode, re-hash, deadline
def test_prepare_requires_installed_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "portable")
    op = FakeOpener({})
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=op, workdir=tmp_path)
    assert e.value.key == "upd_err_mode" and op.calls == []


def _prepared(tmp_path):
    data = b"installer" * 20
    return updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data))),
                                  workdir=tmp_path)


class PopenSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append((a, k))


def test_launch_ok_uses_system32_cwd_and_args(tmp_path, monkeypatch):
    spy = PopenSpy()
    monkeypatch.setattr(updater.subprocess, "Popen", spy)
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    p = _prepared(tmp_path)
    updater.launch_installer(p)
    (argv,), kw = spy.calls[0]
    assert argv == [str(p), "/SILENT", "/CLOSEAPPLICATIONS", "/LAUNCHAPP=1"]
    assert kw["cwd"].lower() == "c:\\windows\\system32"


def test_launch_aborts_when_file_changed_after_verification(tmp_path, monkeypatch):
    spy = PopenSpy()
    monkeypatch.setattr(updater.subprocess, "Popen", spy)
    p = _prepared(tmp_path)
    p.write_bytes(b"swapped")
    with pytest.raises(updater.UpdateError) as e:
        updater.launch_installer(p)
    assert e.value.key == "upd_err_hash" and spy.calls == []


def test_launch_refuses_unverified_path(tmp_path, monkeypatch):
    spy = PopenSpy()
    monkeypatch.setattr(updater.subprocess, "Popen", spy)
    f = tmp_path / "x.exe"
    f.write_bytes(b"x")
    with pytest.raises(updater.UpdateError):
        updater.launch_installer(f)
    assert spy.calls == []


def test_launch_requires_installed_mode(tmp_path, monkeypatch):
    spy = PopenSpy()
    monkeypatch.setattr(updater.subprocess, "Popen", spy)
    p = _prepared(tmp_path)
    monkeypatch.setattr(updater, "install_mode", lambda *a, **k: "source")
    with pytest.raises(updater.UpdateError) as e:
        updater.launch_installer(p)
    assert e.value.key == "upd_err_mode" and spy.calls == []


def test_total_deadline_stops_download(tmp_path, monkeypatch):
    data = b"x" * 100
    clock = iter([0.0, 1.0, 2.0])                      # deadline set at 0 + 600; then jumps far ahead
    monkeypatch.setattr(updater.time, "monotonic", lambda: next(clock, 10_000.0))
    with pytest.raises(updater.UpdateError) as e:
        updater.prepare_update(release(), opener=FakeOpener(routes(data, sums_for(data))), workdir=tmp_path)
    assert e.value.key == "upd_err_timeout" and list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- stale folder cleanup
NOW = 1_000_000_000.0


def _age(path, days):
    import os
    os.utime(path, (NOW - days * 86400, NOW - days * 86400))


def test_cleanup_old_updates_removes_only_old_ours(tmp_path):
    old, new, other = (tmp_path / n for n in ("TBW-update-old", "TBW-update-new", "other-old"))
    for d in (old, new, other):
        d.mkdir()
        (d / "f.exe").write_bytes(b"x")
    _age(old, 2)
    _age(other, 5)
    _age(new, 0.04)
    assert updater.cleanup_old_updates(tmp_path, now=NOW) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_cleanup_does_not_follow_reparse_points(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    link = tmp_path / "TBW-update-link"                 # pretend this name is a junction
    link.mkdir()
    _age(link, 9)
    real = updater._is_reparse
    monkeypatch.setattr(updater, "_is_reparse", lambda e: e.name == "TBW-update-link" or real(e))
    removed = []
    monkeypatch.setattr(updater.shutil, "rmtree", lambda p, **k: removed.append(p))
    assert updater.cleanup_old_updates(tmp_path, now=NOW) == 0
    assert removed == [] and link.exists() and (victim / "keep.txt").read_text() == "keep"


def test_cleanup_unlinks_inner_reparse_entry_not_its_target(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    old = tmp_path / "TBW-update-old"
    old.mkdir()
    (old / "junction").mkdir()                          # stands in for a junction inside the folder
    _age(old, 9)
    real = updater._is_reparse
    monkeypatch.setattr(updater, "_is_reparse", lambda e: e.name == "junction" or real(e))
    unlinked = []
    real_rmdir = updater.os.rmdir
    monkeypatch.setattr(updater.os, "rmdir", lambda p: (unlinked.append(p), real_rmdir(p))[1])
    assert updater.cleanup_old_updates(tmp_path, now=NOW) == 1
    assert unlinked and unlinked[0].endswith("junction")
    assert not old.exists() and (target / "keep.txt").read_text() == "keep"


def test_cleanup_missing_root_is_harmless(tmp_path):
    assert updater.cleanup_old_updates(tmp_path / "nope") == 0
