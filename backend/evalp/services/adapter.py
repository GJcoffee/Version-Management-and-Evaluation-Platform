"""接入覆盖单独留修订；运行快照与不可变业务版本分离。"""

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.context import AppContext
from evalp.errors import Conflict, EvalpError, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.runtime.adapters.registry import validate_binding
from evalp.spec.models import AdapterBinding
from evalp.store.models import AdapterCredential, AdapterRevision, Version
from evalp.utils.canonical import canonical_json, sha256_hex
from evalp.utils.time import now_iso


class BindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: AdapterBinding | None
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    acknowledge_transport_change: bool = False
    dry_run: bool = False


class BindingBatch(BindingUpdate):
    version_ids: list[str] = Field(min_length=1, max_length=200)
    expected_revisions: dict[str, int]


class BindingRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    adapter_id: str = "hiagent"
    secret: SecretStr


class AdapterService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def snapshot(self, s: AsyncSession, version: Version) -> dict[str, Any]:
        """事务内取当前绑定；接入字段不存在时显式固定原运行方式。"""
        row = (await s.execute(
            select(AdapterRevision).where(AdapterRevision.version_id == version.id)
            .order_by(AdapterRevision.revision.desc()).limit(1)
        )).scalar_one_or_none()
        if row:
            return deepcopy(row.snapshot)
        return self.make_snapshot(version.spec.get("adapter"), 0, "spec")

    @staticmethod
    def make_snapshot(binding: dict | None, revision: int, source: str) -> dict[str, Any]:
        return {
            "revision": revision, "binding": deepcopy(binding), "source": source,
            "fingerprint": sha256_hex(canonical_json(binding)),
        }

    async def validate(self, binding: AdapterBinding) -> AdapterBinding:
        binding = validate_binding(binding)
        async with self.ctx.db.read() as s:
            row = await s.get(AdapterCredential, binding.credential_ref)
            if row is None or row.adapter_id != binding.id:
                raise ValidationFailed("凭据引用不存在或不适用于该适配器")
        if not await self.ctx.secrets.get(binding.credential_ref):
            raise ValidationFailed("凭据正文不可用，请重新保存凭据")
        return binding

    async def get(self, ref: str) -> dict[str, Any]:
        from evalp.services.version import VersionService

        async with self.ctx.db.read() as s:
            v = await self.ctx.svc(VersionService).resolve_ref_orm(s, ref)
            current = await self.snapshot(s, v)
            rows = (await s.execute(
                select(AdapterRevision).where(AdapterRevision.version_id == v.id)
                .order_by(AdapterRevision.revision.desc())
            )).scalars().all()
            history = [{
                **r.snapshot, "reason": r.reason, "created_at": r.created_at,
            } for r in rows]
            original = self.make_snapshot(v.spec.get("adapter"), 0, "spec")
            return {"version_id": v.id, "current": current, "history": [*history, original]}

    async def update(self, ref: str, body: BindingUpdate) -> dict[str, Any]:
        from evalp.services.version import VersionService

        binding = (await self.validate(body.binding)).model_dump() if body.binding else None
        async with self.ctx.db.write() as s:
            v = await self.ctx.svc(VersionService).resolve_ref_orm(s, ref)
            current = await self.snapshot(s, v)
            if current["revision"] != body.expected_revision:
                raise Conflict("接入已被修改，请刷新后重试", {"current_revision": current["revision"]})
            if binding and not current["binding"] and not body.acknowledge_transport_change:
                raise ValidationFailed("切换原运行方式前，需要确认旧入口中的业务处理已保留")
            if binding is None and v.spec.get("transport") == "adapter":
                raise ValidationFailed("此版本没有可恢复的 HTTP 或子进程入口")
            target = self.make_snapshot(binding, current["revision"] + 1, "override")
            if body.dry_run:
                return {"version_id": v.id, "before": current, "target": target, "applied": False}
            s.add(AdapterRevision(
                version_id=v.id, revision=target["revision"], snapshot=target,
                reason=body.reason, created_at=now_iso(),
            ))
            return {"version_id": v.id, "before": current, "target": target, "applied": True}

    async def batch(self, body: BindingBatch) -> list[dict[str, Any]]:
        """逐项事务，失败项保留原绑定；必须为每项提供读取到的修订。"""
        if len(set(body.version_ids)) != len(body.version_ids):
            raise ValidationFailed("版本列表不能重复")
        results = []
        for vid in body.version_ids:
            try:
                if vid not in body.expected_revisions:
                    raise ValidationFailed("缺少该版本的预期接入修订")
                update = BindingUpdate.model_validate({
                    **body.model_dump(exclude={"version_ids", "expected_revisions"}),
                    "expected_revision": body.expected_revisions[vid],
                })
                results.append(await self.update(vid, update))
            except EvalpError as exc:
                results.append({"version_id": vid, "applied": False, **exc.to_dict()})
        return results

    async def rollback(self, ref: str, body: BindingRollback) -> dict[str, Any]:
        state = await self.get(ref)
        previous = next((r for r in state["history"] if r["revision"] == body.revision), None)
        if previous is None:
            raise NotFound("接入修订不存在")
        return await self.update(ref, BindingUpdate(
            binding=previous["binding"], expected_revision=body.expected_revision,
            reason=body.reason, acknowledge_transport_change=True,
        ))

    async def credentials(self) -> list[dict[str, str]]:
        async with self.ctx.db.read() as s:
            rows = (await s.execute(select(AdapterCredential))).scalars().all()
            return [{"ref": r.ref, "name": r.name, "adapter_id": r.adapter_id} for r in rows]

    async def create_credential(self, body: CredentialCreate) -> dict[str, str]:
        """创建不可变凭据引用，更新凭据时创建新引用，历史运行不随之变化。"""
        if body.adapter_id != "hiagent" or not body.secret.get_secret_value().strip():
            raise ValidationFailed("适配器或凭据不合法")
        ref = f"adapter:{new_id('prv')}"
        await self.ctx.secrets.set(ref, body.secret.get_secret_value())
        try:
            async with self.ctx.db.write() as s:
                s.add(AdapterCredential(
                    ref=ref, name=body.name, adapter_id=body.adapter_id, created_at=now_iso(),
                ))
        except BaseException:
            await self.ctx.secrets.delete(ref)
            raise
        return {"ref": ref, "name": body.name, "adapter_id": body.adapter_id}
