import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from taskbar_battery_widget import ed25519, updater

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "signing"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"tbw_tool_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_keys = _load("gen_keys")
sign_release = _load("sign_release")

SEED = bytes(range(32))
PUB = ed25519.public_from_seed(SEED)
TAG, VER = "v1.4.0", "1.4.0"
SETUP, PORTABLE = f"TaskbarBatteryWidget-Setup-{VER}.exe", f"TaskbarBatteryWidget-portable-{VER}.exe"


# ---------------------------------------------------------------- gen_keys
@pytest.fixture
def no_icacls(monkeypatch):
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(gen_keys.subprocess, "run", fake_run)
    return calls


def test_gen_keys_writes_seed_prints_only_public(tmp_path, capsys, no_icacls):
    key = tmp_path / "sub" / "k.key"
    assert gen_keys.main([str(key)]) == 0
    out = capsys.readouterr()
    seed_hex = key.read_text().strip()
    assert len(seed_hex) == 64 and bytes.fromhex(seed_hex)
    pub = ed25519.public_from_seed(bytes.fromhex(seed_hex)).hex()
    assert pub in out.out and f'"{pub}",' in out.out and "PUBLIC_KEYS" in out.out
    assert seed_hex not in out.out and seed_hex not in out.err
    assert "password manager" in out.out
    assert any(c[0] == "icacls" for c in no_icacls)             # permissions restriction attempted


def test_gen_keys_refuses_to_overwrite_without_force(tmp_path, capsys, no_icacls):
    key = tmp_path / "k.key"
    key.write_text("precious")
    assert gen_keys.main([str(key)]) == 1
    assert key.read_text() == "precious"
    assert "already exists" in capsys.readouterr().err
    assert gen_keys.main([str(key), "--force"]) == 0
    assert key.read_text() != "precious" and len(key.read_text().strip()) == 64


def test_gen_keys_refuses_path_inside_repo(capsys, no_icacls):
    inside = gen_keys.REPO_ROOT / "secret.key"
    assert gen_keys.main([str(inside)]) == 2
    assert not inside.exists()


