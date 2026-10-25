"""持久化任务调度器（详设 8.3）。

任务存放在 ``jobs`` 表里：``pending`` -> ``running`` -> ``done | failed | cancelled``。worker 池（数量取设置
``scheduler.workers``）循环领取任务，领取时加锁；同一个 group（评测的 run、重放的会话）同时执行的任务数受
group 的并发上限约束；优先级高的先领，同优先级先到先得。

用法（给评测、重放等业务模块）::

    sched = ctx.svc(Scheduler)

    async def handle(job: Job) -> None:          # job.ref_id 是业务对象的 id
        ...                                      # 暂时性错误抛 Retriable，调度器按指数退避重试
        ...                                      # 其他异常 = 任务失败，不再重试
    async def group_info(group_id: str) -> GroupInfo | None:
        return GroupInfo(limit=8, paused=False)  # 该 group 的并发上限 / 是否暂停

    sched.register("replay_turn", handle, group_loader=group_info,
                   on_retry=..., on_fail=..., on_cancel=..., on_reset=...)   # 钩子都是可选的
    await sched.enqueue("replay_turn", ref_id, group_id=session_id, priority=10)   # 或 enqueue_many

``register(kind, handler, ...)`` 的参数：

- ``handler(job)``：执行一个任务。返回 = 成功；抛 :class:`Retriable` = 稍后重试（``attempts`` 未超过
  ``scheduler.max_attempts`` 时按 ``backoff_base * 2**(attempts-1)`` 秒退避，``Retriable(retry_after=…)``
  可覆盖）；
  抛其他异常 = 失败。``job.attempts`` 是含本次在内的领取次数。
- ``group_loader(group_id)``：返回 :class:`GroupInfo`（并发上限、是否暂停），结果缓存几秒；业务状态变化后
  调用 ``sched.group_changed(group_id)`` 使缓存失效并唤醒 worker。返回 ``None`` 表示不限。
- 钩子（都在调度器自己的事务之外执行，异常只记录日志）：``on_retry(job, exc)`` 在任务被安排重试之前；
  ``on_fail(job, exc)`` 在任务确定失败（含重试次数用尽）之前；``on_cancel(job)`` 任务被取消之后；
  ``on_reset(job)`` 任务因进程重启 / 停机被放回 pending 时，让业务把自己的状态一并重置。

没有注册 handler 的 kind 不会被领取（留在队列里，等注册之后再执行）。

重启恢复：``start()`` 先把 ``running`` 的任务重置为 ``pending``（不消耗重试次数），再调用 ``on_reset``
和启动钩子，
然后才开始领取任务。

取消：``cancel_group(group_id)`` 把该 group 未开始的任务置为 ``cancelled``，正在执行的任务所在的 asyncio
task 被取消。
暂停：由 ``group_loader`` 返回 ``paused=True`` 实现——暂停的 group 不再被领取，正在执行的任务继续跑完。

测试辅助：``await sched.wait_idle()`` 等待队列清空；``busy`` 是正在执行的任务数。
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update

from evalp.context import AppContext
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.services.settings import SettingsService
from evalp.store.models import Job as JobRow
from evalp.utils.time import now_iso, to_iso

log = get_logger(__name__)

DEFAULT_GRACE_S = 30.0
MAX_CLAIM_ROUNDS = 500


class Retriable(Exception):
    """可重试的暂时性错误（限流、超时、连接失败等）。``retry_after`` 为建议的等待秒数。"""

    def __init__(
        self, message: str = "", *, retry_after: float | None = None, code: str = "RETRIABLE"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.code = code


@dataclass(frozen=True)
class Job:
    """被领取的任务（只读快照）。"""

    id: str
    kind: str
    ref_id: str
    group_id: str | None
    priority: int
    attempts: int
    created_at: str


@dataclass(frozen=True)
class GroupInfo:
    """一个 group 的调度参数：同时执行数上限（None 不限）与是否暂停。"""

    limit: int | None = None
    paused: bool = False


Handler = Callable[[Job], Awaitable[None]]
GroupLoader = Callable[[str], Awaitable[GroupInfo | None]]
ErrorHook = Callable[[Job, BaseException], Awaitable[None]]
JobHook = Callable[[Job], Awaitable[None]]
StartupHook = Callable[[], Awaitable[None]]


@dataclass
class _Registration:
    kind: str
    handler: Handler
    group_loader: GroupLoader | None = None
    on_retry: ErrorHook | None = None
    on_fail: ErrorHook | None = None
    on_cancel: JobHook | None = None
    on_reset: JobHook | None = None


@dataclass(eq=False)
class _Running:
    job: Job
    task: asyncio.Task[None] | None = None
    reason: str | None = None  # 取消原因：cancel（业务取消）| shutdown（停机）


def _job_of(row: JobRow) -> Job:
    return Job(
        id=row.id,
        kind=row.kind,
        ref_id=row.ref_id,
        group_id=row.group_id,
        priority=row.priority,
        attempts=row.attempts,
        created_at=row.created_at,
    )


class Scheduler:
    """持久化任务调度器（服务单例：``ctx.svc(Scheduler)``）。"""

    close_order = -10  # AppContext.close 时最先关闭：先让任务收尾，再关闭会话服务等

    def __init__(
        self,
        ctx: AppContext,
        *,
        workers: int | None = None,
        poll_interval: float = 1.0,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        group_ttl: float = 5.0,
        grace: float = DEFAULT_GRACE_S,
    ) -> None:
        self.ctx = ctx
        self.workers_override = workers
        self.poll_interval = poll_interval
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.group_ttl = group_ttl
        self.grace = grace
        self._regs: dict[str, _Registration] = {}
        self._startup_hooks: list[StartupHook] = []
        self._claim_lock = asyncio.Lock()
        self._wake_gen = 0
        self._waiters: set[asyncio.Future[None]] = set()
        self._group_running: Counter[str] = Counter()
        self._group_cache: dict[str, tuple[GroupInfo, float]] = {}
        self._running: dict[str, _Running] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._target_workers = 0
        self._started = False
        self._stopping = False

    # ------------------------------------------------------------ 注册

    def register(
        self,
        kind: str,
        handler: Handler,
        *,
        group_loader: GroupLoader | None = None,
        on_retry: ErrorHook | None = None,
        on_fail: ErrorHook | None = None,
        on_cancel: JobHook | None = None,
        on_reset: JobHook | None = None,
    ) -> None:
        """注册某类任务的 handler 与钩子（见模块 docstring）。重复注册同一个 kind 会覆盖。"""
        self._regs[kind] = _Registration(kind, handler, group_loader, on_retry, on_fail, on_cancel, on_reset)

    def add_startup_hook(self, hook: StartupHook) -> None:
        """注册启动钩子：恢复完成、开始领取任务之前调用（例如业务层的对账）。"""
        self._startup_hooks.append(hook)

    # ------------------------------------------------------------ 状态

    @property
    def started(self) -> bool:
        return self._started and not self._stopping

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def busy(self) -> int:
        """正在执行任务的 worker 数。"""
        return len(self._running)

    @property
    def total_workers(self) -> int:
        return self._target_workers

    def running_in_group(self, group_id: str) -> int:
        return self._group_running[group_id]

    async def _max_attempts(self) -> int:
        return await self.ctx.svc(SettingsService).get("scheduler.max_attempts")

    async def _worker_count(self) -> int:
        if self.workers_override is not None:
            return self.workers_override
        return await self.ctx.svc(SettingsService).get("scheduler.workers")

    # ------------------------------------------------------------ 入队

    async def enqueue(
        self,
        kind: str,
        ref_id: str,
        *,
        group_id: str | None = None,
        priority: int = 0,
        run_after: str | None = None,
    ) -> str:
        """新增一个任务，返回任务 id，并唤醒 worker。"""
        ids = await self.enqueue_many(
            [(kind, ref_id)], group_id=group_id, priority=priority, run_after=run_after
        )
        return ids[0]

    async def enqueue_many(
        self,
        items: Iterable[tuple[str, str]],
        *,
        group_id: str | None = None,
        priority: int = 0,
        run_after: str | None = None,
        session: Any | None = None,
    ) -> list[str]:
        """批量新增任务 ``[(kind, ref_id), …]``（按给定顺序执行）。

        传入 ``session`` 时在调用方的事务里写入（调用方提交后需要自己 ``wake()``），否则自己开一个写事务。
        """
        rows = self.job_rows(items, group_id=group_id, priority=priority, run_after=run_after)
        if rows:
            if session is not None:
                await session.execute(insert(JobRow), rows)
            else:
                async with self.ctx.db.write() as s:
                    await s.execute(insert(JobRow), rows)
                self.wake()
        return [r["id"] for r in rows]

    @staticmethod
    def job_rows(
        items: Iterable[tuple[str, str]],
        *,
        group_id: str | None = None,
        priority: int = 0,
        run_after: str | None = None,
    ) -> list[dict[str, Any]]:
        """构造 jobs 表的行（调用方需要在自己的事务里批量插入时使用）。"""
        now = now_iso()
        return [
            {
                "id": new_id("job"),
                "kind": kind,
                "ref_id": ref_id,
                "group_id": group_id,
                "priority": priority,
                "status": "pending",
                "run_after": run_after or now,
                "attempts": 0,
                "last_error": None,
                "created_at": now,
                "updated_at": now,
            }
            for kind, ref_id in items
        ]

    def wake(self) -> None:
        """唤醒空闲的 worker（有新任务、group 状态变化时调用）。"""
        self._wake_gen += 1
        waiters, self._waiters = self._waiters, set()
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    async def _sleep_until_woken(self, seen_gen: int) -> None:
        """空闲等待：被 wake() 唤醒，或到 poll_interval 为止（领取期间发生的唤醒不会丢）。"""
        if self._wake_gen != seen_gen:
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.add(fut)
        try:
            await asyncio.wait_for(fut, self.poll_interval)
        except TimeoutError:
            pass
        finally:
            self._waiters.discard(fut)

    def group_changed(self, group_id: str) -> None:
        """group 的并发上限 / 暂停状态变了：丢弃缓存并唤醒 worker。"""
        self._group_cache.pop(group_id, None)
        self.wake()

    # ------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        """恢复遗留任务并启动 worker 池（幂等）。"""
        if self._started and not self._stopping:
            return
        self._stopping = False
        await self.recover()
        for hook in self._startup_hooks:
            try:
                await hook()
            except Exception as e:  # noqa: BLE001
                log.error("scheduler.startup_hook_failed", error=f"{type(e).__name__}: {e}")
        self._started = True
        await self.set_workers(await self._worker_count())
        log.info("scheduler.started", workers=self._target_workers)

    async def set_workers(self, n: int) -> None:
        """调整 worker 数量（增加立即生效；减少时多余的 worker 在手头任务结束后退出）。"""
        n = max(1, int(n))
        self._target_workers = n
        self._worker_tasks = [t for t in self._worker_tasks if not t.done()]
        for idx in range(len(self._worker_tasks), n):
            self._worker_tasks.append(asyncio.create_task(self._worker(idx), name=f"scheduler-worker-{idx}"))
        self.wake()

    async def apply_settings(self) -> None:
        """设置变更后调用：按最新的 ``scheduler.workers`` 调整 worker 数。"""
        if self.started:
            await self.set_workers(await self._worker_count())

    async def stop(self, *, grace: float | None = None) -> None:
        """停止：不再领取新任务；等待正在执行的任务最多 ``grace`` 秒，超时后取消它们（任务放回 pending）。"""
        if not self._started:
            return
        grace = self.grace if grace is None else grace
        self._stopping = True
        self.wake()
        running = [r.task for r in self._running.values() if r.task is not None]
        if running and grace > 0:
            await asyncio.wait(running, timeout=grace)
        for r in list(self._running.values()):
            if r.task is not None and not r.task.done():
                r.reason = "shutdown"
                r.task.cancel()
        workers, self._worker_tasks = self._worker_tasks, []
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._started = False
        log.info("scheduler.stopped")

    async def aclose(self) -> None:
        """AppContext 关闭时调用。"""
        await self.stop()

    # ------------------------------------------------------------ 恢复

    async def recover(self) -> int:
        """把 running 的任务重置为 pending（不消耗重试次数），并通知业务重置自己的状态。返回任务数。"""
        now = now_iso()
        async with self.ctx.db.write() as s:
            rows = (await s.execute(select(JobRow).where(JobRow.status == "running"))).scalars().all()
            jobs = [_job_of(r) for r in rows]
            for r in rows:
                r.status = "pending"
                r.attempts = max(r.attempts - 1, 0)
                r.last_error = "interrupted"
                r.updated_at = now
        for job in jobs:
            await self._run_hook("on_reset", job)
        if jobs:
            log.warning("scheduler.recovered_jobs", count=len(jobs))
            self.wake()
        return len(jobs)

    # ------------------------------------------------------------ 领取

    async def _group_info(self, kind: str, group_id: str) -> GroupInfo:
        cached = self._group_cache.get(group_id)
        now = time.monotonic()
        if cached is not None and now - cached[1] < self.group_ttl:
            return cached[0]
        reg = self._regs.get(kind)
        info: GroupInfo | None = None
        if reg is not None and reg.group_loader is not None:
            try:
                info = await reg.group_loader(group_id)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                log.error("scheduler.group_loader_failed", group_id=group_id, error=err)
        info = info or GroupInfo()
        self._group_cache[group_id] = (info, now)
        return info

    async def _claim(self) -> Job | None:
        """领取一个可执行的任务（加锁）：跳过暂停 / 已满的 group，按 priority 降序、run_after、created_at
        取第一个。"""
        async with self._claim_lock:
            if self._stopping or not self._regs:
                return None
            now = now_iso()
            excluded: set[str] = set()
            for _ in range(MAX_CLAIM_ROUNDS):
                stmt = select(JobRow).where(
                    JobRow.status == "pending", JobRow.run_after <= now, JobRow.kind.in_(list(self._regs))
                )
                if excluded:
                    stmt = stmt.where(JobRow.group_id.is_(None) | JobRow.group_id.not_in(excluded))
                stmt = stmt.order_by(JobRow.priority.desc(), JobRow.run_after, JobRow.created_at).limit(1)
                async with self.ctx.db.read() as s:
                    row = (await s.execute(stmt)).scalar_one_or_none()
                if row is None:
                    return None
                gid = row.group_id
                if gid is not None:
                    info = await self._group_info(row.kind, gid)
                    if info.paused or (info.limit is not None and self._group_running[gid] >= info.limit):
                        excluded.add(gid)
                        continue
                async with self.ctx.db.write() as s:
                    result = await s.execute(
                        update(JobRow)
                        .where(JobRow.id == row.id, JobRow.status == "pending")
                        .values(status="running", attempts=JobRow.attempts + 1, updated_at=now_iso())
                    )
                    claimed = result.rowcount == 1  # type: ignore[attr-defined]
                if not claimed:  # 刚被取消 / 被别处改了：重新选
                    continue
                if gid is not None:
                    self._group_running[gid] += 1
                return Job(
                    id=row.id,
                    kind=row.kind,
                    ref_id=row.ref_id,
                    group_id=gid,
                    priority=row.priority,
                    attempts=row.attempts + 1,
                    created_at=row.created_at,
                )
            return None

    # ------------------------------------------------------------ worker

    async def _worker(self, idx: int) -> None:
        while not self._stopping and idx < self._target_workers:
            seen_gen = self._wake_gen
            try:
                job = await self._claim()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("scheduler.claim_failed", error=f"{type(e).__name__}: {e}")
                await asyncio.sleep(min(self.poll_interval, 1.0))
                continue
            if job is None:
                await self._sleep_until_woken(seen_gen)
                continue
            await self._execute(job)

    async def _execute(self, job: Job) -> None:
        state = _Running(job)
        self._running[job.id] = state
        state.task = asyncio.create_task(self._run_job(job, state), name=f"job-{job.id}")
        try:
            await asyncio.shield(state.task)
        except asyncio.CancelledError:
            # worker 自己被取消（不应发生：停机时取消的是任务）；让任务收尾后再退出
            state.reason = state.reason or "shutdown"
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
            raise
        finally:
            self._running.pop(job.id, None)
            if job.group_id is not None:
                self._group_running[job.group_id] -= 1
                if self._group_running[job.group_id] <= 0:
                    del self._group_running[job.group_id]
            self.wake()

    async def _run_job(self, job: Job, state: _Running) -> None:
        reg = self._regs[job.kind]
        try:
            await reg.handler(job)
        except asyncio.CancelledError:
            if state.reason == "cancel":
                await asyncio.shield(self._finish(job, "cancelled", "cancelled"))
                await self._run_hook("on_cancel", job)
            else:
                await asyncio.shield(self._reset(job))
                await self._run_hook("on_reset", job)
            return  # 任务已处理完善后，吞掉取消，让 worker 正常继续 / 退出
        except Retriable as e:
            await self._on_retriable(reg, job, e)
        except Exception as e:  # noqa: BLE001
            log.error("scheduler.job_failed", job_id=job.id, kind=job.kind, error=f"{type(e).__name__}: {e}")
            await self._run_hook("on_fail", job, e)
            await self._finish(job, "failed", f"{type(e).__name__}: {e}")
        else:
            await self._finish(job, "done", None)

    async def _on_retriable(self, reg: _Registration, job: Job, e: Retriable) -> None:
        max_attempts = await self._max_attempts()
        if job.attempts >= max_attempts:
            log.warning("scheduler.job_gave_up", job_id=job.id, kind=job.kind, attempts=job.attempts)
            await self._run_hook("on_fail", job, e)
            await self._finish(job, "failed", f"{e.code}: {e.message}")
            return
        delay = e.retry_after if e.retry_after is not None else self.backoff(job.attempts)
        await self._run_hook("on_retry", job, e)
        run_after = to_iso(datetime.now(UTC) + timedelta(seconds=delay))
        async with self.ctx.db.write() as s:
            await s.execute(
                update(JobRow)
                .where(JobRow.id == job.id)
                .values(
                    status="pending",
                    run_after=run_after,
                    last_error=f"{e.code}: {e.message}"[:500],
                    updated_at=now_iso(),
                )
            )
        log.info(
            "scheduler.job_retry", job_id=job.id, kind=job.kind, attempts=job.attempts, delay=round(delay, 3)
        )

    def backoff(self, attempts: int) -> float:
        """第 ``attempts`` 次失败后的等待秒数：``backoff_base * 2**(attempts-1)``，封顶 ``backoff_max``。"""
        return min(self.backoff_max, self.backoff_base * (2 ** max(attempts - 1, 0)))

    async def _finish(self, job: Job, status: str, error: str | None) -> None:
        async with self.ctx.db.write() as s:
            await s.execute(
                update(JobRow)
                .where(JobRow.id == job.id)
                .values(status=status, last_error=error[:500] if error else None, updated_at=now_iso())
            )

    async def _reset(self, job: Job) -> None:
        """任务被中断（停机）：放回 pending，不消耗重试次数。"""
        async with self.ctx.db.write() as s:
            await s.execute(
                update(JobRow)
                .where(JobRow.id == job.id)
                .values(
                    status="pending",
                    attempts=max(job.attempts - 1, 0),
                    last_error="interrupted",
                    updated_at=now_iso(),
                )
            )

    async def _run_hook(self, name: str, job: Job, exc: BaseException | None = None) -> None:
        reg = self._regs.get(job.kind)
        hook = getattr(reg, name, None) if reg is not None else None
        if hook is None:
            return
        try:
            await (hook(job, exc) if exc is not None else hook(job))
        except Exception as e:  # noqa: BLE001
            log.error("scheduler.hook_failed", hook=name, job_id=job.id, error=f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 取消

    async def cancel_group(self, group_id: str) -> int:
        """取消一个 group：未开始的任务置为 cancelled；正在执行的任务被取消。返回被取消的任务数。"""
        async with self.ctx.db.write() as s:
            result = await s.execute(
                update(JobRow)
                .where(JobRow.group_id == group_id, JobRow.status == "pending")
                .values(status="cancelled", updated_at=now_iso())
            )
            n = int(result.rowcount or 0)  # type: ignore[attr-defined]
        running = [r for r in self._running.values() if r.job.group_id == group_id]
        for r in running:
            r.reason = "cancel"
            if r.task is not None:
                r.task.cancel()
        self.group_changed(group_id)
        return n + len(running)

    async def cancel_jobs(self, kind: str, ref_ids: Iterable[str]) -> int:
        """按业务对象 id 取消单个任务（未开始的置为 cancelled，正在执行的被取消）。"""
        ids = set(ref_ids)
        if not ids:
            return 0
        async with self.ctx.db.write() as s:
            result = await s.execute(
                update(JobRow)
                .where(JobRow.kind == kind, JobRow.ref_id.in_(ids), JobRow.status == "pending")
                .values(status="cancelled", updated_at=now_iso())
            )
            n = int(result.rowcount or 0)  # type: ignore[attr-defined]
        for r in list(self._running.values()):
            if r.job.kind == kind and r.job.ref_id in ids and r.task is not None:
                r.reason = "cancel"
                r.task.cancel()
                n += 1
        return n

    # ------------------------------------------------------------ 辅助（测试 / 运维）

    async def pending_count(self, group_id: str | None = None) -> int:
        async with self.ctx.db.read() as s:
            stmt = select(func.count()).select_from(JobRow).where(JobRow.status == "pending")
            if group_id is not None:
                stmt = stmt.where(JobRow.group_id == group_id)
            return int((await s.execute(stmt)).scalar_one())

    async def wait_idle(
        self, timeout: float = 60.0, *, group_id: str | None = None, include_paused: bool = False
    ) -> None:
        """等待队列清空：没有正在执行的任务，也没有待执行的任务（含退避中的）。

        默认忽略属于"暂停"的 group 的待执行任务（它们不会被领取，等不到）；``include_paused=True`` 时一起等。
        超时抛 ``TimeoutError``。
        """
        deadline = time.monotonic() + timeout
        while True:
            if not self._busy_in(group_id) and not await self._has_runnable(group_id, include_paused):
                # 再确认一遍，避免恰好在任务交接的间隙里误判
                await asyncio.sleep(0.02)
                if not self._busy_in(group_id) and not await self._has_runnable(group_id, include_paused):
                    return
            if time.monotonic() > deadline:
                raise TimeoutError(f"调度器在 {timeout}s 内没有清空队列")
            await asyncio.sleep(0.02)

    def _busy_in(self, group_id: str | None) -> bool:
        if group_id is None:
            return bool(self._running)
        return any(r.job.group_id == group_id for r in self._running.values())

    async def _has_runnable(self, group_id: str | None, include_paused: bool) -> bool:
        async with self.ctx.db.read() as s:
            stmt = select(JobRow.group_id, JobRow.kind).where(JobRow.status == "pending").distinct()
            if group_id is not None:
                stmt = stmt.where(JobRow.group_id == group_id)
            groups = (await s.execute(stmt)).all()
        for gid, kind in groups:
            if kind not in self._regs:
                continue
            if gid is None or include_paused:
                return True
            self._group_cache.pop(gid, None)  # 等待期间以库里的最新状态为准
            if not (await self._group_info(kind, gid)).paused:
                return True
        return False

    def stats(self) -> dict[str, Any]:
        """运行状态：worker 数、忙碌数、各 group 正在执行的任务数。"""
        return {
            "workers": self._target_workers,
            "busy": self.busy,
            "groups": dict(self._group_running),
            "started": self.started,
        }
