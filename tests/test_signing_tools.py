import hashlib
import importlib.util
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


# ---------------------------------------------------------------- sign_release
class FakeGh:
    """Stands in for subprocess.run(['gh', ...]) against a draft release."""

    def __init__(self, tamper=False):
        self.setup, self.portable = b"setup-bytes", b"portable-bytes"
        h = lambda b: hashlib.sha256(b).hexdigest()
        self.sums = f"{h(self.setup)}  {SETUP}\n{h(self.portable)}  {PORTABLE}\n"
        if tamper:
            self.setup = b"tampered"
        self.calls, self.uploaded, self.sums_bytes = [], None, self.sums.encode()

    def __call__(self, cmd, **k):
        assert cmd[0] == "gh"
        self.calls.append(cmd[1:3])
        if cmd[1:3] == ["release", "download"]:
            d = Path(cmd[cmd.index("--dir") + 1])
            (d / "SHA256SUMS.txt").write_bytes(self.sums_bytes)
            (d / SETUP).write_bytes(self.setup)
            (d / PORTABLE).write_bytes(self.portable)
        elif cmd[1:3] == ["release", "upload"]:
            p = Path(cmd[4])
            assert p.name == "SHA256SUMS.txt.sig" and "--clobber" in cmd
            self.uploaded = p.read_text()
        return subprocess.CompletedProcess(cmd, 0, "", "")


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
    assert [c[2] for c in cmds] == ["download", "upload", "edit"]   # upload strictly before publish


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
    monkeypatch.setattr(sign_release.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "release not found"))
    assert sign_release.main([TAG, "--key", str(keyfile)]) == 1
    assert "release not found" in capsys.readouterr().err


def test_signature_roundtrip_matches_updater_verification(keyfile, monkeypatch):
    gh = FakeGh()
    monkeypatch.setattr(sign_release.subprocess, "run", gh)
    sign_release.sign_release(TAG, keyfile)
    assert updater.verify_sums_signature(gh.sums_bytes, gh.uploaded.encode(), [PUB])
    assert not updater.verify_sums_signature(gh.sums_bytes + b"x", gh.uploaded.encode(), [PUB])
