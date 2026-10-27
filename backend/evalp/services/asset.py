"""资产服务（详设 5）：资产与修订、内容去重、diff、引用查询、软删除 / 硬删除、大文件上传。

规则：

- 新建修订按内容去重（``UNIQUE(asset_id, blob_hash)``）：内容与已有修订相同时
  返回已有修订（``existed=True``）。
  如果相同的是更早的修订（例如改回旧内容），把"最新"指针 ``latest_rev`` 移回该修订（修订号只增不减，
  下一个新修订取 ``max(rev)+1``）；
- 新修订会对 blob ``incref``；
- 被版本（``version_asset_refs``）或评测（``eval_runs.dataset_rev_id``）引用的资产不能硬删除（ASSET_IN_USE）；
- 快照入库的资产带来源 ID（``source_id``）：按 ``(kind, source_id)`` 识别同一个资产，
  名字变了自动改名并保留修订历史（旧名记在 ``meta.former_names``）；
- agent 开启 ``snapshot_readonly`` 规范后，来自快照的资产拒绝快照推送之外的写入（``ensure_writable``）。

供其他模块使用的辅助方法：``resolve_ref`` / ``get_revision`` / ``read_bytes`` / ``read_text``。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, BinaryIO

from sqlalchemy import Text, cast, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import AssetInUse, Conflict, ConventionViolation, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.schemas.asset import (
    AssetCreate,
    AssetOut,
    AssetWriteOut,
    RevisionCreate,
    RevisionDetail,
    RevisionOut,
    UploadOut,
)
from evalp.services.asset_content import (
    MAX_SIZE,
    ValidatedContent,
    default_content_type,
    normalize_content_type,
    validate_content,
)
from evalp.services.conventions import conventions_of
from evalp.spec.diff import identity_paths, unified_content_diff
from evalp.spec.refs import AssetRef, parse_asset_ref, validate_asset_name
from evalp.spec.resolve import ResolvedAsset, lookup_revision
from evalp.store.models import (
    Agent,
    Asset,
    AssetRevision,
    Blob,
    Draft,
    EvalCase,
    EvalRun,
    Version,
    VersionAssetRef,
)
from evalp.utils.time import now_iso

log = get_logger(__name__)

CONTENT_INLINE_LIMIT = 5 * 1024 * 1024
UPLOAD_LIMIT = MAX_SIZE["file"]
_CHUNK = 1024 * 1024


def revision_out(r: AssetRevision) -> RevisionOut:
    """ORM -> DTO。"""
    return RevisionOut(
        id=r.id,
        asset_id=r.asset_id,
        rev=r.rev,
        blob_hash=r.blob_hash,
        content_type=r.content_type,
        size=r.size,
        meta=r.meta or {},
        message=r.message,
        created_at=r.created_at,
    )


def asset_out(a: Asset, latest: AssetRevision | None = None) -> AssetOut:
    """ORM -> DTO（latest 为 ``latest_rev`` 对应的修订）。"""
    return AssetOut(
        id=a.id,
        kind=a.kind,
        name=a.name,
        display_name=a.display_name,
        source_id=a.source_id,
        former_names=list(a.meta.get("former_names", [])),
        description=a.description,
        latest_rev=a.latest_rev,
        deleted_at=a.deleted_at,
        created_at=a.created_at,
        latest=revision_out(latest) if latest is not None else None,
    )


@dataclass
class _Payload:
    """待写入的内容：data 为 None 表示 blob 已在库中（大文件上传）。"""

    hash: str
    size: int
    data: bytes | None = None


class AssetService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------ 查询

    @staticmethod
    async def _get_asset(s: AsyncSession, asset_id: str) -> Asset:
        a = await s.get(Asset, asset_id)
        if a is None:
            raise NotFound(f"资产不存在: {asset_id}", {"id": asset_id})
        return a

    @staticmethod
    async def _latest_of(s: AsyncSession, a: Asset) -> AssetRevision | None:
        return (
            await s.execute(
                select(AssetRevision).where(AssetRevision.asset_id == a.id, AssetRevision.rev == a.latest_rev)
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        kind: str | None = None,
        q: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[AssetOut]:
        """分页列出资产（最新创建在前）；默认不含已软删除的。"""
        stmt = select(Asset)
        if kind:
            stmt = stmt.where(Asset.kind == kind)
        if not include_deleted:
            stmt = stmt.where(Asset.deleted_at.is_(None))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(Asset.name.like(like), Asset.display_name.like(like), Asset.description.like(like))
            )
        async with self.ctx.db.read() as s:
            page = await paginate(s, stmt, Asset.id, limit=limit, cursor=cursor)
            assets: list[Asset] = page.items
            latest: dict[tuple[str, int], AssetRevision] = {}
            if assets:
                rows = (
                    await s.execute(
                        select(AssetRevision).where(AssetRevision.asset_id.in_([a.id for a in assets]))
                    )
                ).scalars()
                latest = {(r.asset_id, r.rev): r for r in rows}
            items = [asset_out(a, latest.get((a.id, a.latest_rev))) for a in assets]
        return Page[AssetOut](items=items, next_cursor=page.next_cursor)

    async def get(self, asset_id: str) -> AssetOut:
        """按 id 获取资产（含最新修订摘要，已软删除的也可获取）。"""
        async with self.ctx.db.read() as s:
            a = await self._get_asset(s, asset_id)
            return asset_out(a, await self._latest_of(s, a))

    async def find(self, kind: str, name: str, *, include_deleted: bool = False) -> AssetOut | None:
        """按 (kind, name) 查找；不存在返回 None。"""
        async with self.ctx.db.read() as s:
            a = (
                await s.execute(select(Asset).where(Asset.kind == kind, Asset.name == name))
            ).scalar_one_or_none()
            if a is None or (a.deleted_at is not None and not include_deleted):
                return None
            return asset_out(a, await self._latest_of(s, a))

    async def list_revisions(
        self, asset_id: str, *, limit: int = 50, cursor: str | None = None
    ) -> Page[RevisionOut]:
        """分页列出修订（新修订在前）。"""
        async with self.ctx.db.read() as s:
            await self._get_asset(s, asset_id)
            stmt = select(AssetRevision).where(AssetRevision.asset_id == asset_id)
            return await paginate(
                s, stmt, AssetRevision.id, limit=limit, cursor=cursor, transform=revision_out
            )

    async def get_revision(self, revision_id: str, *, session: AsyncSession | None = None) -> RevisionOut:
        """按修订 id 获取修订。"""
        if session is not None:
            return revision_out(await self._revision_orm(session, revision_id))
        async with self.ctx.db.read() as s:
            return revision_out(await self._revision_orm(s, revision_id))

    @staticmethod
    async def _revision_orm(s: AsyncSession, revision_id: str) -> AssetRevision:
        r = await s.get(AssetRevision, revision_id)
        if r is None:
            raise NotFound(f"资产修订不存在: {revision_id}", {"id": revision_id})
        return r

    async def get_revision_by_rev(self, asset_id: str, rev: int | str) -> RevisionOut:
        """按资产 id + 修订号（或 ``latest``）获取修订。"""
        async with self.ctx.db.read() as s:
            return revision_out(await self._rev_orm(s, asset_id, rev))

    async def _rev_orm(self, s: AsyncSession, asset_id: str, rev: int | str) -> AssetRevision:
        a = await self._get_asset(s, asset_id)
        number = a.latest_rev if rev == "latest" else int(rev)
        r = (
            await s.execute(
                select(AssetRevision).where(AssetRevision.asset_id == asset_id, AssetRevision.rev == number)
            )
        ).scalar_one_or_none()
        if r is None:
            raise NotFound(f"资产修订不存在: {a.kind}/{a.name}@{rev}", {"asset_id": asset_id, "rev": rev})
        return r

    async def revision_detail(self, *, revision_id: str | None = None, asset_id: str | None = None,
                              rev: int | str | None = None) -> RevisionDetail:  # fmt: skip
        """带内容的修订（用 revision_id，或 asset_id + rev）。"""
        async with self.ctx.db.read() as s:
            r = (
                await self._revision_orm(s, revision_id)
                if revision_id
                else await self._rev_orm(s, asset_id or "", rev if rev is not None else "latest")
            )
            a = await self._get_asset(s, r.asset_id)
            base = revision_out(r)
            kind, name = a.kind, a.name
        content: str | None = None
        if base.size <= CONTENT_INLINE_LIMIT:
            try:
                content = (await self.ctx.blobs.get_bytes(base.blob_hash)).decode("utf-8")
            except UnicodeDecodeError:
                content = None
        return RevisionDetail(
            **base.model_dump(), kind=kind, name=name, content=content, content_available=content is not None
        )

    # ------------------------------------------------------------ 供其他模块使用

    async def resolve_ref(self, ref: str | AssetRef, *, session: AsyncSession | None = None) -> ResolvedAsset:
        """解析 ``asset://kind/name@latest|rev`` 为具体修订；不存在或已删除抛 SpecInvalid。"""
        parsed = parse_asset_ref(ref) if isinstance(ref, str) else ref
        if session is not None:
            asset, revision = await lookup_revision(session, parsed)
            return ResolvedAsset.of(asset, revision)
        async with self.ctx.db.read() as s:
            asset, revision = await lookup_revision(s, parsed)
            return ResolvedAsset.of(asset, revision)

    async def read_bytes(self, pin: dict[str, Any] | ResolvedAsset) -> bytes:
        """按固化引用（``{"kind","name","rev","hash"}`` 或其外层 ``{"$asset": …}``）读取资产内容。"""
        h = pin.hash if isinstance(pin, ResolvedAsset) else (pin.get("$asset") or pin)["hash"]
        return await self.ctx.blobs.get_bytes(h)

    async def read_text(self, pin: dict[str, Any] | ResolvedAsset) -> str:
        """同 read_bytes，按 UTF-8 解码。"""
        return (await self.read_bytes(pin)).decode("utf-8")

    async def read_revision_bytes(self, revision_id: str) -> bytes:
        """按修订 id 读取内容。"""
        rev = await self.get_revision(revision_id)
        return await self.ctx.blobs.get_bytes(rev.blob_hash)

    # ------------------------------------------------------------ 写入

    async def _payload_from(
        self, kind: str, content: str | None, blob_upload_id: str | None
    ) -> tuple[_Payload, bytes | None]:
        """返回 (payload, 用于校验的字节)；file 类上传不需要读入内存，校验字节为 None。"""
        if blob_upload_id is not None:
            async with self.ctx.db.read() as s:
                blob = await s.get(Blob, blob_upload_id)
            if blob is None or not self.ctx.blobs.exists(blob_upload_id):
                raise NotFound(
                    f"上传的文件不存在或已过期: {blob_upload_id}", {"blob_upload_id": blob_upload_id}
                )
            limit = MAX_SIZE[kind]
            if blob.size > limit:
                raise ValidationFailed(
                    f"{kind} 资产不能超过 {limit // (1024 * 1024)}MB",
                    {
                        "errors": [{"path": "blob_upload_id", "message": "内容过大"}],
                        "limit": limit,
                        "size": blob.size,
                    },
                )
            payload = _Payload(hash=blob_upload_id, size=blob.size)
            if kind == "file":
                return payload, None
            data = await self.ctx.blobs.get_bytes(blob_upload_id)
            return payload, data
        data = (content or "").encode("utf-8")
        return _Payload(hash=hashlib.sha256(data).hexdigest(), size=len(data), data=data), data

    async def validate(
        self,
        kind: str,
        data: bytes | None,
        content_type: str | None,
        column_map: dict[str, Any] | None = None,
    ) -> ValidatedContent:
        """在线程里校验内容（CPU 密集的数据集解析不阻塞事件循环）。"""
        if data is None:  # file 类大文件上传
            return ValidatedContent(normalize_content_type(content_type) or default_content_type("file", b""))
        return await asyncio.to_thread(validate_content, kind, data, content_type, column_map=column_map)

    async def create(self, body: AssetCreate) -> AssetWriteOut:
        """创建资产（含首个修订）；资产已存在时等价于新增修订，已软删除的同名资产会被恢复。"""
        payload, data = await self._payload_from(body.kind, body.content, body.blob_upload_id)
        validated = await self.validate(body.kind, data, body.content_type, body.column_map)
        async with self.ctx.db.write() as s:
            asset, created = await self.get_or_create_asset(s, body.kind, body.name, body.description)
            if not created:
                await self.ensure_writable(s, asset)
            revision, existed = await self.add_revision(s, asset, payload, validated, body.message)
            return AssetWriteOut(
                asset=asset_out(asset, await self._latest_of(s, asset)),
                revision=revision_out(revision),
                existed=existed,
                created=created,
            )

    async def add_revision_to(self, asset_id: str, body: RevisionCreate) -> AssetWriteOut:
        """为已有资产新增修订（``POST /assets/{id}/revisions``）。"""
        async with self.ctx.db.read() as s:
            kind = (await self._get_asset(s, asset_id)).kind
        payload, data = await self._payload_from(kind, body.content, body.blob_upload_id)
        validated = await self.validate(kind, data, body.content_type, body.column_map)
        async with self.ctx.db.write() as s:
            asset = await self._get_asset(s, asset_id)
            if asset.deleted_at is not None:
                raise Conflict("资产已被删除，请先恢复", code="ASSET_DELETED")
            await self.ensure_writable(s, asset)
            revision, existed = await self.add_revision(s, asset, payload, validated, body.message)
            return AssetWriteOut(
                asset=asset_out(asset, await self._latest_of(s, asset)),
                revision=revision_out(revision),
                existed=existed,
                created=False,
            )

    async def get_or_create_asset(
        self, s: AsyncSession, kind: str, name: str, description: str | None = None
    ) -> tuple[Asset, bool]:
        """在事务内取得（或创建 / 恢复）资产，返回 (asset, 是否新建)。"""
        validate_asset_name(name)
        asset = (
            await s.execute(select(Asset).where(Asset.kind == kind, Asset.name == name))
        ).scalar_one_or_none()
        if asset is not None:
            if asset.deleted_at is not None:
                asset.deleted_at = None  # 同名的软删除资产：恢复
            if description is not None:
                asset.description = description
            return asset, False
        asset = Asset(
            id=new_id("ast"),
            kind=kind,
            name=name,
            description=description,
            latest_rev=0,
            created_at=now_iso(),
        )
        s.add(asset)
        await s.flush()
        return asset, True

    async def get_or_create_by_source(
        self, s: AsyncSession, kind: str, name: str, source_id: str, display_name: str | None
    ) -> tuple[Asset, bool, str | None]:
        """快照入库：按 ``(kind, source_id)`` 取得（或创建 / 恢复）资产，返回 ``(asset, 是否新建, 旧名)``。

        找到资产但名字不同：自动改名，修订历史原样保留，旧名追加到 ``meta.former_names``（旧名返回给调用方）。
        没有该 source_id 的资产时，认领同名且还没有来源 ID 的资产（升级前入库的），否则新建。
        新名字被别的资产占用时抛 Conflict（不会合并两个资产）。
        """
        validate_asset_name(name)
        asset = (
            await s.execute(select(Asset).where(Asset.kind == kind, Asset.source_id == source_id))
        ).scalar_one_or_none()
        same_name = (
            await s.execute(select(Asset).where(Asset.kind == kind, Asset.name == name))
        ).scalar_one_or_none()
        renamed_from: str | None = None
        created = False
        if asset is None:
            if same_name is not None and same_name.source_id is not None:
                raise Conflict(
                    f"资产名 {kind}/{name} 已被来源 ID 为 {same_name.source_id!r} 的另一个资产占用"
                    f"（本文件的来源 ID 是 {source_id!r}）：请调整 snapshot.map 的 name，"
                    "让两个资产的名字不同。",
                    {
                        "asset": f"{kind}/{name}",
                        "source_id": source_id,
                        "other_source_id": same_name.source_id,
                    },
                    code="ASSET_NAME_CONFLICT",
                )
            asset, created = await self.get_or_create_asset(s, kind, name)
            asset.source_id = source_id
        else:
            asset.deleted_at = None
            if asset.name != name:
                if same_name is not None:
                    raise Conflict(
                        f"资产 {kind}/{asset.name}（来源 ID {source_id!r}）改名为 {name} 时，"
                        "该名字已被另一个资产占用：请调整 snapshot.map 的 name，让两个资产的名字不同。",
                        {"asset": f"{kind}/{name}", "source_id": source_id},
                        code="ASSET_NAME_CONFLICT",
                    )
                renamed_from = asset.name
                former = [n for n in asset.meta.get("former_names", []) if n != name]
                asset.meta = {**asset.meta, "former_names": [*former, renamed_from]}
                asset.name = name
        asset.display_name = display_name
        await s.flush()
        return asset, created, renamed_from

    async def add_revision(
        self,
        s: AsyncSession,
        asset: Asset,
        payload: _Payload,
        validated: ValidatedContent,
        message: str | None = None,
    ) -> tuple[AssetRevision, bool]:
        """在事务内新增修订（内容去重），返回 (修订, existed)。"""
        existing = (
            await s.execute(
                select(AssetRevision).where(
                    AssetRevision.asset_id == asset.id, AssetRevision.blob_hash == payload.hash
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if asset.latest_rev != existing.rev:
                asset.latest_rev = existing.rev  # 改回旧内容：把"最新"指回该修订
            return existing, True
        if payload.data is not None:
            await self.ctx.blobs.put_bytes(payload.data, session=s)
        elif (await s.get(Blob, payload.hash)) is None:
            raise NotFound(f"blob 未登记: {payload.hash}")
        await self.ctx.blobs.incref(payload.hash, session=s)
        max_rev = (
            await s.execute(select(func.max(AssetRevision.rev)).where(AssetRevision.asset_id == asset.id))
        ).scalar_one()
        revision = AssetRevision(
            id=new_id("arv"),
            asset_id=asset.id,
            rev=(max_rev or 0) + 1,
            blob_hash=payload.hash,
            content_type=validated.content_type,
            size=payload.size,
            meta=validated.meta,
            message=message,
            created_at=now_iso(),
        )
        s.add(revision)
        await s.flush()
        asset.latest_rev = revision.rev
        if validated.cases:
            rows = [
                {
                    "id": new_id("cas"),
                    "dataset_rev_id": revision.id,
                    "case_key": c.case_key,
                    "idx": i,
                    "input": c.input,
                    "expected": c.expected,
                    "rubric": c.rubric,
                    "tags": c.tags,
                    "metadata_": c.metadata,
                    "case_hash": c.case_hash,
                }
                for i, c in enumerate(validated.cases)
            ]
            await s.execute(insert(EvalCase), rows)
        return revision, False

    async def add_revision_bytes(
        self,
        s: AsyncSession,
        asset: Asset,
        data: bytes,
        content_type: str | None = None,
        message: str | None = None,
        column_map: dict[str, Any] | None = None,
        meta_extra: dict[str, Any] | None = None,
    ) -> tuple[AssetRevision, bool]:
        """事务内：校验并新增修订（供工作区同步使用）；``meta_extra`` 并入修订的 meta（如 ``origin``）。"""
        validated = await self.validate(asset.kind, data, content_type, column_map)
        validated.meta = {**validated.meta, **(meta_extra or {})}
        payload = _Payload(hash=hashlib.sha256(data).hexdigest(), size=len(data), data=data)
        return await self.add_revision(s, asset, payload, validated, message)

    async def add_revision_from_blob(
        self, s: AsyncSession, asset: Asset, blob_hash: str, content_type: str | None = None,
        message: str | None = None, meta_extra: dict[str, Any] | None = None,
    ) -> tuple[AssetRevision, bool]:  # fmt: skip
        """事务内：用库中已有的 blob 新增修订（客户端只给了哈希）。"""
        blob = await s.get(Blob, blob_hash)
        if blob is None or not self.ctx.blobs.exists(blob_hash):
            raise NotFound(f"blob 不存在: {blob_hash}")
        data = None if asset.kind == "file" else await self.ctx.blobs.get_bytes(blob_hash)
        validated = await self.validate(asset.kind, data, content_type, None)
        validated.meta = {**validated.meta, **(meta_extra or {})}
        return await self.add_revision(s, asset, _Payload(hash=blob_hash, size=blob.size), validated, message)

    async def update(self, asset_id: str, description: str | None) -> AssetOut:
        """修改资产描述。"""
        async with self.ctx.db.write() as s:
            a = await self._get_asset(s, asset_id)
            await self.ensure_writable(s, a)
            a.description = description
            return asset_out(a, await self._latest_of(s, a))

    async def ensure_writable(self, s: AsyncSession, asset: Asset) -> None:
        """``snapshot_readonly`` 规范：来自快照（最新修订 ``origin=snapshot``）的资产，被开启了该规范的
        agent 引用（版本或草稿）时，快照推送之外的写入（新修订 / upsert / 改描述 / 删除）一律拒绝。
        快照推送本身不调用它。"""
        latest = await self._latest_of(s, asset)
        if latest is None or latest.meta.get("origin") != "snapshot":
            return
        agents = [a for a in await self._referencing_agents(s, asset) if conventions_of(a).snapshot_readonly]
        if not agents:
            return
        key = f"{asset.kind}/{asset.name}"
        names = "、".join(f"「{a.name}」" for a in agents)
        raise ConventionViolation(
            f"资产 {key} 来自 agent 快照（origin=snapshot），agent {names}开启了规范 snapshot_readonly，"
            "只能通过快照推送（evalp push / publish）更新：请在来源平台修改后重新导出并推送；"
            "确需手工修改时，先在 agent 设置的「规范」里关闭 snapshot_readonly。",
            {
                "convention": "snapshot_readonly",
                "asset": key,
                "agents": [a.name for a in agents],
            },
            status=409,
        )

    @staticmethod
    async def _referencing_agents(s: AsyncSession, a: Asset) -> list[Agent]:
        """版本或草稿引用了该资产的 agent。"""
        rev_ids = select(AssetRevision.id).where(AssetRevision.asset_id == a.id)
        by_version = (
            select(Agent)
            .join(Version, Version.agent_id == Agent.id)
            .join(VersionAssetRef, VersionAssetRef.version_id == Version.id)
            .where(VersionAssetRef.asset_revision_id.in_(rev_ids))
        )
        by_draft = (
            select(Agent)
            .join(Draft, Draft.agent_id == Agent.id)
            .where(cast(Draft.spec, Text).like(f"%asset://{a.kind}/{a.name}@%"))
        )
        found: dict[str, Agent] = {}
        for stmt in (by_version, by_draft):
            for agent in (await s.execute(stmt)).scalars():
                found[agent.id] = agent
        return list(found.values())

    # ------------------------------------------------------------ 上传

    async def upload(self, reader: Callable[[int], Awaitable[bytes]]) -> UploadOut:
        """接收大文件：边读边写入临时文件（限 100MB），再放入 BlobStore。

        返回的 blob_upload_id 就是 blob 的 sha256；创建资产时可用它代替 content。
        未被任何修订引用的 blob 会在 24 小时后被 GC。
        """
        fd, tmp_name = tempfile.mkstemp(prefix="upload-", dir=self._tmp_dir())
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await reader(_CHUNK):
                    size += len(chunk)
                    if size > UPLOAD_LIMIT:
                        raise ValidationFailed(
                            f"上传文件不能超过 {UPLOAD_LIMIT // (1024 * 1024)}MB",
                            {"errors": [{"path": "file", "message": "文件过大"}], "limit": UPLOAD_LIMIT},
                        )
                    await asyncio.to_thread(self._write, out, chunk)
            h = await self.ctx.blobs.put_file(tmp_name)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return UploadOut(blob_upload_id=h, size=size)

    @staticmethod
    def _write(out: BinaryIO, chunk: bytes) -> None:
        out.write(chunk)

    def _tmp_dir(self) -> str:
        d = self.ctx.blobs.root / ".tmp"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    # ------------------------------------------------------------ 引用 / 删除

    async def references(self, asset_id: str, rev: int | None = None) -> dict[str, Any]:
        """哪些版本、哪些评测（以及哪些草稿）引用了该资产（可限定修订号）。"""
        async with self.ctx.db.read() as s:
            a = await self._get_asset(s, asset_id)
            rev_stmt = select(AssetRevision.id).where(AssetRevision.asset_id == a.id)
            if rev is not None:
                rev_stmt = rev_stmt.where(AssetRevision.rev == rev)
            vrows = (
                await s.execute(
                    select(VersionAssetRef, Version, Agent, AssetRevision)
                    .join(Version, Version.id == VersionAssetRef.version_id)
                    .join(Agent, Agent.id == Version.agent_id)
                    .join(AssetRevision, AssetRevision.id == VersionAssetRef.asset_revision_id)
                    .where(VersionAssetRef.asset_revision_id.in_(rev_stmt))
                    .order_by(Version.id.desc(), VersionAssetRef.spec_path)
                )
            ).all()
            xpaths: dict[str, dict[str, str]] = {}  # version_id -> 下标路径到身份键路径的映射（与 diff 一致）
            versions = [
                {
                    "version_id": v.id,
                    "agent_id": ag.id,
                    "agent_name": ag.name,
                    "seq": v.seq,
                    "rev": ar.rev,
                    "revision_id": ar.id,
                    "spec_path": xpaths.setdefault(v.id, identity_paths(v.spec)).get(
                        ref.spec_path, ref.spec_path
                    ),
                }
                for ref, v, ag, ar in vrows
            ]
            erows = (
                await s.execute(
                    select(EvalRun, AssetRevision)
                    .join(AssetRevision, AssetRevision.id == EvalRun.dataset_rev_id)
                    .where(EvalRun.dataset_rev_id.in_(rev_stmt))
                    .order_by(EvalRun.id.desc())
                )
            ).all()
            evals = [
                {
                    "run_id": r.id,
                    "batch_id": r.batch_id,
                    "status": r.status,
                    "agent_id": r.agent_id,
                    "version_id": r.version_id,
                    "dataset_rev_id": r.dataset_rev_id,
                    "rev": ar.rev,
                }
                for r, ar in erows
            ]
            drows = (
                await s.execute(
                    select(Draft, Agent)
                    .join(Agent, Agent.id == Draft.agent_id)
                    .where(
                        or_(
                            cast(Draft.spec, Text).like(f"%asset://{a.kind}/{a.name}@%"),
                            cast(Draft.spec, Text).like(f'%asset://{a.kind}/{a.name}"%'),
                        )
                    )
                    .order_by(Draft.id.desc())
                )
            ).all()
            drafts = [
                {"draft_id": d.id, "draft_name": d.name, "agent_id": ag.id, "agent_name": ag.name}
                for d, ag in drows
            ]
            return {
                "asset": asset_out(a, await self._latest_of(s, a)).model_dump(),
                "versions": versions,
                "evals": evals,
                "drafts": drafts,
                "in_use": bool(versions or evals),
            }

    async def delete(self, asset_id: str, *, hard: bool = False) -> dict[str, Any]:
        """删除资产。

        默认软删除（列表默认不显示，已有版本仍可读取其内容）；``hard=True`` 物理删除，
        被版本或评测引用时抛 ASSET_IN_USE。
        """
        async with self.ctx.db.write() as s:
            a = await self._get_asset(s, asset_id)
            await self.ensure_writable(s, a)
            rev_ids = select(AssetRevision.id).where(AssetRevision.asset_id == a.id)
            n_versions = (
                await s.execute(
                    select(func.count(func.distinct(VersionAssetRef.version_id))).where(
                        VersionAssetRef.asset_revision_id.in_(rev_ids)
                    )
                )
            ).scalar_one()
            n_evals = (
                await s.execute(
                    select(func.count()).select_from(EvalRun).where(EvalRun.dataset_rev_id.in_(rev_ids))
                )
            ).scalar_one()
            if not hard:
                if a.deleted_at is None:
                    a.deleted_at = now_iso()
                return {"id": a.id, "deleted": "soft", "versions": n_versions, "evals": n_evals}
            if n_versions or n_evals:
                raise AssetInUse(
                    f"资产被 {n_versions} 个版本、{n_evals} 个评测引用，无法硬删除（可软删除）",
                    {"versions": n_versions, "evals": n_evals},
                )
            revs = (
                await s.execute(
                    select(AssetRevision.id, AssetRevision.blob_hash).where(AssetRevision.asset_id == a.id)
                )
            ).all()
            for rid, bh in revs:
                await s.execute(delete(EvalCase).where(EvalCase.dataset_rev_id == rid))
                await s.execute(delete(AssetRevision).where(AssetRevision.id == rid))
                await self.ctx.blobs.decref(bh, session=s)
            await s.delete(a)
            return {"id": asset_id, "deleted": "hard", "versions": 0, "evals": 0}

    async def restore(self, asset_id: str) -> AssetOut:
        """恢复被软删除的资产。"""
        async with self.ctx.db.write() as s:
            a = await self._get_asset(s, asset_id)
            a.deleted_at = None
            if await self._latest_of(s, a) is None:  # GC 可能已清掉未被引用的最新修订：指向剩余的最大修订
                top = (
                    await s.execute(select(func.max(AssetRevision.rev)).where(AssetRevision.asset_id == a.id))
                ).scalar_one()
                a.latest_rev = top or 0
            return asset_out(a, await self._latest_of(s, a))

    # ------------------------------------------------------------ diff

    async def diff(self, left_rev_id: str, right_rev_id: str) -> dict[str, Any]:
        """两个修订的 diff：文本类返回 unified diff；数据集按 case_key 统计增删改。"""
        async with self.ctx.db.read() as s:
            lr = await self._revision_orm(s, left_rev_id)
            rr = await self._revision_orm(s, right_rev_id)
            la, ra = await self._get_asset(s, lr.asset_id), await self._get_asset(s, rr.asset_id)
            if la.kind != ra.kind:
                raise ValidationFailed(
                    "只能比较同一类型的资产修订",
                    {"errors": [{"path": "right", "message": f"{la.kind} 与 {ra.kind} 类型不同"}]},
                )
            lo, ro = revision_out(lr), revision_out(rr)
            lname, rname = f"{la.name}@{lr.rev}", f"{ra.name}@{rr.rev}"
            kind = la.kind
        head = {
            "kind": kind,
            "left": {**lo.model_dump(), "name": la.name},
            "right": {**ro.model_dump(), "name": ra.name},
            "identical": lo.blob_hash == ro.blob_hash,
        }
        if kind == "dataset":
            from evalp.services.dataset import DatasetService

            return {
                **head,
                "type": "dataset",
                **await self.ctx.svc(DatasetService).diff_revisions(left_rev_id, right_rev_id),
            }
        lb = await self.ctx.blobs.get_bytes(lo.blob_hash)
        rb = await self.ctx.blobs.get_bytes(ro.blob_hash)
        try:
            lt, rt = lb.decode("utf-8"), rb.decode("utf-8")
        except UnicodeDecodeError:
            return {
                **head,
                "type": "binary",
                "content_diff": None,
                "sizes": {"left": lo.size, "right": ro.size},
            }
        return {
            **head,
            "type": "text",
            "content_diff": unified_content_diff(lt, rt, lname, rname),
        }


__all__ = ["AssetService", "asset_out", "revision_out"]