def test_gen_keys_default_path(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    assert gen_keys.default_key_path().parts[-2:] == (".tbw-signing", "tbw_release_ed25519.key")


# ---------------------------------------------------------------- gen_keys: permissions
def test_gen_keys_restricts_new_folder_before_writing_seed(tmp_path, monkeypatch):
    key = tmp_path / "newdir" / "k.key"
    events = []

    def fake_run(cmd, **k):
        target = Path(cmd[1])
        events.append((target.name, target.is_file() and target.stat().st_size))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(gen_keys.subprocess, "run", fake_run)
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(gen_keys.sys, "platform", "win32")
    assert gen_keys.main([str(key)]) == 0
    assert [e[0] for e in events] == ["newdir", "k.key"]          # folder first, then the file
    assert events[1][1] == 0                                      # file was still EMPTY when restricted
    assert len(key.read_text().strip()) == 64


def test_gen_keys_leaves_foreign_existing_folder_alone(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "mine"
    folder.mkdir()
    targets = []

    def fake_run(cmd, **k):
        targets.append(Path(cmd[1]).name)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(gen_keys.subprocess, "run", fake_run)
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(gen_keys.sys, "platform", "win32")
    assert gen_keys.main([str(folder / "k.key")]) == 0
    assert targets == ["k.key"]                                   # only the file, never the folder
    assert "left untouched" in capsys.readouterr().out


def test_gen_keys_restricts_existing_default_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tbw-signing").mkdir(parents=True)
    targets = []

    def fake_run(cmd, **k):
        targets.append(Path(cmd[1]).name)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(gen_keys.subprocess, "run", fake_run)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(gen_keys.sys, "platform", "win32")
    assert gen_keys.main([]) == 0
    assert targets == [".tbw-signing", "tbw_release_ed25519.key"]


# ---------------------------------------------------------------- sign_release
def _bytes_result(cmd, data):
    return subprocess.CompletedProcess(cmd, 0, data, b"")


class FakeGh:
    """Stands in for subprocess.run(['gh', ...]) against a draft release."""

    def __init__(self, tamper=False, version="2.102.0", no_download=False, attest_ok=True):
        self.setup, self.portable = b"setup-bytes", b"portable-bytes"
        h = lambda b: hashlib.sha256(b).hexdigest()
        self.sums = f"{h(self.setup)}  {SETUP}\n{h(self.portable)}  {PORTABLE}\n"
        if tamper:
            self.setup = b"tampered"
        self.calls, self.uploaded, self.sums_bytes = [], None, self.sums.encode()
        self.version, self.no_download, self.attest_ok = version, no_download, attest_ok
        self.corrupt_after_upload = False
        self.attested = []

    def files(self):
        f = {"SHA256SUMS.txt": self.sums_bytes, SETUP: self.setup, PORTABLE: self.portable}
        if self.uploaded is not None:
            f["SHA256SUMS.txt.sig"] = self.uploaded.encode()
            if self.corrupt_after_upload:
                f[PORTABLE] = b"swapped-after-signing"
        return f

    def __call__(self, cmd, **k):
        assert cmd[0] == "gh"
        text = bool(k.get("text"))
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out if text else out.encode(), "" if text else b"")
        self.calls.append(cmd[1:3])
        if cmd[1] == "--version":
            return ok(f"gh version {self.version} (2026-01-01)\nhttps://github.com/cli/cli\n")
        if cmd[1:3] == ["release", "download"]:
            if self.no_download:
                return subprocess.CompletedProcess(cmd, 1, "", "release not found")
            d = Path(cmd[cmd.index("--dir") + 1])
            wanted = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--pattern"]
            for n, data in self.files().items():
                if n in wanted:
                    (d / n).write_bytes(data)
            return ok()
        if cmd[1] == "api" and cmd[2].endswith("/releases"):
            rels = [{"tag_name": "v0.0.1", "draft": False, "assets": []},
                    {"tag_name": TAG, "draft": True,
                     "assets": [{"name": n, "id": 100 + i} for i, n in enumerate(self.files())]}]
            return ok(json.dumps([rels]))
        if cmd[1] == "api" and "/releases/assets/" in cmd[-1]:
            aid = int(cmd[-1].rsplit("/", 1)[1]) - 100
            return _bytes_result(cmd, list(self.files().values())[aid])
        if cmd[1:3] == ["release", "upload"]:
            p = Path(cmd[4])
            assert p.name == "SHA256SUMS.txt.sig" and "--clobber" in cmd
            self.uploaded = p.read_text()
            return ok()
        if cmd[1:3] == ["attestation", "verify"]:
            self.attested.append(Path(cmd[3]).name)
            assert "--repo" in cmd
            if not self.attest_ok:
                return subprocess.CompletedProcess(cmd, 1, "", "no attestations found")
            return ok()
        return ok()


@pytest.fixture(autouse=True)
def _embedded_key(monkeypatch):
    monkeypatch.setattr(updater, "PUBLIC_KEYS", (PUB.hex(),))


@pytest.fixture
def keyfile(tmp_path):
    p = tmp_path / "k.key"
    p.write_text(SEED.hex() + "\n")
    return p


def test_sign_release_uploads_verifiable_signature(keyfile, monkeypatch, capsys):
    gh = FakeGh()
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile)]) == 0
    sig = ed25519.decode_signature(gh.uploaded.encode())
    assert sig is not None and ed25519.verify(PUB, gh.sums_bytes, sig)
    assert gh.uploaded.count("\n") == 1
    assert ["release", "edit"] not in gh.calls                      # no --publish: stays a draft
    assert sorted(gh.attested) == sorted([SETUP, PORTABLE])
    out = capsys.readouterr()
    assert SEED.hex() not in out.out + out.err


def test_sign_release_publish_edits_draft_flag(keyfile, monkeypatch):
    cmds = []
    gh = FakeGh()

    def spy(cmd, **k):
        cmds.append(cmd)
        return gh(cmd, **k)
    monkeypatch.setattr(sign_release.subprocess, "run", spy)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 0
    assert cmds[-1][:4] == ["gh", "release", "edit", TAG] and "--draft=false" in cmds[-1]
    steps = [c[2] if c[1] == "release" else c[1] for c in cmds]
    assert steps == ["--version", "download", "attestation", "attestation", "upload", "download", "edit"]


def test_sign_release_aborts_on_hash_mismatch(keyfile, monkeypatch, capsys):
    gh = FakeGh(tamper=True)
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 1
    assert "hash mismatch" in capsys.readouterr().err
    assert gh.uploaded is None and ["release", "upload"] not in gh.calls
    assert ["release", "edit"] not in gh.calls


