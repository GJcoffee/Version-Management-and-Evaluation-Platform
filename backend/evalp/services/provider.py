"""Provider 服务：CRUD、密钥管理、连通性测试。"""

from __future__ import annotations

import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import Conflict, NotFound
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.schemas.provider import (
    ModelInfo,
    ProviderCreate,
    ProviderOut,
    ProviderTestResult,
    ProviderUpdate,
)
from evalp.secrets import provider_secret_key
from evalp.store.models import Provider
from evalp.utils.time import now_iso

log = get_logger(__name__)

TEST_TIMEOUT_S = 10.0


def to_out(p: Provider) -> ProviderOut:
    """ORM -> DTO（不含密钥）。"""
    return ProviderOut(
        id=p.id,
        name=p.name,
        base_url=p.base_url,
        has_api_key=bool(p.api_key_ref),
        headers=p.headers or {},
        models=[ModelInfo(**m) for m in (p.models or [])],
        rpm_limit=p.rpm_limit,
        tpm_limit=p.tpm_limit,
        max_concurrency=p.max_concurrency,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _models_json(models: list[ModelInfo]) -> list[dict[str, Any]]:
    return [m.model_dump() for m in models]


class ProviderService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def list(self, *, limit: int = 50, cursor: str | None = None) -> Page[ProviderOut]:
        """分页列出 Provider（最新在前）。"""
        async with self.ctx.db.read() as s:
            return await paginate(
                s, select(Provider), Provider.id, limit=limit, cursor=cursor, transform=to_out
            )

    async def get(self, provider_id: str) -> ProviderOut:
        """按 id 获取；不存在抛 NotFound。"""
        async with self.ctx.db.read() as s:
            return to_out(await self._get_orm(s, provider_id))

    async def find_by_name(self, name: str) -> ProviderOut | None:
        """按名称查找；不存在返回 None。"""
        async with self.ctx.db.read() as s:
            p = (await s.execute(select(Provider).where(Provider.name == name))).scalar_one_or_none()
            return to_out(p) if p else None

    async def get_by_name(self, name: str) -> ProviderOut:
        """按名称获取（Spec 通过 name 引用 Provider）；不存在抛 NotFound。"""
        found = await self.find_by_name(name)
        if found is None:
            raise NotFound(f"Provider 不存在: {name}", {"name": name})
        return found

    async def get_api_key(self, provider_id: str) -> str | None:
        """取出明文密钥（仅供 LLM 客户端等内部使用，不要写入响应或日志）。"""
        async with self.ctx.db.read() as s:
            p = await self._get_orm(s, provider_id)
            ref = p.api_key_ref
        if not ref:
            return None
        return await self.ctx.secrets.get(ref)

    @staticmethod
    async def _get_orm(s: Any, provider_id: str) -> Provider:
        p = await s.get(Provider, provider_id)
        if p is None:
            raise NotFound(f"Provider 不存在: {provider_id}", {"id": provider_id})
        return p

    async def create(self, body: ProviderCreate) -> ProviderOut:
        pid = new_id("prv")
        now = now_iso()
        ref = provider_secret_key(pid) if body.api_key else None
        secret_written = False
        try:
            async with self.ctx.db.write() as s:
                p = Provider(
                    id=pid,
                    name=body.name,
                    base_url=body.base_url,
                    api_key_ref=ref,
                    headers=body.headers,
                    models=_models_json(body.models),
                    rpm_limit=body.rpm_limit,
                    tpm_limit=body.tpm_limit,
                    max_concurrency=body.max_concurrency,
                    created_at=now,
                    updated_at=now,
                )
                s.add(p)
                await s.flush()
                if ref and body.api_key:
                    await self.ctx.secrets.set(ref, body.api_key)
                    secret_written = True
                out = to_out(p)
        except IntegrityError:
            if secret_written and ref:
                await self.ctx.secrets.delete(ref)
            raise Conflict(f"Provider 名称已存在: {body.name}", {"name": body.name}) from None
        log.info("provider.created", provider_id=pid, name=body.name)
        return out

    async def update(self, provider_id: str, body: ProviderUpdate) -> ProviderOut:
        fields = body.model_fields_set
        async with self.ctx.db.write() as s:
            p = await self._get_orm(s, provider_id)
            for key in ("name", "base_url", "headers", "rpm_limit", "tpm_limit", "max_concurrency"):
                if key not in fields:
                    continue
                value = getattr(body, key)
                if value is None and key in ("name", "base_url", "headers", "max_concurrency"):
                    continue  # 这些字段不允许置空，null 视为未提供
                setattr(p, key, value)
            if "models" in fields and body.models is not None:
                p.models = _models_json(body.models)
            if "api_key" in fields and body.api_key is not None:
                if body.api_key == "":
                    if p.api_key_ref:
                        await self.ctx.secrets.delete(p.api_key_ref)
                    p.api_key_ref = None
                else:
                    ref = provider_secret_key(p.id)
                    await self.ctx.secrets.set(ref, body.api_key)
                    p.api_key_ref = ref
            p.updated_at = now_iso()
            try:
                await s.flush()
            except IntegrityError:
                raise Conflict(f"Provider 名称已存在: {body.name}", {"name": body.name}) from None
            out = to_out(p)
        log.info("provider.updated", provider_id=provider_id, fields=sorted(fields - {"api_key"}))
        self._invalidate_llm()
        return out

    async def delete(self, provider_id: str) -> None:
        async with self.ctx.db.write() as s:
            p = await self._get_orm(s, provider_id)
            ref = p.api_key_ref
            await s.delete(p)
        if ref:
            await self.ctx.secrets.delete(ref)
        log.info("provider.deleted", provider_id=provider_id)
        self._invalidate_llm()

    def _invalidate_llm(self) -> None:
        """Provider 配置变化后立即让 LLM 客户端缓存失效（重建客户端以应用新的 base_url/密钥）。"""
        from evalp.runtime.llm.registry import LLMRegistry

        self.ctx.svc(LLMRegistry).invalidate(rebuild=True)

    async def test(self, provider_id: str) -> ProviderTestResult:
        """调用 ``GET {base_url}/models`` 检查连通性与密钥（超时 10s）。"""
        async with self.ctx.db.read() as s:
            p = await self._get_orm(s, provider_id)
            base_url, extra_headers, ref = p.base_url, dict(p.headers or {}), p.api_key_ref
        headers: dict[str, str] = {}
        api_key = await self.ctx.secrets.get(ref) if ref else None
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(extra_headers)
        url = f"{base_url.rstrip('/')}/models"

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=TEST_TIMEOUT_S) as client:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return ProviderTestResult(
                ok=False, latency_ms=_ms(start), error=f"请求超时（>{TEST_TIMEOUT_S:.0f}s）"
            )
        except httpx.HTTPError as e:
            return ProviderTestResult(
                ok=False, latency_ms=_ms(start), error=_scrub(f"{type(e).__name__}: {e}", api_key)
            )
        latency = _ms(start)
        if resp.status_code >= 400:
            snippet = _scrub(resp.text[:200].strip(), api_key)
            return ProviderTestResult(
                ok=False,
                latency_ms=latency,
                error=f"HTTP {resp.status_code}" + (f": {snippet}" if snippet else ""),
            )
        models: list[str] | None = None
        try:
            data = resp.json().get("data")
            if isinstance(data, list):
                models = [str(m["id"]) for m in data if isinstance(m, dict) and "id" in m]
        except (ValueError, AttributeError):
            pass
        return ProviderTestResult(ok=True, latency_ms=latency, models=models)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _scrub(text: str, secret: str | None) -> str:
    """错误信息中去掉密钥。"""
    return text.replace(secret, "***") if secret else text
