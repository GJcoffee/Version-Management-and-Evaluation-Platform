"""适配器接入配置：全平台只有生产 / 测试两套，每次变化存档；运行快照与不可变业务版本分离。"""

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.context import AppContext
from evalp.errors import Conflict, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.runtime.adapters.registry import validate_binding
from evalp.schemas.adapter import Environment
from evalp.spec.models import AdapterBinding
from evalp.store.models import AdapterCredential, AdapterEnvironment, AdapterEnvironmentRevision
from evalp.utils.canonical import canonical_json, sha256_hex
from evalp.utils.time import now_iso

NO_CHANGE = "内容未变化"


class EnvironmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: AdapterBinding
    expected_revision: int = Field(ge=0, description="编辑时测试环境的当前存档号，0 表示尚未配置")
    reason: str = Field(min_length=1, max_length=1000)


class EnvironmentPush(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_revision: int = Field(ge=1, description="要推送的测试存档号，必须是当前的测试存档")
    reason: str = Field("", max_length=1000)


class EnvironmentRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, description="回到生产的哪个历史存档")
    reason: str = Field("", max_length=1000)


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    adapter_id: str = "hiagent"
    secret: SecretStr


def fingerprint_of(binding: dict | None) -> str | None:
    return sha256_hex(canonical_json(binding)) if binding is not None else None


