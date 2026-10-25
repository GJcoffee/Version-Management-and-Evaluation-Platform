"""Agent 服务：增删改查；创建时同时创建 main 草稿。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import Conflict, NotFound, ValidationFailed
from evalp.ids import is_id, new_id
from evalp.logging import get_logger
from evalp.schemas.agent import AgentCreate, AgentCreated, AgentOut, AgentUpdate
from evalp.services.convert import agent_out, draft_out
from evalp.spec.models import SCHEMA_VERSION, CodeSpec, ConfigSpec, dump_spec
from evalp.store.models import Agent, Draft, Provider, Version, VersionTag
from evalp.utils.time import now_iso

log = get_logger(__name__)


async def minimal_spec(s: AsyncSession, kind: str) -> dict[str, Any]:
    """生成 kind 对应的最小 Spec 模板（补齐默认值，便于编辑）。

    配置型：若已登记 Provider，则填入第一个 Provider 及其第一个模型。
    """
    if kind == "code":
        spec = CodeSpec.model_validate(
            {
                "kind": "code",
                "transport": "http",
                "http": {"base_url": "http://127.0.0.1:8001"},
                "source": {"capture_git": False},
            }
        )
        data = dump_spec(spec)
    else:
        provider = (await s.execute(select(Provider).order_by(Provider.id).limit(1))).scalar_one_or_none()
        model_name = ""
        if provider is not None and provider.models:
            model_name = provider.models[0]["name"]
        spec = ConfigSpec.model_validate(
            {
                "kind": "config",
                "model": {"provider": "placeholder", "name": "placeholder"},
                "prompts": {"system": "你是一个乐于助人的助手。"},
            }
        )
        data = dump_spec(spec)
        data["model"]["provider"] = provider.name if provider is not None else ""
        data["model"]["name"] = model_name
    for key in ("name", "description", "notes"):
        data.pop(key, None)
    return data


def check_draft_spec(spec: Any, agent_kind: str) -> dict[str, Any]:
    """草稿保存时的轻量检查：必须是对象且 kind 与 agent 一致（缺省时补上 kind / schema）。

    完整的结构与业务校验放在 validate / publish，草稿允许处于未完成状态。
    """
    if not isinstance(spec, dict):
        raise ValidationFailed("Spec 必须是对象", {"errors": [{"path": "spec", "message": "必须是对象"}]})
    out = dict(spec)
    kind = out.setdefault("kind", agent_kind)
    if kind != agent_kind:
        raise ValidationFailed(
            f"Spec 的 kind={kind} 与 agent 的类型 {agent_kind} 不一致",
            {"errors": [{"path": "spec.kind", "message": f"必须是 {agent_kind}"}]},
        )
    schema = out.setdefault("schema", SCHEMA_VERSION)
    if schema != SCHEMA_VERSION:
        raise ValidationFailed(
            f"不支持的 schema: {schema}",
            {"errors": [{"path": "spec.schema", "message": f"必须是 {SCHEMA_VERSION}"}]},
        )
    return out


class AgentService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------ 事务内辅助（工作区同步也会用）

    @staticmethod
    async def get_orm(s: AsyncSession, ref: str) -> Agent:
        """按 id（agt_…）或名称取 agent；不存在抛 NotFound。"""
        agent = await AgentService.find_orm(s, ref)
        if agent is None:
            raise NotFound(f"Agent 不存在: {ref}", {"ref": ref})
        return agent

    @staticmethod
    async def find_orm(s: AsyncSession, ref: str) -> Agent | None:
        if is_id(ref, "agt"):
            return await s.get(Agent, ref)
        return (await s.execute(select(Agent).where(Agent.name == ref))).scalar_one_or_none()

    @staticmethod
    async def insert_agent(s: AsyncSession, name: str, kind: str, description: str | None = None) -> Agent:
        now = now_iso()
        agent = Agent(
            id=new_id("agt"),
            name=name,
            kind=kind,
            description=description,
            archived=0,
            created_at=now,
            updated_at=now,
        )
        s.add(agent)
        try:
            await s.flush()
        except IntegrityError:
            raise Conflict(f"Agent 名称已存在: {name}", {"name": name}) from None
        return agent

    @staticmethod
    async def insert_draft(
        s: AsyncSession, agent: Agent, name: str, spec: dict[str, Any], base_version_id: str | None = None
    ) -> Draft:
        now = now_iso()
        draft = Draft(
            id=new_id("drf"),
            agent_id=agent.id,
            name=name,
            spec=spec,
            base_version_id=base_version_id,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        s.add(draft)
        try:
            await s.flush()
        except IntegrityError:
            raise Conflict(f"草稿名称已存在: {name}", {"name": name}) from None
        return draft

    # ------------------------------------------------------------ 接口

    @staticmethod
    def _check_reserved(name: str | None) -> None:
        """平台内部使用的 agent 名称（如评审 agent ``evalp-judge``）不能被用户占用。"""
        from evalp.services.judge import HIDDEN_AGENTS  # 延迟导入：judge 依赖本模块

        if name in HIDDEN_AGENTS:
            raise ValidationFailed(
                f"agent 名称 {name} 是平台保留名称",
                {"errors": [{"path": "name", "message": "保留名称，请换一个"}]},
            )

    async def create(self, body: AgentCreate) -> AgentCreated:
        """创建 agent，同时创建 main 草稿（可带初始 spec，没带时生成该 kind 的最小模板）。"""
        self._check_reserved(body.name)
        async with self.ctx.db.write() as s:
            spec = (
                check_draft_spec(body.spec, body.kind)
                if body.spec is not None
                else await minimal_spec(s, body.kind)
            )
            agent = await self.insert_agent(s, body.name, body.kind, body.description)
            draft = await self.insert_draft(s, agent, "main", spec)
            out = AgentCreated(agent=agent_out(agent), draft=draft_out(draft))
        log.info("agent.created", agent_id=out.agent.id, name=body.name, kind=body.kind)
        return out

    async def _latest_versions(self, s: AsyncSession, agent_ids: list[str]) -> dict[str, Version]:
        if not agent_ids:
            return {}
        sub = (
            select(Version.agent_id, func.max(Version.seq).label("seq"))
            .where(Version.agent_id.in_(agent_ids))
            .group_by(Version.agent_id)
            .subquery()
        )
        rows = (
            await s.execute(
                select(Version).join(sub, (Version.agent_id == sub.c.agent_id) & (Version.seq == sub.c.seq))
            )
        ).scalars()
        return {v.agent_id: v for v in rows}

    async def _tags_of(self, s: AsyncSession, agent_ids: list[str]) -> dict[str, list[str]]:
        """一次查询批量取多个 agent 的标签名（每个 agent 内按字典序）。"""
        out: dict[str, list[str]] = {}
        if not agent_ids:
            return out
        rows = (
            await s.execute(
                select(VersionTag.agent_id, VersionTag.tag)
                .where(VersionTag.agent_id.in_(agent_ids))
                .order_by(VersionTag.agent_id, VersionTag.tag)
            )
        ).all()
        for aid, tag in rows:
            out.setdefault(aid, []).append(tag)
        return out

    async def list(
        self,
        *,
        include_archived: bool = False,
        include_hidden: bool = False,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[AgentOut]:
        """分页列出 agent（最新创建在前），默认不含已归档的和平台内部的隐藏 agent（评审 agent）。"""
        from evalp.services.judge import HIDDEN_AGENTS

        stmt = select(Agent)
        if not include_hidden:
            stmt = stmt.where(Agent.name.not_in(HIDDEN_AGENTS))
        if not include_archived:
            stmt = stmt.where(Agent.archived == 0)
        if kind:
            stmt = stmt.where(Agent.kind == kind)
        if q:
            stmt = stmt.where(Agent.name.like(f"%{q}%") | Agent.description.like(f"%{q}%"))
        async with self.ctx.db.read() as s:
            page = await paginate(s, stmt, Agent.id, limit=limit, cursor=cursor)
            agents: list[Agent] = page.items
            ids = [a.id for a in agents]
            latest = await self._latest_versions(s, ids)
            tags = await self._tags_of(s, ids)
            items = [agent_out(a, latest.get(a.id), tags.get(a.id)) for a in agents]
        return Page[AgentOut](items=items, next_cursor=page.next_cursor)

    async def get(self, ref: str) -> AgentOut:
        """按 id 或名称获取。"""
        async with self.ctx.db.read() as s:
            a = await self.get_orm(s, ref)
            latest = await self._latest_versions(s, [a.id])
            tags = await self._tags_of(s, [a.id])
            return agent_out(a, latest.get(a.id), tags.get(a.id))

    async def update(self, ref: str, body: AgentUpdate) -> AgentOut:
        """修改名称 / 描述 / 归档状态。"""
        fields = body.model_fields_set
        if "name" in fields:
            self._check_reserved(body.name)
        async with self.ctx.db.write() as s:
            a = await self.get_orm(s, ref)
            if "name" in fields and body.name is not None:
                a.name = body.name
            if "description" in fields:
                a.description = body.description
            if "archived" in fields and body.archived is not None:
                a.archived = 1 if body.archived else 0
            a.updated_at = now_iso()
            try:
                await s.flush()
            except IntegrityError:
                raise Conflict(f"Agent 名称已存在: {body.name}", {"name": body.name}) from None
            latest = await self._latest_versions(s, [a.id])
            tags = await self._tags_of(s, [a.id])
            return agent_out(a, latest.get(a.id), tags.get(a.id))

    async def delete(self, ref: str) -> None:
        """删除 agent 及其草稿。已有版本的 agent 不能删除（请归档）。"""
        async with self.ctx.db.write() as s:
            a = await self.get_orm(s, ref)
            n = (
                await s.execute(select(func.count()).select_from(Version).where(Version.agent_id == a.id))
            ).scalar_one()
            if n:
                raise Conflict(
                    f"Agent 已有 {n} 个版本，不能删除，请改为归档（PATCH archived=true）",
                    {"versions": n},
                    code="AGENT_IN_USE",
                )
            await s.execute(delete(Draft).where(Draft.agent_id == a.id))
            await s.execute(delete(Agent).where(Agent.id == a.id))
