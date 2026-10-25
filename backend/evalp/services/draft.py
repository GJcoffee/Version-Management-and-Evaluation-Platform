"""草稿服务：保存（乐观锁）、校验、发布（详设 4.4）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.context import AppContext
from evalp.errors import DraftConflict, NotFound
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.schemas.agent import DraftCreate, DraftOut, DraftValidateOut, PublishOut
from evalp.services.agent import AgentService, check_draft_spec, minimal_spec
from evalp.services.convert import draft_out
from evalp.services.version import VersionService
from evalp.spec.canonical import canonicalize
from evalp.spec.git import capture_git
from evalp.spec.resolve import unresolve
from evalp.spec.validate import validate_spec
from evalp.store.models import Agent, Draft, Version, VersionAssetRef
from evalp.utils.time import now_iso

log = get_logger(__name__)


def conflict_error(draft: Draft, **extra: Any) -> DraftConflict:
    """DRAFT_CONFLICT：details 中带 current_revision。"""
    return DraftConflict(
        f"草稿已被修改（当前 revision={draft.revision}）",
        {"current_revision": draft.revision, "updated_at": draft.updated_at, **extra},
    )


class DraftService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    @staticmethod
    async def get_orm(s: AsyncSession, draft_id: str) -> Draft:
        d = await s.get(Draft, draft_id)
        if d is None:
            raise NotFound(f"草稿不存在: {draft_id}", {"id": draft_id})
        return d

    @staticmethod
    async def find_by_name(s: AsyncSession, agent_id: str, name: str) -> Draft | None:
        return (
            await s.execute(select(Draft).where(Draft.agent_id == agent_id, Draft.name == name))
        ).scalar_one_or_none()

    async def list(self, agent_ref: str) -> list[DraftOut]:
        """agent 的全部草稿。"""
        async with self.ctx.db.read() as s:
            agent = await AgentService.get_orm(s, agent_ref)
            rows = (
                await s.execute(
                    select(Draft).where(Draft.agent_id == agent.id).order_by(Draft.created_at, Draft.id)
                )
            ).scalars()
            return [draft_out(d) for d in rows]

    async def get(self, draft_id: str) -> DraftOut:
        async with self.ctx.db.read() as s:
            return draft_out(await self.get_orm(s, draft_id))

    async def create(self, agent_ref: str, body: DraftCreate) -> DraftOut:
        """新建草稿；可指定初始 spec，或从某个版本复制（``from_version_id``），否则使用最小模板。"""
        async with self.ctx.db.write() as s:
            agent = await AgentService.get_orm(s, agent_ref)
            base: str | None = None
            if body.from_version_id is not None:
                v = await VersionService(self.ctx).resolve_ref_orm(s, body.from_version_id)
                if v.agent_id != agent.id:
                    raise NotFound("该版本不属于这个 agent", {"version_id": body.from_version_id})
                spec, base = unresolve(v.spec), v.id
            elif body.spec is not None:
                spec = check_draft_spec(body.spec, agent.kind)
            else:
                spec = await minimal_spec(s, agent.kind)
            draft = await AgentService.insert_draft(s, agent, body.name, spec, base_version_id=base)
            return draft_out(draft)

    # ------------------------------------------------------------ 保存

    async def save_in_session(
        self, s: AsyncSession, draft: Draft, agent_kind: str, spec: Any, expected_revision: int | None,
        *, force: bool = False,
    ) -> Draft:  # fmt: skip
        """事务内保存草稿：revision 不一致抛 DraftConflict（force=True 时跳过检查）。"""
        spec = check_draft_spec(spec, agent_kind)
        if not force and expected_revision != draft.revision:
            raise conflict_error(draft)
        draft.spec = spec
        draft.revision += 1
        draft.updated_at = now_iso()
        await s.flush()
        return draft

    async def save(self, draft_id: str, spec: dict[str, Any], expected_revision: int) -> DraftOut:
        """保存草稿（乐观锁）。revision 不一致时抛 DraftConflict，details.current_revision 为当前值。"""
        async with self.ctx.db.write() as s:
            draft = await self.get_orm(s, draft_id)
            agent = await s.get(Agent, draft.agent_id)
            assert agent is not None
            await self.save_in_session(s, draft, agent.kind, spec, expected_revision)
            return draft_out(draft)

    # ------------------------------------------------------------ 校验

    async def validate(self, draft_id: str, spec: dict | None = None) -> DraftValidateOut:
        """校验草稿：返回 errors / warnings，以及固化后的 Spec 预览（有错误时为 null）。

        spec 不为空时做干跑校验：校验传入的 spec，不读取也不修改已保存的草稿内容。
        """
        async with self.ctx.db.read() as s:
            draft = await self.get_orm(s, draft_id)
            agent = await s.get(Agent, draft.agent_id)
            assert agent is not None
            report = await validate_spec(
                self.ctx, s, spec if spec is not None else draft.spec, agent_kind=agent.kind
            )
        return DraftValidateOut(**report.to_dict())

    # ------------------------------------------------------------ 发布

    async def publish(
        self, draft_id: str, expected_revision: int, notes: str | None = None, via: str = "api"
    ) -> PublishOut:
        """发布草稿为版本（详设 4.4）：整个过程在一个写事务内完成。

        同一 agent 下 spec_hash 相同时返回已有版本（existed=True）。
        """
        versions = self.ctx.svc(VersionService)
        async with self.ctx.db.write() as s:
            draft = await self.get_orm(s, draft_id)
            if draft.revision != expected_revision:
                raise conflict_error(draft)
            agent = await s.get(Agent, draft.agent_id)
            assert agent is not None
            # 1、2、3：结构校验、业务校验、固化引用
            report = await validate_spec(self.ctx, s, draft.spec, agent_kind=agent.kind)
            report.raise_for_errors()
            spec, resolved = report.spec, report.resolved
            assert spec is not None and resolved is not None
            # 4：代码型 agent 采集 git 信息
            git_info = None
            if spec.kind == "code":
                if spec.source.capture_git:
                    git_info = await capture_git(
                        spec.source.repo, allow_dirty=spec.source.allow_dirty, blobs=self.ctx.blobs, session=s
                    )
                    resolved["source"]["git"] = git_info.spec_pin()
                else:
                    resolved["source"]["git"] = None
            # 5：规范化与哈希
            canon = canonicalize(resolved)
            existing = (
                await s.execute(
                    select(Version).where(Version.agent_id == agent.id, Version.spec_hash == canon.hash)
                )
            ).scalar_one_or_none()
            if existing is not None:
                draft.base_version_id = existing.id
                return PublishOut(version=await versions.out_in_session(s, existing), existed=True)
            version = Version(
                id=new_id("ver"),
                agent_id=agent.id,
                seq=await versions.next_seq(s, agent.id),
                spec_hash=canon.hash,
                spec=canon.data,
                source=git_info.source_column() if git_info is not None else {},
                parent_version_id=draft.base_version_id,
                from_draft_id=draft.id,
                notes=notes,
                created_via=via,
                created_at=now_iso(),
            )
            s.add(version)
            await s.flush()
            seen: set[tuple[str, str]] = set()
            for ref in report.refs:
                if (ref.revision_id, ref.spec_path) in seen:
                    continue
                seen.add((ref.revision_id, ref.spec_path))
                s.add(
                    VersionAssetRef(
                        version_id=version.id, asset_revision_id=ref.revision_id, spec_path=ref.spec_path
                    )
                )
            if git_info is not None and git_info.patch_blob:
                await self.ctx.blobs.incref(git_info.patch_blob, session=s)
            draft.base_version_id = version.id
            agent.updated_at = version.created_at
            await s.flush()
            out = PublishOut(version=await versions.out_in_session(s, version), existed=False)
        log.info("version.published", agent_id=agent.id, version_id=version.id, seq=version.seq, via=via)
        return out