class AdapterService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------ 读取

    @staticmethod
    def _out(name: str, env: AdapterEnvironment, row: AdapterEnvironmentRevision | None) -> dict[str, Any]:
        return {
            "environment": name, "configured": row is not None, "revision": env.revision,
            "binding": deepcopy(row.binding) if row else None,
            "fingerprint": fingerprint_of(row.binding) if row else None,
            "source": row.source if row else None, "reason": row.reason if row else None,
            "origin": row.origin if row else None, "created_at": row.created_at if row else None,
            "updated_at": env.updated_at,
        }

    async def _state(self, s: AsyncSession, name: str) -> dict[str, Any]:
        env = await s.get(AdapterEnvironment, name)
        assert env is not None
        row = await s.get(AdapterEnvironmentRevision, (name, env.revision)) if env.revision else None
        return self._out(name, env, row)

    async def get(self) -> dict[str, Any]:
        """两个环境的当前配置；``test_selectable`` 表示测试已配置且与生产不同，界面才提供测试选项。"""
        async with self.ctx.db.read() as s:
            production, test = await self._state(s, "production"), await self._state(s, "test")
        return {
            "production": production, "test": test,
            "test_selectable": test["configured"] and test["fingerprint"] != production["fingerprint"],
        }

    async def revisions(self, name: Environment) -> list[dict[str, Any]]:
        """环境的存档历史，新的在前。"""
        async with self.ctx.db.read() as s:
            env = await s.get(AdapterEnvironment, name)
            assert env is not None
            rows = (await s.execute(
                select(AdapterEnvironmentRevision).where(AdapterEnvironmentRevision.environment == name)
                .order_by(AdapterEnvironmentRevision.created_at.desc(),
                          AdapterEnvironmentRevision.revision.desc())
            )).scalars().all()
            return [{
                "revision": r.revision, "current": r.revision == env.revision, "binding": r.binding,
                "fingerprint": fingerprint_of(r.binding), "source": r.source, "reason": r.reason,
                "origin": r.origin, "created_at": r.created_at,
            } for r in rows]

    async def snapshot(
        self, s: AsyncSession, spec: dict[str, Any], environment: str
    ) -> dict[str, Any] | None:
        """事务内固化一次运行用的接入：只有 ``transport: adapter`` 的版本使用环境配置。

        要测试但测试未配置、或与生产内容相同时，落到生产并在 ``requested_environment`` 里留下请求的环境。
        """
        if spec.get("transport") != "adapter":
            return None
        production = await self._state(s, "production")
        chosen = production
        if environment == "test":
            test = await self._state(s, "test")
            if test["configured"] and test["fingerprint"] != production["fingerprint"]:
                chosen = test
        if not chosen["configured"]:
            raise ValidationFailed("生产环境尚未配置适配器，请先在 设置 → 适配器 中配置并推送到生产")
        out = {
            "environment": chosen["environment"], "revision": chosen["revision"],
            "binding": chosen["binding"], "fingerprint": chosen["fingerprint"], "reason": chosen["reason"],
        }
        if chosen["environment"] != environment:
            out["requested_environment"] = environment
        return out

    # ------------------------------------------------------------ 校验

    async def validate(self, binding: AdapterBinding) -> AdapterBinding:
        binding = validate_binding(binding)
        async with self.ctx.db.read() as s:
            row = await s.get(AdapterCredential, binding.credential_ref)
            if row is None or row.adapter_id != binding.id:
                raise ValidationFailed("凭据引用不存在或不适用于该适配器")
        if not await self.ctx.secrets.get(binding.credential_ref):
            raise ValidationFailed("凭据正文不可用，请重新保存凭据")
        return binding

    # ------------------------------------------------------------ 变更

    async def _append(
        self, s: AsyncSession, name: str, binding: dict[str, Any], source: str, reason: str,
        origin: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """追加存档并设为当前；内容与当前相同则不追加。"""
        before = await self._state(s, name)
        if before["fingerprint"] == fingerprint_of(binding):
            return {"environment": name, "before": before, "target": before, "applied": False,
                    "message": NO_CHANGE}
        latest = (await s.execute(
            select(func.max(AdapterEnvironmentRevision.revision))
            .where(AdapterEnvironmentRevision.environment == name)
        )).scalar_one_or_none() or 0
        now = now_iso()
        s.add(AdapterEnvironmentRevision(
            environment=name, revision=latest + 1, binding=binding, source=source, reason=reason,
            origin=origin, created_at=now,
        ))
        env = await s.get(AdapterEnvironment, name)
        assert env is not None
        env.revision, env.updated_at = latest + 1, now
        await s.flush()
        return {"environment": name, "before": before, "target": await self._state(s, name), "applied": True}

    async def update_test(self, body: EnvironmentUpdate) -> dict[str, Any]:
        binding = await self.validate(body.binding)
        if binding.config.get("business_bridge"):
            raise ValidationFailed("业务桥接绑定的是某个固定版本，不能用于全平台的环境配置")
        async with self.ctx.db.write() as s:
            current = await self._state(s, "test")
            if current["revision"] != body.expected_revision:
                raise Conflict("测试配置已被修改，请刷新后重试", {"current_revision": current["revision"]})
            return await self._append(s, "test", binding.model_dump(), "edit", body.reason, None)

    async def push(self, body: EnvironmentPush) -> dict[str, Any]:
        """把测试的当前配置复制为生产的新存档。"""
        async with self.ctx.db.write() as s:
            test = await self._state(s, "test")
            if test["revision"] != body.test_revision:
                raise Conflict("测试配置已被修改，请刷新后重试", {"current_revision": test["revision"]})
            reason = f"推送自测试存档 #{test['revision']}" + (f"：{body.reason}" if body.reason else "")
            return await self._append(s, "production", test["binding"], "push", reason,
                                      {"environment": "test", "revision": test["revision"]})

    async def rollback(self, body: EnvironmentRollback) -> dict[str, Any]:
        """生产回到某个历史存档：把它的配置追加为新存档，不改动历史。"""
        async with self.ctx.db.write() as s:
            target = await s.get(AdapterEnvironmentRevision, ("production", body.revision))
            if target is None:
                raise NotFound("生产存档不存在")
            reason = f"回滚到存档 #{body.revision}" + (f"：{body.reason}" if body.reason else "")
            return await self._append(s, "production", target.binding, "rollback", reason,
                                      {"environment": "production", "revision": body.revision})

    # ------------------------------------------------------------ 凭据

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
