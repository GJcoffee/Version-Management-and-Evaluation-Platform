"""持久化任务调度器（详设 8.3）：并发 / 优先级 / 暂停 / 取消 / 退避重试 / 重启恢复。"""

from __future__ import annotations

import asyncio
import time

import pytest
from evalp.ids import new_id
from evalp.runtime.scheduler import GroupInfo, Job, Retriable, Scheduler
from evalp.services.settings import SettingsService
from evalp.store.models import Job as JobRow
from evalp.utils.time import now_iso
from sqlalchemy import select

KIND = "t_job"


@pytest.fixture
async def sched(ctx):
    """独立的调度器实例（不用应用单例），轮询 / 退避都很短；测试结束时停止。"""
    s = Scheduler(
        ctx, workers=4, poll_interval=0.02, backoff_base=0.05, backoff_max=0.5, group_ttl=0.05, grace=2
    )
    yield s
    await s.stop(grace=0)


class Probe:
    """记录并发、执行顺序与调用次数。"""

    def __init__(self, hold: float = 0.05) -> None:
        self.hold = hold
        self.cur = 0
        self.max = 0
        self.by_group: dict[str, int] = {}
        self.max_by_group: dict[str, int] = {}
        self.order: list[str] = []
        self.calls: dict[str, int] = {}

    async def handle(self, job: Job) -> None:
        self.cur += 1
        self.max = max(self.max, self.cur)
        g = job.group_id or "-"
        self.by_group[g] = self.by_group.get(g, 0) + 1
        self.max_by_group[g] = max(self.max_by_group.get(g, 0), self.by_group[g])
        self.order.append(job.ref_id)
        self.calls[job.ref_id] = self.calls.get(job.ref_id, 0) + 1
        try:
            await asyncio.sleep(self.hold)
        finally:
            self.cur -= 1
            self.by_group[g] -= 1


async def statuses(ctx, kind=KIND) -> dict[str, str]:
    async with ctx.db.read() as s:
        rows = (await s.execute(select(JobRow.ref_id, JobRow.status).where(JobRow.kind == kind))).all()
    return dict(rows)


async def test_worker_pool_size_limits_concurrency(ctx, sched):
    probe = Probe()
    sched.register(KIND, probe.handle)
    await sched.enqueue_many([(KIND, f"j{i}") for i in range(12)])
    await sched.start()
    assert sched.total_workers == 4
    await sched.wait_idle()
    assert probe.max == 4 and len(probe.calls) == 12
    assert set((await statuses(ctx)).values()) == {"done"}


async def test_workers_follow_settings_and_can_be_resized(ctx):
    s = Scheduler(ctx, poll_interval=0.02)
    await ctx.svc(SettingsService).update({"scheduler.workers": 3})
    probe = Probe()
    s.register(KIND, probe.handle)
    await s.start()
    try:
        assert s.total_workers == 3
        await ctx.svc(SettingsService).update({"scheduler.workers": 6})
        await s.apply_settings()
        assert s.total_workers == 6
        await s.enqueue_many([(KIND, f"j{i}") for i in range(12)])
        await s.wait_idle()
        assert probe.max == 6
    finally:
        await s.stop(grace=0)


async def test_group_concurrency_limit(ctx, sched):
    probe = Probe()
    limits = {"g2": 2, "g1": 1}

    async def loader(gid: str) -> GroupInfo:
        return GroupInfo(limit=limits[gid])

    sched.register(KIND, probe.handle, group_loader=loader)
    await sched.set_workers(8)
    await sched.enqueue_many([(KIND, f"a{i}") for i in range(8)], group_id="g2")
    await sched.enqueue_many([(KIND, f"b{i}") for i in range(4)], group_id="g1")
    await sched.start()
    await sched.wait_idle()
    assert probe.max_by_group == {"g2": 2, "g1": 1}
    assert probe.max == 3  # 两个 group 同时跑，互不挤占
    assert len(probe.calls) == 12


async def test_priority_then_fifo(ctx, sched):
    probe = Probe(hold=0.01)
    sched.register(KIND, probe.handle)
    sched.workers_override = 1
    await sched.enqueue_many([(KIND, "low1"), (KIND, "low2")], priority=0)
    await sched.enqueue_many([(KIND, "hi1"), (KIND, "hi2")], priority=10)
    await sched.enqueue_many([(KIND, "mid")], priority=5)
    await sched.start()
    await sched.wait_idle()
    assert probe.order == ["hi1", "hi2", "mid", "low1", "low2"]


