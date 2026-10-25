"""Provider 级限流器（详设 6.8）。

每个 Provider 一个 :class:`RateLimiter`，同时约束：

- 并发：同时进行的请求数 ≤ ``max_concurrency``；
- RPM：令牌桶，容量 ``rpm_limit``，每分钟补充 ``rpm_limit`` 个，每次请求消耗 1 个；
- TPM：令牌桶，容量 ``tpm_limit``，请求前按估算值预扣，响应后按实际 usage 多退少补（可能出现负值，
  表示"欠账"，之后的请求会等到补回为止）；
- 优先级：等待者按优先级排队，**数值越大越先获得**，同优先级先到先得。

只看队首等待者（严格按优先级顺序放行），因此高优先级的请求不会被低优先级的小请求插队，
也不会让需要大量 token 的请求饿死。限额为 None 表示不限。

用法::

    async with limiter.slot(est_tokens=800, priority=PRIORITY_INTERACTIVE) as permit:
        resp = await call_model()
        permit.set_actual(resp.usage.total)      # 不调用则按预扣值结算

限额来自 ``limits_source``（异步函数，通常读取 ProviderService），每次 ``acquire`` 时按 ``ttl`` 秒的
短缓存刷新，因此在设置里修改限额后很快生效；读取失败时沿用旧值。
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from evalp.logging import get_logger

log = get_logger(__name__)

# 优先级常量：交互（对话 / 双栏）> 重放 > 评测
PRIORITY_INTERACTIVE = 100
PRIORITY_REPLAY = 50
PRIORITY_EVAL = 10


@dataclass(frozen=True)
class Limits:
    """限额。None 表示不限。"""

    rpm_limit: int | None = None
    tpm_limit: int | None = None
    max_concurrency: int = 8


LimitsSource = Callable[[], Awaitable[Limits]]


class _Bucket:
    """令牌桶：容量 = 每分钟额度，匀速补充。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.tokens = float(limit)
        self.updated = time.monotonic()

    @property
    def rate(self) -> float:
        """每秒补充量。"""
        return self.limit / 60.0

    def refill(self, now: float) -> None:
        if now > self.updated:
            self.tokens = min(float(self.limit), self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def resize(self, limit: int, now: float) -> None:
        self.refill(now)
        if limit > self.limit:
            self.tokens += limit - self.limit  # 调高限额立即获得相应余量
        self.limit = limit
        self.tokens = min(self.tokens, float(limit))

    def wait_for(self, amount: float) -> float:
        """还需要等多久才能凑够 amount（秒），已足够返回 0。"""
        missing = amount - self.tokens
        return 0.0 if missing <= 0 else missing / self.rate


class _Waiter:
    __slots__ = ("est", "fut", "priority", "seq")

    def __init__(self, priority: int, seq: int, est: int, fut: asyncio.Future[Permit]) -> None:
        self.priority = priority
        self.seq = seq
        self.est = est
        self.fut = fut

    def __lt__(self, other: _Waiter) -> bool:
        return (-self.priority, self.seq) < (-other.priority, other.seq)


class Permit:
    """一次已获得的放行许可；必须 ``release``（或使用 ``async with limiter.slot``）。"""

    def __init__(self, limiter: RateLimiter, est_tokens: int) -> None:
        self._limiter = limiter
        self.est_tokens = est_tokens
        self.actual_tokens: int | None = None
        self.queued_ms = 0
        self._released = False

    def set_actual(self, tokens: int) -> None:
        """记录实际消耗的 token 数（prompt + completion），释放时据此多退少补。"""
        self.actual_tokens = max(0, int(tokens))

    def release(self, actual_tokens: int | None = None) -> None:
        """释放并结算（幂等）。actual_tokens 为空时用 set_actual 的值，仍为空则按预扣值结算。"""
        if self._released:
            return
        self._released = True
        if actual_tokens is not None:
            self.actual_tokens = max(0, int(actual_tokens))
        self._limiter._release(self)


class _Slot:
    def __init__(self, limiter: RateLimiter, est_tokens: int, priority: int) -> None:
        self._limiter, self._est, self._priority = limiter, est_tokens, priority
        self._permit: Permit | None = None

    async def __aenter__(self) -> Permit:
        self._permit = await self._limiter.acquire(self._est, self._priority)
        return self._permit

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._permit is not None:
            self._permit.release()


class RateLimiter:
    """单个 Provider 的限流器。"""

    def __init__(
        self,
        name: str,
        limits_source: LimitsSource | None = None,
        *,
        limits: Limits | None = None,
        ttl: float = 2.0,
    ) -> None:
        self.name = name
        self._source = limits_source
        self._ttl = ttl
        self._loaded_at = float("-inf") if limits_source is not None else float("inf")
        self._refreshing = False
        self.limits = limits or Limits()
        self._rpm: _Bucket | None = None
        self._tpm: _Bucket | None = None
        self._sync_buckets(time.monotonic())
        self._inflight = 0
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()
        self._timer: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ 限额

    def set_limits(self, limits: Limits) -> None:
        """直接设置限额（立即生效）。"""
        self.limits = limits
        self._sync_buckets(time.monotonic())
        self._pump()

    def _sync_buckets(self, now: float) -> None:
        for attr, limit in (("_rpm", self.limits.rpm_limit), ("_tpm", self.limits.tpm_limit)):
            bucket: _Bucket | None = getattr(self, attr)
            if limit is None:
                setattr(self, attr, None)
            elif bucket is None:
                setattr(self, attr, _Bucket(limit))
            elif bucket.limit != limit:
                bucket.resize(limit, now)

    async def refresh(self, *, force: bool = False) -> None:
        """按 ttl 从 limits_source 刷新限额；读取失败沿用旧值。"""
        if self._source is None or self._refreshing:
            return
        if not force and time.monotonic() - self._loaded_at < self._ttl:
            return
        self._refreshing = True
        try:
            new = await self._source()
        except Exception as e:  # 数据库暂时不可读等
            log.warning("ratelimit.refresh_failed", provider=self.name, error=str(e))
        else:
            self._loaded_at = time.monotonic()
            if new != self.limits:
                self.set_limits(new)
        finally:
            self._refreshing = False

    # ------------------------------------------------------------------ 获取 / 释放

    def slot(self, est_tokens: int = 0, priority: int = PRIORITY_EVAL) -> _Slot:
        """``async with limiter.slot(...) as permit`` 的便捷写法。"""
        return _Slot(self, est_tokens, priority)

    async def acquire(self, est_tokens: int = 0, priority: int = PRIORITY_EVAL) -> Permit:
        """等待并获得放行许可。优先级高的先获得；取消时自动退出队列。"""
        await self.refresh()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Permit] = loop.create_future()
        est = max(0, int(est_tokens))
        waiter = _Waiter(priority, next(self._seq), est, fut)
        started = time.monotonic()
        heapq.heappush(self._waiters, waiter)
        self._pump()
        try:
            permit = await fut
        except BaseException:
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                fut.result().release(0)  # 取消与放行竞争：把资源还回去
            else:
                fut.cancel()
                self._pump()  # 队首可能已变化
            raise
        permit.queued_ms = int((time.monotonic() - started) * 1000)
        return permit

    def _effective_est(self, est: int) -> int:
        tpm = self.limits.tpm_limit
        return est if tpm is None else min(est, tpm)  # 单个请求超过桶容量时按容量计，避免永远等不到

    def _pump(self) -> None:
        """尽可能多地放行队首等待者，并为下一次令牌补充设置定时器。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        now = time.monotonic()
        for b in (self._rpm, self._tpm):
            if b is not None:
                b.refill(now)
        delay: float | None = None
        while self._waiters:
            w = self._waiters[0]
            if w.fut.done():  # 已取消
                heapq.heappop(self._waiters)
                continue
            if self._inflight >= self.limits.max_concurrency:
                break  # 等待 release
            est = self._effective_est(w.est)
            wait = 0.0
            if self._rpm is not None:
                wait = max(wait, self._rpm.wait_for(1))
            if self._tpm is not None:
                wait = max(wait, self._tpm.wait_for(est))
            if wait > 0:
                delay = wait
                break
            if self._rpm is not None:
                self._rpm.tokens -= 1
            if self._tpm is not None:
                self._tpm.tokens -= est
            self._inflight += 1
            heapq.heappop(self._waiters)
            w.fut.set_result(Permit(self, est))
        if delay is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._timer = loop.call_later(max(delay, 0.001), self._pump)

    def _release(self, permit: Permit) -> None:
        self._inflight = max(0, self._inflight - 1)
        if self._tpm is not None:
            actual = permit.est_tokens if permit.actual_tokens is None else permit.actual_tokens
            self._tpm.refill(time.monotonic())
            self._tpm.tokens = min(float(self._tpm.limit), self._tpm.tokens + permit.est_tokens - actual)
        self._pump()

    # ------------------------------------------------------------------ 状态

    def status(self) -> dict[str, Any]:
        """当前状态：inflight、waiting、rpm_left / tpm_left（不限时为 None）。"""
        now = time.monotonic()
        for b in (self._rpm, self._tpm):
            if b is not None:
                b.refill(now)
        return {
            "inflight": self._inflight,
            "waiting": sum(1 for w in self._waiters if not w.fut.done()),
            "rpm_left": None if self._rpm is None else max(0, int(self._rpm.tokens)),
            "tpm_left": None if self._tpm is None else max(0, int(self._tpm.tokens)),
            "max_concurrency": self.limits.max_concurrency,
            "rpm_limit": self.limits.rpm_limit,
            "tpm_limit": self.limits.tpm_limit,
        }

    @property
    def inflight(self) -> int:
        return self._inflight

    def close(self) -> None:
        """取消定时器与全部等待者（关闭应用时调用）。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for w in self._waiters:
            if not w.fut.done():
                w.fut.cancel()
        self._waiters.clear()
