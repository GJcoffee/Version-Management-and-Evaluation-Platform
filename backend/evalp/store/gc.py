"""垃圾回收（详设 3.4）。

规则：
1. 已软删除（assets.deleted_at 非空）的资产，其修订若没有被任何版本（version_asset_refs）或评测运行
   （eval_runs.dataset_rev_id）引用，则连同解析出的用例（eval_cases）一并删除，并释放对 blob 的引用；
   修订清空后的资产记录也一并删除；
2. 删除 refcount=0 且创建时间超过 24 小时的 blob（先删库记录、再删文件）；
3. 清理没有对应 blobs 记录且超过 24 小时的孤儿文件，以及遗留的临时文件。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select

from evalp.logging import get_logger
from evalp.store.blobstore import TMP_DIRNAME, BlobStore
from evalp.store.db import Database
from evalp.store.models import (
    Asset,
    AssetRevision,
    Blob,
    EvalCase,
    EvalRun,
    VersionAssetRef,
)
from evalp.utils.time import iso_ago

log = get_logger(__name__)

GRACE_HOURS = 24


@dataclass
class GCResult:
    blobs_deleted: int = 0
    bytes_freed: int = 0
    revisions_deleted: int = 0
    assets_deleted: int = 0


async def _purge_deleted_assets(db: Database, blobs: BlobStore, result: GCResult) -> None:
    async with db.write() as s:
        deleted_ids = (await s.execute(select(Asset.id).where(Asset.deleted_at.is_not(None)))).scalars().all()
        if not deleted_ids:
            return
        referenced_by_version = select(VersionAssetRef.asset_revision_id)
        referenced_by_run = select(EvalRun.dataset_rev_id)
        revs = (
            await s.execute(
                select(AssetRevision.id, AssetRevision.blob_hash, AssetRevision.asset_id).where(
                    AssetRevision.asset_id.in_(deleted_ids),
                    AssetRevision.id.not_in(referenced_by_version),
                    AssetRevision.id.not_in(referenced_by_run),
                )
            )
        ).all()
        for rev_id, blob_hash, _asset_id in revs:
            await s.execute(delete(EvalCase).where(EvalCase.dataset_rev_id == rev_id))
            await s.execute(delete(AssetRevision).where(AssetRevision.id == rev_id))
            await blobs.decref(blob_hash, session=s)
            result.revisions_deleted += 1
        # 修订清空的软删除资产一并删除
        for asset_id in deleted_ids:
            remaining = (
                await s.execute(
                    select(func.count()).select_from(AssetRevision).where(AssetRevision.asset_id == asset_id)
                )
            ).scalar_one()
            if remaining == 0:
                await s.execute(delete(Asset).where(Asset.id == asset_id))
                result.assets_deleted += 1


async def _purge_unreferenced_blobs(
    db: Database, blobs: BlobStore, result: GCResult, grace_hours: float
) -> None:
    cutoff = iso_ago(hours=grace_hours)
    async with db.write() as s:
        rows = (
            await s.execute(select(Blob.hash, Blob.size).where(Blob.refcount <= 0, Blob.created_at < cutoff))
        ).all()
        for h, _size in rows:
            await s.execute(delete(Blob).where(Blob.hash == h))
    # 记录删除成功（已提交）后再删文件
    for h, size in rows:
        freed = blobs.delete_file(h)
        result.blobs_deleted += 1
        result.bytes_freed += freed or size


async def _purge_orphan_files(db: Database, blobs: BlobStore, result: GCResult, grace_hours: float) -> None:
    """删除没有 blobs 记录的孤儿文件与过期临时文件（例如写入后事务回滚遗留的）。"""
    root: Path = blobs.root
    limit = time.time() - grace_hours * 3600
    async with db.read() as s:
        known = set((await s.execute(select(Blob.hash))).scalars().all())
    for sub in root.iterdir() if root.exists() else []:
        if not sub.is_dir():
            continue
        is_tmp = sub.name == TMP_DIRNAME
        for f in sub.iterdir():
            if not f.is_file():
                continue
            if not is_tmp and (sub.name + f.name) in known:
                continue
            try:
                st = f.stat()
                if st.st_mtime >= limit:
                    continue
                f.unlink()
            except OSError:
                continue
            result.blobs_deleted += 0 if is_tmp else 1
            result.bytes_freed += st.st_size
        if not is_tmp:
            try:
                sub.rmdir()
            except OSError:
                pass


async def run_gc(db: Database, blobs: BlobStore, *, grace_hours: float = GRACE_HOURS) -> GCResult:
    """执行一次 GC，返回统计。"""
    result = GCResult()
    await _purge_deleted_assets(db, blobs, result)
    await _purge_unreferenced_blobs(db, blobs, result, grace_hours)
    await _purge_orphan_files(db, blobs, result, grace_hours)
    log.info(
        "gc.done",
        blobs_deleted=result.blobs_deleted,
        bytes_freed=result.bytes_freed,
        revisions_deleted=result.revisions_deleted,
    )
    return result