async def test_pause_and_resume_a_group(ctx, sched):
    probe = Probe(hold=0.01)
    state = {"paused": True}

    async def loader(gid: str) -> GroupInfo:
        return GroupInfo(paused=state["paused"] and gid == "p")

    sched.register(KIND, probe.handle, group_loader=loader)
    await sched.enqueue_many([(KIND, f"p{i}") for i in range(5)], group_id="p")
    await sched.enqueue_many([(KIND, f"o{i}") for i in range(3)], group_id="other")
    await sched.start()
    await sched.wait_idle(timeout=5)  # 默认不等暂停的 group
    assert sorted(probe.calls) == ["o0", "o1", "o2"]  # 暂停的 group 没有被领取，别的 group 不受影响
    assert await sched.pending_count("p") == 5
    state["paused"] = False
    sched.group_changed("p")
    await sched.wait_idle(timeout=5, include_paused=True)
    assert len(probe.calls) == 8 and await sched.pending_count() == 0


async def test_cancel_group_cancels_running_task_and_pending_jobs(ctx, sched):
    started = asyncio.Event()
    seen: dict[str, str] = {}
    cancelled_hook: list[str] = []

    async def handle(job: Job) -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen[job.ref_id] = "cancelled"
            raise

    async def on_cancel(job: Job) -> None:
        cancelled_hook.append(job.ref_id)

    async def loader(gid: str) -> GroupInfo:
        return GroupInfo(limit=1)

    sched.register(KIND, handle, group_loader=loader, on_cancel=on_cancel)
    await sched.enqueue_many([(KIND, f"j{i}") for i in range(4)], group_id="g")
    await sched.start()
    await asyncio.wait_for(started.wait(), 5)
    assert sched.busy == 1
    n = await sched.cancel_group("g")
    assert n == 4  # 1 个在跑 + 3 个排队
    await sched.wait_idle(timeout=5)
    assert seen == {"j0": "cancelled"} and cancelled_hook == ["j0"]
    assert set((await statuses(ctx)).values()) == {"cancelled"}
    assert sched.busy == 0


async def test_retriable_backs_off_exponentially_then_succeeds(ctx, sched):
    stamps: list[float] = []
    retried: list[int] = []

    async def handle(job: Job) -> None:
        stamps.append(time.monotonic())
        if job.attempts < 3:
            raise Retriable("限流", code="RATE_LIMITED")

    async def on_retry(job: Job, exc: BaseException) -> None:
        retried.append(job.attempts)

    sched.register(KIND, handle, on_retry=on_retry)
    sched.backoff_base = 0.1
    await sched.enqueue(KIND, "x")
    await sched.start()
    await sched.wait_idle(timeout=10)
    assert len(stamps) == 3 and retried == [1, 2]
    gap1, gap2 = stamps[1] - stamps[0], stamps[2] - stamps[1]
    assert gap1 >= 0.1 and gap2 >= 0.2 and gap2 > gap1  # 0.1s、0.2s：指数退避
    async with ctx.db.read() as s:
        row = (await s.execute(select(JobRow).where(JobRow.ref_id == "x"))).scalar_one()
    assert row.status == "done" and row.attempts == 3


async def test_retry_after_overrides_backoff(ctx, sched):
    stamps: list[float] = []

    async def handle(job: Job) -> None:
        stamps.append(time.monotonic())
        if job.attempts == 1:
            raise Retriable("稍后", retry_after=0.3)

    sched.register(KIND, handle)
    await sched.enqueue(KIND, "x")
    await sched.start()
    await sched.wait_idle(timeout=10)
    assert stamps[1] - stamps[0] >= 0.3


async def test_gives_up_after_max_attempts(ctx, sched):
    await ctx.svc(SettingsService).update({"scheduler.max_attempts": 3})
    failed: list[tuple[int, str]] = []
    calls = 0

    async def handle(job: Job) -> None:
        nonlocal calls
        calls += 1
        raise Retriable("一直限流", code="RATE_LIMITED")

    async def on_fail(job: Job, exc: BaseException) -> None:
        failed.append((job.attempts, getattr(exc, "code", "")))

    sched.register(KIND, handle, on_fail=on_fail)
    await sched.enqueue(KIND, "x")
    await sched.start()
    await sched.wait_idle(timeout=10)
    assert calls == 3 and failed == [(3, "RATE_LIMITED")]
    assert (await statuses(ctx)) == {"x": "failed"}


async def test_non_retriable_error_fails_immediately(ctx, sched):
    failed: list[str] = []

    async def handle(job: Job) -> None:
        raise ValueError("坏数据")

    async def on_fail(job: Job, exc: BaseException) -> None:
        failed.append(str(exc))

    sched.register(KIND, handle, on_fail=on_fail)
    await sched.enqueue(KIND, "x")
    await sched.start()
    await sched.wait_idle(timeout=10)
    assert failed == ["坏数据"]
    async with ctx.db.read() as s:
        row = (await s.execute(select(JobRow))).scalar_one()
    assert row.status == "failed" and row.attempts == 1 and "ValueError" in row.last_error


