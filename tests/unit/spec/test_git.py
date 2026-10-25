"""代码型 agent 的 git 信息采集（用临时 git 仓库）。"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from evalp.errors import GitDirty, SpecInvalid
from evalp.spec.git import capture_git


def run(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "agent"
    r.mkdir()
    run(r, "init", "-q", "-b", "main")
    (r / "agent.py").write_text("print('v1')\n", encoding="utf-8")
    (r / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    run(r, "add", "-A")
    run(r, "commit", "-q", "-m", "init")
    return r


async def test_clean_repo(repo):
    info = await capture_git(str(repo))
    assert info.commit == run(repo, "rev-parse", "HEAD").strip()
    assert info.branch == "main" and info.dirty is False
    assert info.patch_hash is None and info.patch_blob is None
    assert info.spec_pin() == {"commit": info.commit, "patch_hash": None}
    assert info.source_column() == {
        "git_commit": info.commit,
        "git_branch": "main",
        "dirty": False,
        "patch_blob": None,
    }


async def test_works_from_subdirectory(repo):
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "x.py").write_text("x", encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "sub")
    info = await capture_git(str(sub))
    assert info.commit == run(repo, "rev-parse", "HEAD").strip()


async def test_new_commit_changes_commit(repo):
    first = (await capture_git(str(repo))).commit
    (repo / "agent.py").write_text("print('v2')\n", encoding="utf-8")
    run(repo, "commit", "-q", "-am", "v2")
    assert (await capture_git(str(repo))).commit != first


async def test_dirty_not_allowed(repo):
    (repo / "agent.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(GitDirty) as ei:
        await capture_git(str(repo), allow_dirty=False)
    assert ei.value.code == "GIT_DIRTY" and ei.value.status == 400
    assert any("agent.py" in line for line in ei.value.details["changed_files"])


async def test_untracked_file_counts_as_dirty(repo):
    (repo / "new.py").write_text("x", encoding="utf-8")
    with pytest.raises(GitDirty):
        await capture_git(str(repo))
    (repo / "new.py").unlink()
    (repo / "ignored.txt").write_text("ignored", encoding="utf-8")  # 被 .gitignore 忽略的不算
    assert (await capture_git(str(repo))).dirty is False


async def test_dirty_allowed_stores_patch_blob(repo, ctx):
    (repo / "agent.py").write_text("print('changed')\n", encoding="utf-8")
    (repo / "new.py").write_text("brand new\n", encoding="utf-8")  # 未跟踪文件也进入补丁
    async with ctx.db.write() as s:
        info = await capture_git(str(repo), allow_dirty=True, blobs=ctx.blobs, session=s)
    assert info.dirty is True
    assert info.patch_blob == info.patch_hash and len(info.patch_hash) == 64
    patch = await ctx.blobs.get_bytes(info.patch_blob)
    assert hashlib.sha256(patch).hexdigest() == info.patch_hash
    text = patch.decode()
    assert "-print('v1')" in text and "+print('changed')" in text and "+brand new" in text
    assert info.spec_pin()["patch_hash"] == info.patch_hash
    assert info.source_column()["patch_blob"] == info.patch_hash and info.source_column()["dirty"] is True
    # 不修改用户仓库的暂存区
    assert run(repo, "diff", "--cached", "--name-only").strip() == ""


async def test_patch_hash_tracks_content(repo):
    (repo / "agent.py").write_text("print('a')\n", encoding="utf-8")
    h1 = (await capture_git(str(repo), allow_dirty=True)).patch_hash
    h1_again = (await capture_git(str(repo), allow_dirty=True)).patch_hash
    (repo / "agent.py").write_text("print('b')\n", encoding="utf-8")
    h2 = (await capture_git(str(repo), allow_dirty=True)).patch_hash
    assert h1 == h1_again and h1 != h2


async def test_not_a_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    with pytest.raises(SpecInvalid) as ei:
        await capture_git(str(d))
    assert "capture_git" in ei.value.message


async def test_missing_directory(tmp_path):
    with pytest.raises(SpecInvalid):
        await capture_git(str(tmp_path / "nope"))


async def test_repo_without_commits(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    run(d, "init", "-q")
    with pytest.raises(SpecInvalid) as ei:
        await capture_git(str(d))
    assert "提交" in ei.value.message
