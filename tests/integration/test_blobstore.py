import asyncio
import hashlib
import os

import pytest
from evalp.errors import NotFound
from evalp.store import blobstore as bs
from evalp.store.models import Blob
from sqlalchemy import func, select


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def test_put_get_path_layout(ctx):
    data = "你好，世界".encode()
    h = await ctx.blobs.put_bytes(data)
    assert h == sha(data)
    p = ctx.blobs.path(h)
    assert p == ctx.config.blobs_dir / h[:2] / h[2:]
    assert p.read_bytes() == data
    assert ctx.blobs.exists(h)
    assert await ctx.blobs.get_bytes(h) == data
    with ctx.blobs.open(h) as f:
        assert f.read() == data
    async with ctx.db.read() as s:
        blob = await s.get(Blob, h)
    assert blob.size == len(data) and blob.refcount == 0


async def test_dedup(ctx):
    h1 = await ctx.blobs.put_bytes(b"same")
    mtime = ctx.blobs.path(h1).stat().st_mtime_ns
    h2 = await ctx.blobs.put_bytes(b"same")
    assert h1 == h2
    assert ctx.blobs.path(h1).stat().st_mtime_ns == mtime  # 未重写
    files = [p for p in ctx.config.blobs_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    async with ctx.db.read() as s:
        assert (await s.execute(select(func.count()).select_from(Blob))).scalar() == 1


async def test_put_file_streams_and_dedups(ctx, tmp_path):
    src = tmp_path / "big.bin"
    payload = os.urandom(3 * 1024 * 1024 + 17)
    src.write_bytes(payload)
    h = await ctx.blobs.put_file(src)
    assert h == sha(payload)
    assert ctx.blobs.path(h).read_bytes() == payload
    assert await ctx.blobs.put_file(src) == h
    assert await ctx.blobs.put_bytes(payload) == h
    assert not list((ctx.config.blobs_dir / bs.TMP_DIRNAME).iterdir())  # 无临时文件残留


async def test_atomic_write_failure_leaves_nothing(ctx, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk error")

    monkeypatch.setattr(bs.os, "replace", boom)
    with pytest.raises(OSError):
        await ctx.blobs.put_bytes(b"partial")
    monkeypatch.undo()
    h = sha(b"partial")
    assert not ctx.blobs.exists(h)
    tmp_dir = ctx.config.blobs_dir / bs.TMP_DIRNAME
    assert not tmp_dir.exists() or not list(tmp_dir.iterdir())
    async with ctx.db.read() as s:
        assert await s.get(Blob, h) is None
    # 失败后可正常重试
    assert await ctx.blobs.put_bytes(b"partial") == h
    assert ctx.blobs.exists(h)


async def test_concurrent_puts_same_content(ctx):
    hs = await asyncio.gather(*(ctx.blobs.put_bytes(b"x" * 1000) for _ in range(10)))
    assert len(set(hs)) == 1
    assert ctx.blobs.path(hs[0]).read_bytes() == b"x" * 1000


async def test_refcount(ctx):
    h = await ctx.blobs.put_bytes(b"data")
    await ctx.blobs.incref(h)
    await ctx.blobs.incref(h, 2)
    assert await ctx.blobs.refcount(h) == 3
    await ctx.blobs.decref(h)
    assert await ctx.blobs.refcount(h) == 2
    await ctx.blobs.decref(h, 10)  # 不会为负
    assert await ctx.blobs.refcount(h) == 0
    with pytest.raises(NotFound):
        await ctx.blobs.incref("0" * 64)


async def test_refcount_within_caller_transaction(ctx):
    async with ctx.db.write() as s:
        h = await ctx.blobs.put_bytes(b"in-tx", session=s)
        await ctx.blobs.incref(h, session=s)
    assert await ctx.blobs.refcount(h) == 1
    with pytest.raises(RuntimeError):
        async with ctx.db.write() as s:
            await ctx.blobs.incref(h, session=s)
            raise RuntimeError
    assert await ctx.blobs.refcount(h) == 1  # 回滚


async def test_missing_and_invalid(ctx):
    with pytest.raises(NotFound):
        await ctx.blobs.get_bytes("a" * 64)
    with pytest.raises(NotFound):
        ctx.blobs.open("a" * 64)
    assert not ctx.blobs.exists("a" * 64)
    with pytest.raises(ValueError):
        ctx.blobs.path("../etc/passwd")
