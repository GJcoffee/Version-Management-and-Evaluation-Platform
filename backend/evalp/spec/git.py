"""代码型 agent 的 git 信息采集（详设 4.2、4.4 第 4 步）。

- 采集 commit、branch、dirty；
- dirty 且 ``allow_dirty`` 时，把补丁（包含未跟踪文件的 ``git diff HEAD``）存为 blob，补丁哈希参与 spec_hash；
- dirty 且不允许时抛 ``GitDirty``。

补丁通过临时索引文件生成（``GIT_INDEX_FILE``），不会修改用户仓库的暂存区。
``repo`` 为相对路径时相对于服务进程的当前目录；客户端（CLI / MCP）应传绝对路径。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evalp.errors import GitDirty, SpecInvalid
from evalp.store.blobstore import BlobStore
from evalp.utils.canonical import sha256_hex
from sqlalchemy.ext.asyncio import AsyncSession

GIT_TIMEOUT_S = 30
MAX_REPORTED_FILES = 20


@dataclass
class GitInfo:
    """采集结果。patch_blob / patch_hash 只在 dirty 且允许 dirty 时有值（二者相等，都是补丁的 sha256）。"""

    commit: str
    branch: str | None
    dirty: bool
    patch_hash: str | None = None
    patch_blob: str | None = None

    def spec_pin(self) -> dict[str, str | None]:
        """写入 Spec ``source.git``（参与哈希）的部分：commit 与补丁哈希。"""
        return {"commit": self.commit, "patch_hash": self.patch_hash}

    def source_column(self) -> dict[str, object]:
        """写入 ``versions.source`` 的内容。"""
        return {
            "git_commit": self.commit,
            "git_branch": self.branch,
            "dirty": self.dirty,
            "patch_blob": self.patch_blob,
        }


async def _git(cwd: str | Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise SpecInvalid("服务器上找不到 git 命令，无法采集代码型 agent 的版本信息") from None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise SpecInvalid(f"git {args[0]} 超时（>{GIT_TIMEOUT_S}s）") from None
    if proc.returncode != 0:
        msg = err.decode("utf-8", "replace").strip().splitlines()
        raise SpecInvalid(f"git {args[0]} 失败: {msg[-1] if msg else proc.returncode}")
    return out


async def _make_patch(top: str) -> bytes:
    """用临时索引生成相对 HEAD 的补丁（含未跟踪文件，遵守 .gitignore）。"""
    with tempfile.TemporaryDirectory(prefix="evalp-gitidx-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        await _git(top, "read-tree", "HEAD", env=env)
        await _git(top, "add", "-A", env=env)
        return await _git(top, "diff", "--cached", "--binary", "--no-color", "--no-ext-diff", "HEAD", env=env)


async def capture_git(
    repo: str,
    *,
    allow_dirty: bool = False,
    blobs: BlobStore | None = None,
    session: AsyncSession | None = None,
) -> GitInfo:
    """采集 git 信息。

    blobs 不为 None 且需要保存补丁时，把补丁写入 BlobStore（引用计数为 0，调用方在真正创建版本时 incref）。
    """
    path = Path(repo)
    if not path.is_dir():
        raise SpecInvalid(f"source.repo 目录不存在: {repo}", {"repo": repo})
    try:
        top = (await _git(path, "rev-parse", "--show-toplevel")).decode("utf-8").strip()
    except SpecInvalid as e:
        raise SpecInvalid(
            f"source.repo 不是 git 仓库: {repo}（如不需要采集，请设置 source.capture_git: false）",
            {"repo": repo, "reason": e.message},
        ) from None
    try:
        commit = (await _git(top, "rev-parse", "HEAD")).decode().strip()
    except SpecInvalid:
        raise SpecInvalid(f"仓库还没有任何提交: {repo}", {"repo": repo}) from None
    branch_raw = (await _git(top, "rev-parse", "--abbrev-ref", "HEAD")).decode().strip()
    branch = None if branch_raw == "HEAD" else branch_raw
    status = (await _git(top, "status", "--porcelain=v1", "--untracked-files=normal")).decode(
        "utf-8", "replace"
    )
    changed = [ln for ln in status.splitlines() if ln.strip()]
    if not changed:
        return GitInfo(commit=commit, branch=branch, dirty=False)
    if not allow_dirty:
        raise GitDirty(
            f"工作区有未提交的改动（{len(changed)} 个文件），请先提交，或设置 source.allow_dirty: true",
            {"repo": repo, "changed_files": changed[:MAX_REPORTED_FILES], "total": len(changed)},
        )
    patch = await _make_patch(top)
    h = sha256_hex(patch)
    if blobs is not None:
        stored = await blobs.put_bytes(patch, session=session)
        assert stored == h
    return GitInfo(commit=commit, branch=branch, dirty=True, patch_hash=h, patch_blob=h if blobs else None)
