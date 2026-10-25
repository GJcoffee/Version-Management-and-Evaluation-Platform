import os
import time

from evalp.ids import new_id
from evalp.store import blobstore as bs
from evalp.store.gc import run_gc
from evalp.store.models import (
    Agent,
    Asset,
    AssetRevision,
    Blob,
    Draft,
    EvalCase,
    Version,
    VersionAssetRef,
)
from evalp.utils.time import iso_ago, now_iso
from sqlalchemy import select, update


async def age(ctx, h, hours=48):
    async with ctx.db.write() as s:
        await s.execute(update(Blob).where(Blob.hash == h).values(created_at=iso_ago(hours=hours)))


async def test_deletes_old_unreferenced_blob_only(ctx):
    old = await ctx.blobs.put_bytes(b"old-unref")
    young = await ctx.blobs.put_bytes(b"young-unref")
    referenced = await ctx.blobs.put_bytes(b"old-ref")
    await ctx.blobs.incref(referenced)
    await age(ctx, old)
    await age(ctx, referenced)
    res = await run_gc(ctx.db, ctx.blobs)
    assert (res.blobs_deleted, res.bytes_freed) == (1, len(b"old-unref"))
    assert not ctx.blobs.exists(old) and not ctx.blobs.path(old).parent.exists()  # 空目录被清理
    assert ctx.blobs.exists(young) and ctx.blobs.exists(referenced)
    async with ctx.db.read() as s:
        assert await s.get(Blob, old) is None
        assert await s.get(Blob, young) is not None


async def test_reput_refreshes_grace_period(ctx):
    h = await ctx.blobs.put_bytes(b"again")
    await age(ctx, h)
    await ctx.blobs.put_bytes(b"again")  # 重新写入同内容 -> 宽限期重新计算
    assert (await run_gc(ctx.db, ctx.blobs)).blobs_deleted == 0
    assert ctx.blobs.exists(h)


async def test_orphan_files_and_stale_tmp(ctx):
    # 孤儿文件：有文件、无记录（例如事务回滚遗留）
    h_old = "ab" + "0" * 62
    h_new = "cd" + "1" * 62
    for h in (h_old, h_new):
        p = ctx.blobs.path(h)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"orphan")
    stale_tmp = ctx.config.blobs_dir / bs.TMP_DIRNAME / "blob-stale"
    stale_tmp.parent.mkdir(parents=True, exist_ok=True)
    stale_tmp.write_bytes(b"tmp")
    long_ago = time.time() - 48 * 3600
    for p in (ctx.blobs.path(h_old), stale_tmp):
        os.utime(p, (long_ago, long_ago))
    res = await run_gc(ctx.db, ctx.blobs)
    assert not ctx.blobs.exists(h_old) and ctx.blobs.exists(h_new) and not stale_tmp.exists()
    assert res.blobs_deleted == 1 and res.bytes_freed == len(b"orphan") + len(b"tmp")


async def _make_asset(ctx, name, *, deleted, content=b"asset-body"):
    h = await ctx.blobs.put_bytes(content)
    now = now_iso()
    async with ctx.db.write() as s:
        asset = Asset(id=new_id("ast"), kind="dataset", name=name, latest_rev=1, created_at=now,
                      deleted_at=now if deleted else None)  # fmt: skip
        rev = AssetRevision(id=new_id("arv"), asset_id=asset.id, rev=1, blob_hash=h,
                            content_type="application/jsonl", size=len(content), created_at=now)  # fmt: skip
        s.add(asset)
        await s.flush()
        s.add(rev)
        await s.flush()
        s.add(EvalCase(id=new_id("cas"), dataset_rev_id=rev.id, case_key="c1", idx=0,
                       input={"turns": []}, case_hash="h"))  # fmt: skip
        await ctx.blobs.incref(h, session=s)
    await age(ctx, h)
    return asset.id, rev.id, h


async def test_soft_deleted_asset_revisions_are_purged(ctx):
    asset_id, rev_id, h = await _make_asset(ctx, "gone", deleted=True)
    live_asset, live_rev, live_h = await _make_asset(ctx, "live", deleted=False, content=b"live-body")
    res = await run_gc(ctx.db, ctx.blobs)
    assert res.revisions_deleted == 1 and res.assets_deleted == 1
    assert res.blobs_deleted == 1 and res.bytes_freed == len(b"asset-body")
    async with ctx.db.read() as s:
        assert await s.get(Asset, asset_id) is None
        assert await s.get(AssetRevision, rev_id) is None
        assert (await s.execute(select(EvalCase).where(EvalCase.dataset_rev_id == rev_id))).first() is None
        assert await s.get(Asset, live_asset) is not None
        assert await s.get(AssetRevision, live_rev) is not None
    assert not ctx.blobs.exists(h) and ctx.blobs.exists(live_h)


async def test_soft_deleted_asset_kept_when_referenced_by_version(ctx):
    asset_id, rev_id, h = await _make_asset(ctx, "used", deleted=True)
    now = now_iso()
    async with ctx.db.write() as s:
        agent = Agent(id=new_id("agt"), name="a", kind="config", created_at=now, updated_at=now)
        s.add(agent)
        await s.flush()
        ver = Version(id=new_id("ver"), agent_id=agent.id, seq=1, spec_hash="x", spec={}, created_via="api",
                      created_at=now)  # fmt: skip
        s.add(ver)
        await s.flush()
        s.add(VersionAssetRef(version_id=ver.id, asset_revision_id=rev_id, spec_path="prompts.system"))
        s.add(Draft(id=new_id("drf"), agent_id=agent.id, spec={}, base_version_id=ver.id, created_at=now,
                    updated_at=now))  # fmt: skip
    res = await run_gc(ctx.db, ctx.blobs)
    assert res.revisions_deleted == 0 and res.blobs_deleted == 0
    async with ctx.db.read() as s:
        assert await s.get(AssetRevision, rev_id) is not None
        assert await s.get(Asset, asset_id) is not None
    assert ctx.blobs.exists(h)
