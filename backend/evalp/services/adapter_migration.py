"""显式迁移旧接入；原业务版本不变，运行绑定统一并留存原值。"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import Conflict, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.runtime.adapters.binding import normalize_binding
from evalp.services.adapter import AdapterService, fingerprint_of
from evalp.services.agent import AgentService
from evalp.spec.models import AdapterBinding
from evalp.store.models import (
    AdapterBindingMigration,
    AdapterEnvironment,
    AdapterEnvironmentRevision,
    Draft,
    EvalRun,
    Session,
    Version,
    VersionAdapterRoute,
)
from evalp.utils.time import now_iso

HISTORICAL_REASON = "历史业务内容尚未在原生入口发布并核实；请先绑定对应原生部署，不能使用当前生产内容代替"


def native_spec(spec: dict[str, Any], adapter: str) -> dict[str, Any]:
    """迁移可编辑草稿的接入字段，业务参数与资产引用保持原样。"""
    return {**copy.deepcopy(spec), "transport": "adapter", "platform": adapter,
            "http": None, "subprocess": None, "adapter": None, "native_deployment_required": True}


class BindingMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    first_seq: int = Field(ge=1)
    last_seq: int = Field(ge=1)
    adapter: str = "hiagent"
    major: int = Field(1, ge=1)
    production_revision: int = Field(ge=1)
    test_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    dry_run: bool = True

    @model_validator(mode="after")
    def _range(self) -> BindingMigrationRequest:
        if self.last_seq < self.first_seq:
            raise ValueError("last_seq 必须不小于 first_seq")
        return self


def canonical_native(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """改为 adapter+major 并去掉旧桥；调用方保留原值且标记停止执行。"""
    normalized = normalize_binding(raw)
    config = copy.deepcopy(normalized.config)
    retired = bool(config.pop("business_bridge", None))
    return {"adapter": normalized.adapter, "major": normalized.major, "config": config,
            "credential_ref": normalized.credential_ref}, retired


class AdapterMigrationService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def get(self, migration_id: str) -> dict[str, Any]:
        """读取迁移计数和版本范围；原值仅留在本机数据库审计表。"""
        async with self.ctx.db.read() as s:
            row = await s.get(AdapterBindingMigration, migration_id)
            if row is None:
                raise NotFound("接入迁移记录不存在")
            return {"id": row.id, "agent_id": row.agent_id, "reason": row.reason,
                    "created_at": row.created_at, **row.result}

    async def migrate(self, body: BindingMigrationRequest) -> dict[str, Any]:
        """原子迁移整个范围和所有该平台存量绑定；干跑不写入，重复执行不产生重复记录。"""
        adapters = self.ctx.svc(AdapterService)
        state = await adapters.get(body.adapter)
        for name in ("production", "test"):
            entry = state[name]
            expected = getattr(body, f"{name}_revision")
            if entry["revision"] != expected:
                raise Conflict(f"{name} 环境已变化，请重读后迁移")
            if entry["configured"]:
                binding = await adapters.validate(AdapterBinding.model_validate(entry["binding"]))
                if binding.major != body.major or binding.config.get("business_bridge"):
                    raise ValidationFailed("当前环境必须是同 major 的原生平台绑定")
        if not state["production"]["configured"]:
            raise ValidationFailed("必须先配置原生生产环境")
        now, migration_id = now_iso(), new_id("mig")
        async with self.ctx.db.write() as s:
            for name in ("production", "test"):
                current = await adapters._state(s, name, body.adapter)
                if current["revision"] != state[name]["revision"]:
                    raise Conflict("环境在迁移前已变化，请重试")
            agent = await AgentService.get_orm(s, body.agent)
            versions = (await s.execute(select(Version).where(
                Version.agent_id == agent.id, Version.seq >= body.first_seq, Version.seq <= body.last_seq,
            ).order_by(Version.seq))).scalars().all()
            if [v.seq for v in versions] != list(range(body.first_seq, body.last_seq + 1)):
                raise ValidationFailed("迁移范围必须完整，不能缺少版本")
            routes = {r.version_id: r for r in (await s.execute(select(VersionAdapterRoute).where(
                VersionAdapterRoute.version_id.in_([v.id for v in versions]),
            ))).scalars()}
            pending = []
            for v in versions:
                if v.id in routes:
                    r = routes[v.id]
                    if (r.adapter, r.major) != (body.adapter, body.major):
                        raise Conflict("版本已有不同平台接入，不能覆盖")
                elif v.spec.get("kind") == "code" and v.spec.get("transport") == "http":
                    pending.append(v)
                else:
                    raise ValidationFailed(f"v{v.seq} 不是待迁移的 HTTP 版本")
            ids = {v.id for v in versions}
            drafts = (await s.execute(select(Draft).where(Draft.agent_id == agent.id))).scalars().all()
            old_drafts = [d for d in drafts if d.spec.get("transport") == "http"]
            sessions = (await s.execute(select(Session))).scalars().all()
            runs = (await s.execute(select(EvalRun))).scalars().all()
            env_rows = (await s.execute(select(AdapterEnvironmentRevision))).scalars().all()
            before: dict[str, Any] = {
                "versions": [{"id": v.id, "seq": v.seq, "spec_hash": v.spec_hash} for v in pending],
                "sessions": {}, "eval_runs": {}, "environments": {}, "drafts": {},
            }
            changes: list[tuple[Any, str, Any]] = []
            by_id = {v.id: v for v in versions}
            for name, rows in (("sessions", sessions), ("eval_runs", runs)):
                for row in rows:
                    original = row.adapter_snapshot
                    raw = (original or {}).get("binding")
                    selected = row.version_id in ids
                    if not selected and (not raw or normalize_binding(raw).adapter != body.adapter):
                        continue
                    if row.status in {"running", "pending", "queued", "paused", "cancelling"}:
                        raise Conflict("有关联会话或评测仍在执行，请结束后迁移")
                    snapshot = copy.deepcopy(original or {})
                    if raw:
                        binding, retired = canonical_native(raw)
                    else:
                        binding, retired = canonical_native(state["production"]["binding"])
                        params = by_id[row.version_id].spec.get("params", {})
                        # 原应用定位只服务于历史读取，不把当前应用标识伪装成历史值。
                        for old, new in (("hiagent_app", "app_id"), ("hiagent_version", "published_version")):
                            if params.get(old):
                                binding["config"][new] = params[old]
                        snapshot.update(environment="production", revision=state["production"]["revision"])
                    snapshot.update(
                        binding=binding, adapter=body.adapter, fingerprint=fingerprint_of(binding),
                    )
                    if selected or retired:
                        if not snapshot.get("migration_id"):
                            snapshot.update(
                                migration_id=migration_id, execution_blocked_reason=HISTORICAL_REASON,
                            )
                        if not raw:
                            snapshot["historical_identity"] = {
                                "app_id": by_id[row.version_id].spec.get("params", {}).get("hiagent_app"),
                                "published_version": by_id[row.version_id].spec.get("params", {}).get(
                                    "hiagent_version",
                                ),
                            }
                    if snapshot != original:
                        before[name][row.id] = original
                        changes.append((row, "adapter_snapshot", snapshot))
            for row in env_rows:
                if normalize_binding(row.binding).adapter != body.adapter:
                    continue
                binding, retired = canonical_native(row.binding)
                if binding != row.binding:
                    before["environments"][f"{row.environment}:{row.revision}"] = {
                        "binding": row.binding, "origin": row.origin,
                    }
                    changes.append((row, "binding", binding))
                    if retired:
                        env = await s.get(AdapterEnvironment, row.environment)
                        if env and env.revision == row.revision:
                            raise ValidationFailed("仍有旧桥环境生效，必须先推送原生环境")
                        changes.append((row, "origin", {
                            **(row.origin or {}), "migration_id": migration_id, "retired_bridge": True,
                        }))
            for d in old_drafts:
                before["drafts"][d.id] = {"spec": d.spec, "revision": d.revision, "updated_at": d.updated_at}
                changes.extend([(d, "spec", native_spec(d.spec, body.adapter)),
                                (d, "revision", d.revision + 1), (d, "updated_at", now)])
            result = {
                "adapter": body.adapter, "major": body.major, "first_seq": body.first_seq,
                "last_seq": body.last_seq, "versions": len(pending), "already_migrated": len(routes),
                **{name: len(before[name]) for name in ("sessions", "eval_runs", "environments", "drafts")},
                "historical_execution": "requires_verified_native_deployment",
            }
            if body.dry_run or not (pending or changes):
                return {**result, "dry_run": body.dry_run, "applied": False}
            s.add(AdapterBindingMigration(id=migration_id, agent_id=agent.id, reason=body.reason,
                                         before=before, result=result, created_at=now))
            await s.flush()
            for v in pending:
                s.add(VersionAdapterRoute(
                    version_id=v.id, adapter=body.adapter, major=body.major,
                    execution_blocked_reason=HISTORICAL_REASON, migration_id=migration_id,
                ))
            for row, attr, value in changes:
                setattr(row, attr, value)
            await s.flush()
            return {**result, "id": migration_id, "dry_run": False, "applied": True}
