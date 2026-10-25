"""内容寻址的 BlobStore（详设 3.4）。

- 路径：``{blobs_dir}/{hash[0:2]}/{hash[2:]}``；
- 写入：先算 sha256 → 已存在则直接返回 → 否则写临时文件、fsync、``os.replace`` 原子重命名；
- ``blobs`` 表登记大小与引用计数；incref/decref 供资产修订、事件大 payload 等使用。

所有会写库的方法都接受可选 ``session``：在调用方已持有 ``db.write()`` 事务时传入，避免死锁；
不传则自行开启一个写事务。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import IO

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.errors import NotFound
from evalp.store.db import Database
from evalp.store.models import Blob
from evalp.utils.time import now_iso

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024
TMP_DIRNAME = ".tmp"


def _check_hash(h: str) -> str:
    if not _HASH_RE.match(h):
        raise ValueError(f"非法的 blob 哈希: {h!r}")
    return h


def _fsync_dir(path: Path) -> None:
    """尽力 fsync 目录（Windows 不支持，忽略）。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class BlobStore:
    """按 sha256 内容寻址的文件存储 + blobs 表引用计数。"""

    def __init__(self, root: Path | str, db: Database) -> None:
        self.root = Path(root)
        self._db = db
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 路径 / 读取

    def path(self, hash_: str) -> Path:
        """blob 的文件路径（不保证存在）。"""
        h = _check_hash(hash_)
        return self.root / h[:2] / h[2:]

    def exists(self, hash_: str) -> bool:
        """blob 文件是否存在。"""
        return self.path(hash_).is_file()

    def open(self, hash_: str) -> IO[bytes]:
        """以二进制只读方式打开 blob；不存在抛 NotFound。"""
        try:
            return self.path(hash_).open("rb")
        except FileNotFoundError:
            raise NotFound(f"blob 不存在: {hash_}") from None

    async def get_bytes(self, hash_: str) -> bytes:
        """读取 blob 全部内容；不存在抛 NotFound。"""
        p = self.path(hash_)

        def _read() -> bytes:
            try:
                return p.read_bytes()
            except FileNotFoundError:
                raise NotFound(f"blob 不存在: {hash_}") from None

        return await asyncio.to_thread(_read)

    # ------------------------------------------------------------ 写入

    def _tmp_file(self) -> tuple[int, Path]:
        tmp_dir = self.root / TMP_DIRNAME
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=tmp_dir, prefix="blob-")
        return fd, Path(name)

    def _store_bytes_sync(self, data: bytes) -> tuple[str, int]:
        h = hashlib.sha256(data).hexdigest()
        dest = self.path(h)
        if dest.is_file():
            return h, len(data)
        fd, tmp = self._tmp_file()
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            _fsync_dir(dest.parent)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return h, len(data)

    def _store_file_sync(self, src: Path) -> tuple[str, int]:
        """流式计算哈希并复制到临时文件，再原子重命名。"""
        fd, tmp = self._tmp_file()
        sha = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
                while chunk := inp.read(_CHUNK):
                    sha.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            h = sha.hexdigest()
            dest = self.path(h)
            if dest.is_file():
                tmp.unlink(missing_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, dest)
                _fsync_dir(dest.parent)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return h, size

    async def put_bytes(self, data: bytes, *, session: AsyncSession | None = None) -> str:
        """写入字节内容，返回 sha256 哈希；相同内容自动去重。"""
        h, size = await asyncio.to_thread(self._store_bytes_sync, data)
        await self._register(h, size, session)
        return h

    async def put_file(self, path: Path | str, *, session: AsyncSession | None = None) -> str:
        """写入本地文件（流式处理大文件），返回 sha256 哈希。"""
        h, size = await asyncio.to_thread(self._store_file_sync, Path(path))
        await self._register(h, size, session)
        return h

    # ------------------------------------------------------------ blobs 表

    async def _register(self, h: str, size: int, session: AsyncSession | None) -> None:
        async def _do(s: AsyncSession) -> None:
            now = now_iso()
            stmt = sqlite_insert(Blob).values(hash=h, size=size, refcount=0, created_at=now)
            await s.execute(stmt.on_conflict_do_nothing(index_elements=["hash"]))
            # 已登记且无人引用的 blob 被再次写入：刷新创建时间，让 GC 的 24 小时宽限期重新计算
            await s.execute(update(Blob).where(Blob.hash == h, Blob.refcount == 0).values(created_at=now))

        if session is not None:
            await _do(session)
        else:
            async with self._db.write() as s:
                await _do(s)

    async def incref(self, hash_: str, n: int = 1, *, session: AsyncSession | None = None) -> None:
        """引用计数 +n（blob 必须已通过 put_* 登记）。"""
        await self._adjust(hash_, n, session)

    async def decref(self, hash_: str, n: int = 1, *, session: AsyncSession | None = None) -> None:
        """引用计数 -n（不会低于 0）。"""
        await self._adjust(hash_, -n, session)

    async def _adjust(self, hash_: str, delta: int, session: AsyncSession | None) -> None:
        _check_hash(hash_)

        async def _do(s: AsyncSession) -> None:
            new_val = Blob.refcount + delta
            if delta < 0:
                new_val = func.max(new_val, 0)  # 不低于 0
            stmt = update(Blob).where(Blob.hash == hash_).values(refcount=new_val)
            result = await s.execute(stmt)
            if result.rowcount == 0:  # type: ignore[attr-defined]
                raise NotFound(f"blob 未登记: {hash_}")

        if session is not None:
            await _do(session)
        else:
            async with self._db.write() as s:
                await _do(s)

    async def refcount(self, hash_: str) -> int | None:
        """查询引用计数；未登记返回 None。"""
        async with self._db.read() as s:
            return (await s.execute(select(Blob.refcount).where(Blob.hash == hash_))).scalar_one_or_none()

    # ------------------------------------------------------------ 删除（供 GC 使用）

    def delete_file(self, hash_: str) -> int:
        """删除 blob 文件并清理空目录，返回释放的字节数（文件不存在返回 0）。"""
        p = self.path(hash_)
        try:
            size = p.stat().st_size
            p.unlink()
        except FileNotFoundError:
            return 0
        try:
            p.parent.rmdir()
        except OSError:
            pass
        return size