async def test_unregistered_kind_is_left_alone(ctx, sched):
    probe = Probe(hold=0)
    sched.register(KIND, probe.handle)
    await sched.enqueue_many([("replay_turn", "r1")])
    await sched.enqueue(KIND, "x")
    await sched.start()
    await sched.wait_idle(timeout=5)
    assert (await statuses(ctx, "replay_turn")) == {"r1": "pending"}
    # 之后注册 handler，任务才会被领取（replay 模块的用法）
    later = Probe(hold=0)
    sched.register("replay_turn", later.handle)
    sched.wake()
    await sched.wait_idle(timeout=5)
    assert later.calls == {"r1": 1}


async def test_restart_recovery_resets_running_jobs(ctx):
    """进程崩溃遗留的 running 任务：启动时放回 pending（不消耗重试次数）、调用 on_reset，然后重新执行。"""
    now = now_iso()
    async with ctx.db.write() as s:
        for ref, status in (("crashed", "running"), ("waiting", "pending"), ("finished", "done")):
            s.add(
                JobRow(
                    id=new_id("job"),
                    kind=KIND,
                    ref_id=ref,
                    group_id="g",
                    status=status,
                    attempts=1,
                    run_after=now,
                    created_at=now,
                    updated_at=now,
                )
            )
    probe, resets = Probe(hold=0), []
    sched = Scheduler(ctx, workers=2, poll_interval=0.02)

    async def on_reset(job: Job) -> None:
        resets.append(job.ref_id)

    sched.register(KIND, probe.handle, on_reset=on_reset)
    try:
        await sched.start()
        await sched.wait_idle(timeout=5)
    finally:
        await sched.stop(grace=0)
    assert resets == ["crashed"]
    assert sorted(probe.calls) == ["crashed", "waiting"]  # 已完成的不会重跑
    async with ctx.db.read() as s:
        row = (await s.execute(select(JobRow).where(JobRow.ref_id == "crashed"))).scalar_one()
    assert row.status == "done" and row.attempts == 1  # 恢复没有消耗重试次数（本次领取后为 1）


async def test_stop_cancels_stragglers_and_requeues_them(ctx):
    started = asyncio.Event()
    resets: list[str] = []

    async def handle(job: Job) -> None:
        started.set()
        await asyncio.sleep(30)

    async def on_reset(job: Job) -> None:
        resets.append(job.ref_id)

    sched = Scheduler(ctx, workers=1, poll_interval=0.02)
    sched.register(KIND, handle, on_reset=on_reset)
    await sched.enqueue_many([(KIND, "slow"), (KIND, "queued")])
    await sched.start()
    await asyncio.wait_for(started.wait(), 5)
    t0 = time.monotonic()
    await sched.stop(grace=0.2)  # 宽限期过后取消在跑的任务
    assert time.monotonic() - t0 < 5 and not sched.started and sched.busy == 0
    assert resets == ["slow"]
    st = await statuses(ctx)
    assert st == {"slow": "pending", "queued": "pending"}  # 都留到下次启动
    async with ctx.db.read() as s:
        attempts = (await s.execute(select(JobRow.attempts).where(JobRow.ref_id == "slow"))).scalar_one()
    assert attempts == 0


async def test_stop_waits_for_running_job_within_grace(ctx):
    started = asyncio.Event()

    async def handle(job: Job) -> None:
        started.set()
        await asyncio.sleep(0.2)

    sched = Scheduler(ctx, workers=1, poll_interval=0.02)
    sched.register(KIND, handle)
    await sched.enqueue(KIND, "x")
    await sched.start()
    await asyncio.wait_for(started.wait(), 5)
    await sched.stop(grace=5)
    assert (await statuses(ctx)) == {"x": "done"}  # 优雅等待：任务跑完


async def test_busy_is_visible_in_health(ctx, client):
    sched = ctx.svc(Scheduler)
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(job: Job) -> None:
        started.set()
        await release.wait()

    sched.register(KIND, handle)
    await sched.enqueue(KIND, "x")
    await asyncio.wait_for(started.wait(), 5)
    h = (await client.get("/api/v1/health")).json()
    assert h["workers"]["busy"] == 1 and h["queue"]["running"] == 1
    release.set()
    await sched.wait_idle(timeout=5)
    assert (await client.get("/api/v1/health")).json()["workers"]["busy"] == 0