def test_sign_release_aborts_when_exe_not_listed(keyfile, monkeypatch, capsys):
    gh = FakeGh()
    gh.sums_bytes = f"{'0' * 64}  other.exe\n".encode()
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile)]) == 1
    assert gh.uploaded is None


def test_sign_release_rejects_bad_tag_and_key(keyfile, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sign_release.subprocess, "run", lambda *a, **k: pytest.fail("gh must not run"))
    assert sign_release.main(["1.4.0", "--key", str(keyfile)]) == 1
    bad = tmp_path / "bad.key"
    bad.write_text("not hex")
    assert sign_release.main([TAG, "--key", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "not hex" not in err
    assert sign_release.main([TAG, "--key", str(tmp_path / "missing.key")]) == 1


def test_sign_release_gh_failure_reported(keyfile, monkeypatch, capsys):
    def fail(cmd, **k):
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "gh version 2.102.0 (x)\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "release not found")
    monkeypatch.setattr(sign_release.subprocess, "run", fail)
    assert sign_release.main([TAG, "--key", str(keyfile)]) == 1
    assert "release not found" in capsys.readouterr().err


def test_signature_roundtrip_matches_updater_verification(keyfile, monkeypatch):
    gh = FakeGh()
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    sign_release.sign_release(TAG, keyfile)
    assert updater.verify_sums_signature(gh.sums_bytes, gh.uploaded.encode(), [PUB])
    assert not updater.verify_sums_signature(gh.sums_bytes + b"x", gh.uploaded.encode(), [PUB])


def test_sign_release_aborts_before_any_gh_call_if_key_not_embedded(keyfile, monkeypatch, capsys):
    monkeypatch.setattr(updater, "PUBLIC_KEYS", ("00" * 32,))
    monkeypatch.setattr(sign_release.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 1
    assert "NOT in updater.PUBLIC_KEYS" in capsys.readouterr().err


def test_sign_release_reverifies_after_upload_and_does_not_publish_on_mismatch(keyfile, monkeypatch, capsys):
    gh = FakeGh()
    gh.corrupt_after_upload = True                                  # draft changes after the signature upload
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 1
    assert "re-verification failed" in capsys.readouterr().err
    assert ["release", "edit"] not in gh.calls


def test_sign_release_reverify_fails_if_signature_does_not_verify(keyfile, monkeypatch, capsys):
    gh = FakeGh()

    def tampered(cmd, **k):
        res = gh(cmd, **k)
        if cmd[1:3] == ["release", "upload"]:
            gh.uploaded = ed25519.encode_signature(b"\x01" * 64) + "\n"       # the server ends up with another sig
        return res
    monkeypatch.setattr(sign_release.subprocess, "run", tampered)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 1
    assert ["release", "edit"] not in gh.calls


def test_failed_attestation_aborts_before_upload(keyfile, monkeypatch, capsys):
    gh = FakeGh(attest_ok=False)
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 1
    assert "attestation failed" in capsys.readouterr().err
    assert gh.uploaded is None


def test_skip_attestation_skips_verification_with_warning(keyfile, monkeypatch, capsys):
    gh = FakeGh(attest_ok=False)
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile), "--skip-attestation"]) == 0
    assert gh.attested == [] and gh.uploaded is not None
    assert "--skip-attestation" in capsys.readouterr().err


def test_old_gh_aborts(keyfile, monkeypatch, capsys):
    gh = FakeGh(version="2.48.9")
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile)]) == 1
    assert "too old" in capsys.readouterr().err and gh.uploaded is None


def test_draft_download_falls_back_to_api_by_tag(keyfile, monkeypatch, capsys):
    gh = FakeGh(no_download=True)
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main([TAG, "--key", str(keyfile), "--publish"]) == 0
    assert ["api", "repos/Gabrielfch/taskbar-battery-widget/releases"] in gh.calls
    assert gh.uploaded is not None and ["release", "edit"] in gh.calls


def test_api_fallback_fails_when_tag_missing(keyfile, monkeypatch, capsys):
    gh = FakeGh(no_download=True)
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    assert sign_release.main(["v1.9.9", "--key", str(keyfile)]) == 1
    assert "exactly one release" in capsys.readouterr().err and gh.uploaded is None
