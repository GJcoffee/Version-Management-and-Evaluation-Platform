"""LLMRegistry：按 Provider 名称持有 AsyncOpenAI 客户端与限流器（``ctx.svc(LLMRegistry)``）。

- 每个 Provider 一个 ``AsyncOpenAI`` 实例，复用连接；base_url / headers / 密钥变化后（按
  ``updated_at`` 判断，带 ``cache_ttl`` 秒的短缓存）自动重建客户端，限流器保持不变；
- 限额（rpm / tpm / max_concurrency）每次 ``get()`` 时按同一缓存刷新，设置里改完很快生效；
- 测试可以注入 httpx 传输层，让 AsyncOpenAI 走 ``ASGITransport`` 之类的内存传输::

      registry = ctx.svc(LLMRegistry)
      registry.set_transport(httpx.ASGITransport(app=fake_app))

用法::

    llm = ctx.svc(LLMRegistry).llm          # LLMClient
    status = await ctx.svc(LLMRegistry).status("openai")   # {inflight, rpm_left, tpm_left, ...}
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI

from evalp.context import AppContext
from evalp.errors import NotFound, SpecInvalid
from evalp.logging import get_logger
from evalp.runtime.llm.client import LLMClient
from evalp.runtime.llm.pricing import find_model
from evalp.runtime.llm.ratelimit import Limits, RateLimiter
from evalp.schemas.provider import ModelInfo, ProviderOut
from evalp.services.provider import ProviderService

log = get_logger(__name__)

PLACEHOLDER_KEY = "EMPTY"  # 本地模型服务不需要密钥，但 SDK 要求非空


@dataclass
class ProviderHandle:
    """一个 Provider 的运行时句柄。"""

    name: str
    info: ProviderOut
    client: AsyncOpenAI
    limiter: RateLimiter
    api_key: str | None
    fingerprint: tuple[Any, ...]
    http_client: httpx.AsyncClient | None = None
    supports_stream_usage: bool = True  # 服务端拒绝 stream_options 后置为 False
    checked_at: float = field(default_factory=time.monotonic)

    def model_info(self, model: str) -> ModelInfo | None:
        return find_model(self.info.models, model)

    async def aclose(self) -> None:
        await self.client.close()
        if self.http_client is not None:
            await self.http_client.aclose()


def _limits_of(info: ProviderOut) -> Limits:
    return Limits(rpm_limit=info.rpm_limit, tpm_limit=info.tpm_limit, max_concurrency=info.max_concurrency)


def _fingerprint(info: ProviderOut) -> tuple[Any, ...]:
    return (info.id, info.base_url, tuple(sorted(info.headers.items())), info.has_api_key, info.updated_at)


class LLMRegistry:
    """Provider 客户端与限流器的注册表（服务单例）。"""

    def __init__(self, ctx: AppContext, *, cache_ttl: float = 2.0, request_timeout: float = 120.0) -> None:
        self.ctx = ctx
        self.cache_ttl = cache_ttl
        self.request_timeout = request_timeout
        self._handles: dict[str, ProviderHandle] = {}
        self._limiters: dict[str, RateLimiter] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._retired: list[ProviderHandle] = []
        self._transport: httpx.AsyncBaseTransport | None = None
        self._llm: LLMClient | None = None

    # -- 对外

    @property
    def llm(self) -> LLMClient:
        """共享的 LLMClient。"""
        if self._llm is None:
            self._llm = LLMClient(self, request_timeout=self.request_timeout)
        return self._llm

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        """注入 httpx 传输层（测试用）；之后构建的客户端都走它。已有客户端会在下次使用时重建。"""
        self._transport = transport
        self.invalidate(rebuild=True)

    def invalidate(self, name: str | None = None, *, rebuild: bool = False) -> None:
        """让缓存的 Provider 信息过期（rebuild=True 时连客户端一并丢弃重建）。"""
        names = [name] if name else list(self._handles)
        for n in names:
            h = self._handles.get(n)
            if h is None:
                continue
            if rebuild:
                self._retired.append(self._handles.pop(n))
            else:
                h.checked_at = float("-inf")

    async def get(self, name: str) -> ProviderHandle:
        """取得 Provider 句柄（必要时重新读取配置）。Provider 不存在抛 SpecInvalid。"""
        h = self._handles.get(name)
        if h is not None and time.monotonic() - h.checked_at < self.cache_ttl:
            return h
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            h = self._handles.get(name)
            if h is not None and time.monotonic() - h.checked_at < self.cache_ttl:
                return h
            providers = self.ctx.svc(ProviderService)
            try:
                info = await providers.get_by_name(name)
            except NotFound:
                if h is not None:
                    self._retired.append(self._handles.pop(name))
                raise SpecInvalid(f"Provider 不存在: {name}", {"provider": name}) from None
            limiter = self._limiters.get(name)
            if limiter is None:
                limiter = self._limiters[name] = RateLimiter(name, limits=_limits_of(info))
            else:
                limiter.set_limits(_limits_of(info))
            fp = _fingerprint(info)
            if h is not None and h.fingerprint == fp:
                h.info, h.checked_at = info, time.monotonic()
                return h
            api_key = await providers.get_api_key(info.id) if info.has_api_key else None
            if h is not None:
                self._retired.append(h)  # 旧客户端可能还有进行中的请求，推迟到 aclose 时关闭
            new = self._build(name, info, api_key, limiter, fp)
            self._handles[name] = new
            return new

    async def limiter(self, name: str) -> RateLimiter:
        """取得（并刷新限额的）限流器。"""
        return (await self.get(name)).limiter

    async def status(self, name: str) -> dict[str, Any]:
        """限流器状态：inflight、waiting、rpm_left、tpm_left 等。Provider 不存在抛 SpecInvalid。"""
        return (await self.get(name)).limiter.status()

    def statuses(self) -> dict[str, dict[str, Any]]:
        """已创建的全部限流器的状态（同步，不刷新限额）。"""
        return {n: lim.status() for n, lim in self._limiters.items()}

    async def aclose(self) -> None:
        """关闭全部客户端与限流器。"""
        for lim in self._limiters.values():
            lim.close()
        handles = [*self._handles.values(), *self._retired]
        self._handles.clear()
        self._retired.clear()
        for h in handles:
            try:
                await h.aclose()
            except Exception as e:
                log.warning("llm.close_failed", provider=h.name, error=str(e))

    # -- 内部

    def _build(
        self,
        name: str,
        info: ProviderOut,
        api_key: str | None,
        limiter: RateLimiter,
        fingerprint: tuple[Any, ...],
    ) -> ProviderHandle:
        http_client: httpx.AsyncClient | None = None
        if self._transport is not None:
            http_client = httpx.AsyncClient(transport=self._transport, timeout=self.request_timeout)
        client = AsyncOpenAI(
            base_url=info.base_url,
            api_key=api_key or PLACEHOLDER_KEY,
            default_headers=dict(info.headers),
            max_retries=0,  # 重试由 LLMClient 统一控制
            timeout=self.request_timeout,
            http_client=http_client,
        )
        return ProviderHandle(
            name=name,
            info=info,
            client=client,
            limiter=limiter,
            api_key=api_key,
            fingerprint=fingerprint,
            http_client=http_client,
        )
