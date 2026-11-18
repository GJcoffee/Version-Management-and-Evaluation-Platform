"""观测运行时凭据管理，负责存放和按连接范围使用账号密钥。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import Conflict, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.schemas.account import normalize_origin
from evalp.schemas.key_connection import KeyConnectionCreate, KeyConnectionUpdate
from evalp.store.models import TraceKeyConnection
from evalp.utils.time import now_iso

if TYPE_CHECKING:
    from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig


class KeyConnectionService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, connection_id: str) -> asyncio.Lock:
        return self._locks.setdefault(connection_id, asyncio.Lock())

    @staticmethod
    def output(row: TraceKeyConnection) -> dict:
        return {k: getattr(row, k) for k in (
            "id", "name", "origin", "top_url", "workspace_id", "credential_source", "revision",
            "enabled", "status", "last_checked_at", "last_error", "created_at", "updated_at",
        )}

    async def get(self, connection_id: str) -> TraceKeyConnection:
        async with self.ctx.db.read() as s:
            row = await s.get(TraceKeyConnection, connection_id)
        if row is None:
            raise NotFound("密钥连接不存在")
        return row

    async def list(self) -> list[dict]:
        async with self.ctx.db.read() as s:
            rows = (await s.execute(select(TraceKeyConnection).order_by(
                TraceKeyConnection.created_at.desc(),
            ))).scalars()
            return [self.output(r) for r in rows]

    async def create(self, body: KeyConnectionCreate) -> dict:
        connection_id, stamp = new_id("key"), now_iso()
        ref = f"trace-key:{connection_id}:1" if body.credential_source == "key" else None
        if ref:
            await self.ctx.secrets.set(ref, json.dumps({
                "access_key_id": body.access_key_id.get_secret_value().strip(),
                "secret_access_key": body.access_key_secret.get_secret_value().strip(),
            }))
        try:
            async with self.ctx.db.write() as s:
                row = TraceKeyConnection(
                    id=connection_id, name=body.name, origin=body.origin, top_url=body.top_url,
                    workspace_id=body.workspace_id, credential_source=body.credential_source,
                    secret_ref=ref, revision=1, enabled=True, status="pending",
                    created_at=stamp, updated_at=stamp,
                )
                s.add(row)
        except BaseException:
            if ref:
                await self.ctx.secrets.delete(ref)
            raise
        return await self.check(connection_id)

    async def update(self, connection_id: str, body: KeyConnectionUpdate) -> dict:
        async with self.lock(connection_id):
            row = await self.get(connection_id)
            if row.revision != body.expected_revision:
                raise Conflict("密钥连接已更新，请重新加载")
            replace = body.access_key_id is not None
            if replace and row.credential_source != "key":
                raise ValidationFailed("本机认证连接请通过 hicli 更新密钥，或新建独立密钥连接")
            ref = f"trace-key:{connection_id}:{row.revision + 1}" if replace else None
            if ref:
                await self.ctx.secrets.set(ref, json.dumps({
                    "access_key_id": body.access_key_id.get_secret_value().strip(),
                    "secret_access_key": body.access_key_secret.get_secret_value().strip(),
                }))
            try:
                async with self.ctx.db.write() as s:
                    row = await s.get(TraceKeyConnection, connection_id)
                    if body.name is not None:
                        row.name = body.name.strip()
                    if body.enabled is not None:
                        row.enabled = body.enabled
                    if ref:
                        row.secret_ref = ref
                    row.revision += 1
                    row.status = "pending" if row.enabled else "disabled"
                    row.last_error = None
                    row.updated_at = now_iso()
            except BaseException:
                if ref:
                    await self.ctx.secrets.delete(ref)
                raise
        return await self.check(connection_id) if row.enabled else self.output(row)

    async def query(self, connection_id: str, config: HiAgentConfig, action: str,
                    body: dict[str, Any]) -> tuple[dict, dict]:
        from evalp.runtime.adapters.platforms.hiagent.hicli_observe import (
            HicliObserveError,
            query_hicli_trace,
        )

        async with self.lock(connection_id):
            row = await self.get(connection_id)
            if not row.enabled:
                raise HicliObserveError("密钥连接已停用，请在设置中启用或更换")
            if (not config.observe_base_url or normalize_origin(config.observe_base_url) != row.origin
                    or config.workspace_id != row.workspace_id
                    or config.observe_hicli_base_url != row.top_url):
                raise HicliObserveError("密钥连接的站点、接口地址或工作空间与当前配置不匹配")
            identity = None
            if row.credential_source == "key":
                value = await self.ctx.secrets.get(row.secret_ref)
                if not value:
                    raise HicliObserveError("密钥连接的凭据不可用，请在设置中更换")
                try:
                    identity = json.loads(value)
                except (ValueError, TypeError):
                    raise HicliObserveError("密钥连接的凭据格式无效，请重新保存") from None
                if not isinstance(identity, dict):
                    raise HicliObserveError("密钥连接的凭据格式无效，请重新保存")
            result = await query_hicli_trace(config, action, body, identity=identity)
            return result, {"transport": "hicli", "key_connection_ref": row.id,
                            "config_revision": row.revision, "credential_source": row.credential_source}

    async def check(self, connection_id: str) -> dict:
        from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
        from evalp.runtime.adapters.platforms.hiagent.hicli_observe import HicliObserveError

        row = await self.get(connection_id)
        if not row.enabled:
            return self.output(row)
        config = HiAgentConfig(
            base_url=row.origin, app_id="auth-check", user_id="auth-check", published_version="auth-check",
            observe_base_url=row.origin, observe_hicli_base_url=row.top_url, workspace_id=row.workspace_id,
            observe_transport="hicli", observe_key_ref=row.id,
        )
        status, error = "active", None
        try:
            await self.query(row.id, config, "GetTraceInfo", {"MessageID": "evalp-auth-check"})
        except (HicliObserveError, TimeoutError, OSError):
            status, error = "check_failed", "密钥认证检查未通过，请检查密钥、接口地址、网络和观测权限"
        async with self.lock(connection_id):
            async with self.ctx.db.write() as s:
                latest = await s.get(TraceKeyConnection, connection_id)
                # 较早的检查不能覆盖并发的密钥轮换或停用结果。
                if latest.revision == row.revision and latest.enabled:
                    latest.status, latest.last_error = status, error
                    latest.last_checked_at = latest.updated_at = now_iso()
                return self.output(latest)
