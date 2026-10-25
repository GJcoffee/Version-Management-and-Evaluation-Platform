"""垃圾回收（详设 3.4）。

规则：
1. 已软删除（assets.deleted_at 非空）的资产，其修订若没有被任何版本（version_asset_refs）或评测运行
   （eval_runs.dataset_rev_id）引用，则连同解析出的用例（eval_cases）一并删除，并释放对 blob 的引用；
   修订清空后的资产记录也一并删除；
2. 删除 refcount=0 且创建时间超过 24 小时的 blob（先删库记录、再删文件）；
3. 清理没有对应 blobs 记录且超过 24 小时的孤儿文件，以及遗留的临时文件；
4. ``eval_sessions_days`` > 0（设置 ``retention.eval_sessions_days``）时，删除超过保留期的评测 / 评审会话
   及其轮次、事件（释放事件大 payload 的 blob 引用）。评测汇总指标保留，Item 的 ``session_id`` 置空；
   仍有未结束 Item 在用的会话不删。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update

from evalp.logging import get_logger
from evalp.store.blobstore import TMP_DIRNAME, BlobStore
from evalp.store.db import Database
from evalp.store.models import (
    Asset,
    AssetRevision,
    Blob,
    EvalCase,
    EvalItem,
    EvalRun,
    EvalScore,
    Event,
    Session,
    Turn,
    VersionAssetRef,
)
from evalp.utils.time import iso_ago

log = get_logger(__name__)

GRACE_HOURS = 24
SESSION_BATCH = 50


@dataclass
class GCResult:
    blobs_deleted: int = 0
    bytes_freed: int = 0
    revisions_deleted: int = 0
    assets_deleted: int = 0
    eval_sessions_deleted: int = 0


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


async def _purge_eval_sessions(db: Database, blobs: BlobStore, result: GCResult, days: int) -> None:
    """删除超过保留期的评测 / 评审会话（连同轮次与事件），评测的汇总指标不受影响。"""
    cutoff = iso_ago(hours=days * 24)
    while True:
        async with db.write() as s:
            active = select(EvalItem.session_id).where(
                EvalItem.session_id.is_not(None), EvalItem.status.in_(("pending", "running", "scoring"))
            )
            ids = (
                (
                    await s.execute(
                        select(Session.id)
                        .where(
                            Session.mode.in_(("eval", "judge")),
                            Session.updated_at < cutoff,
                            Session.status != "running",
                            Session.id.not_in(active),
                        )
                        .order_by(Session.updated_at)
                        .limit(SESSION_BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                return
            # 解除引用：Item / 评分 / 其他会话（重放来源）/ 其他轮次（锚点）
            await s.execute(update(EvalItem).where(EvalItem.session_id.in_(ids)).values(session_id=None))
            await s.execute(
                update(EvalScore).where(EvalScore.judge_session_id.in_(ids)).values(judge_session_id=None)
            )
            await s.execute(
                update(Session).where(Session.source_session_id.in_(ids)).values(source_session_id=None)
            )
            turn_ids = select(Turn.id).where(Turn.session_id.in_(ids))
            await s.execute(
                update(Turn)
                .where(Turn.anchored_from_turn_id.in_(turn_ids))
                .values(anchored_from_turn_id=None)
            )
            # 释放事件里大 payload 的 blob 引用（每个事件登记时 incref 一次）
            payloads = (await s.execute(select(Event.payload).where(Event.session_id.in_(ids)))).scalars()
            hashes: dict[str, int] = {}
            for payload in payloads:
                if isinstance(payload, dict) and isinstance(payload.get("$blob"), str):
                    hashes[payload["$blob"]] = hashes.get(payload["$blob"], 0) + 1
            for h, n in hashes.items():
                try:
                    await blobs.decref(h, n, session=s)
                except Exception:  # noqa: BLE001 - blob 记录已不存在时忽略
                    log.warning("gc.eval_session_decref_failed", blob=h)
            await s.execute(delete(Event).where(Event.session_id.in_(ids)))
            await s.execute(delete(Turn).where(Turn.session_id.in_(ids)))
            await s.execute(delete(Session).where(Session.id.in_(ids)))
            result.eval_sessions_deleted += len(ids)


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


async def run_gc(
    db: Database,
    blobs: BlobStore,
    *,
    grace_hours: float = GRACE_HOURS,
    eval_sessions_days: int = 0,
) -> GCResult:
    """执行一次 GC，返回统计。``eval_sessions_days`` > 0 时同时清理过期的评测 / 评审会话。"""
    result = GCResult()
    if eval_sessions_days > 0:
        await _purge_eval_sessions(db, blobs, result, eval_sessions_days)
    await _purge_deleted_assets(db, blobs, result)
    await _purge_unreferenced_blobs(db, blobs, result, grace_hours)
    await _purge_orphan_files(db, blobs, result, grace_hours)
    log.info(
        "gc.done",
        blobs_deleted=result.blobs_deleted,
        bytes_freed=result.bytes_freed,
        revisions_deleted=result.revisions_deleted,
        eval_sessions_deleted=result.eval_sessions_deleted,
    )
    return result
