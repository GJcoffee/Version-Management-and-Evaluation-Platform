"""敏感文件权限收紧：POSIX 行为不变；Windows 分支通过 mock 平台判断验证 icacls 调用参数。"""

import os
import stat
import subprocess

import pytest
from evalp import auth, secrets
from evalp.utils import fsperm


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def fake_windows(monkeypatch):
    """假装在 Windows 上：记录 subprocess.run 调用而不真的执行。"""
    calls: list[tuple[list[str], dict]] = []
    result = {"proc": _Proc()}

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if isinstance(result["proc"], Exception):
            raise result["proc"]
        return result["proc"]

    monkeypatch.setattr(fsperm, "is_windows", lambda: True)
    monkeypatch.setattr(fsperm.subprocess, "run", fake_run)
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    return calls, result


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位")
def test_posix_chmod_600(tmp_path):
    p = tmp_path / "f"
    p.write_text("x")
    p.chmod(0o644)
    assert fsperm.restrict_to_owner(p) is True
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_posix_does_not_call_icacls(tmp_path, monkeypatch):
    p = tmp_path / "f"
    p.write_text("x")
    monkeypatch.setattr(fsperm, "is_windows", lambda: False)
    monkeypatch.setattr(fsperm.subprocess, "run", lambda *a, **k: pytest.fail("POSIX 不应调用 icacls"))
    assert fsperm.restrict_to_owner(p) is True


def test_posix_missing_file_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(fsperm, "is_windows", lambda: False)
    assert fsperm.restrict_to_owner(tmp_path / "nope") is False


def test_windows_icacls_arguments(tmp_path, fake_windows):
    calls, _ = fake_windows
    p = tmp_path / "token"
    p.write_text("x")
    assert fsperm.restrict_to_owner(p) is True
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd == ["icacls", str(p), "/inheritance:r", "/grant:r", "WORKGROUP\\alice:(F)"]
    assert kw["timeout"] == fsperm.ICACLS_TIMEOUT_S
    assert kw["check"] is False
    assert kw["capture_output"] is True


def test_windows_user_without_domain(monkeypatch):
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.setenv("USERNAME", "bob")
    assert fsperm.windows_current_user() == "bob"


@pytest.mark.parametrize(
    "outcome",
    [
        _Proc(returncode=5, stderr="Access is denied."),
        FileNotFoundError("icacls"),
        subprocess.TimeoutExpired("icacls", 10),
    ],
)
def test_windows_failure_is_best_effort(tmp_path, fake_windows, outcome):
    _, result = fake_windows
    result["proc"] = outcome
    p = tmp_path / "token"
    p.write_text("x")
    assert fsperm.restrict_to_owner(p) is False  # 不抛异常


def test_ensure_token_restricts_on_create_and_reuse(tmp_path, fake_windows):
    calls, _ = fake_windows
    p = tmp_path / "data" / "token"
    t1 = auth.ensure_token(p)
    assert p.read_text() == t1
    assert [c[0][1] for c in calls] == [str(p)]
    assert auth.ensure_token(p) == t1
    assert len(calls) == 2  # 已存在的 token 每次启动也会重新收紧


def test_secrets_file_restricted_on_windows(tmp_path, fake_windows, monkeypatch):
    calls, _ = fake_windows
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    prev = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        store = secrets.SecretStore(tmp_path / "secrets.enc")
        store._file_set("k", "v")
    finally:
        keyring.set_keyring(prev)
    assert any(c[0][1] == str(tmp_path / "secrets.enc") for c in calls)
