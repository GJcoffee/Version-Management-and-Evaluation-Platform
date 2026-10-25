"""验收 A4（详设 16.2）：200 条用例 × 3 次重复、并发 16 的评测；限流退避；崩溃重启后续跑。"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from evalp.config import load_config
from evalp.context import AppContext
from evalp.runtime.llm.registry import LLMRegistry
from evalp.runtime.scheduler import Scheduler
from evalp.services.eval import EvalService, start_eval_runtime
from evalp.services.session import SessionService
from evalp.store.models import EvalItem, Job as JobRow, Session
from sqlalchemy import func, select
from tests.fixtures.fake_openai import StreamingASGITransport

from .conftest import API, make_cases
from .test_eval_service import wait_until


@pytest.fixture
async def big_provider(ev):
    """eval 并发 16 > 默认的 Provider 并发 8：调高 Provider 的并发上限，才能观察到 16。"""
    listing = (await ev.client.get(f"{API}/providers")).json()["items"]
    pid = next(p["id"] for p in listing if p["name"] == "fake")
    r = await ev.client.patch(f"{API}/providers/{pid}", json={"max_concurrency": 32})
    assert r.status_code == 200, r.text


async def test_a4_200_cases_x3_repeats_concurrency_16(ev, big_provider):
    n = 200
    v = await ev.config_version()
    await ev.dataset("big", make_cases(n))
    ev.fake.latency = 0.05
    # 每 10 个用例里第 0 个：模型总是答错（20 个）；i % 25 == 1 的用例（8 个）：只有第一次答对，另外两次答错
    seen: dict[str, int] = {}

    def answer(req):
        q = req.last_user
        i = int(q.removeprefix("问题"))
        seen[q] = seen.get(q, 0) + 1
        if i % 10 == 0:
            return "我不知道"
        if i % 25 == 1 and seen[q] > 1:
            return "答错了"
        return f"echo: {q}"

    ev.fake.add_rule(reply=answer)
    await ev.sched.set_workers(16)

    created = await ev.create(v, "asset://dataset/big@latest", repeats=3, concurrency=16)
    rid = created.runs[0].id
    assert created.estimate["items"] == 600
    d = await ev.evals.wait_finished(rid, timeout=240, interval=0.1)

    assert d.status == "completed" and d.concurrency == 16
    assert d.progress["total"] == 600 and d.progress["done"] == 600
    assert (
        d.progress["pending"] == d.progress["running"] == d.progress["scoring"] == d.progress["failed"] == 0
    )
    assert 2 <= ev.fake.max_inflight <= 16  # 确实并行了，且不超过 Run 的并发上限
    assert len(ev.fake.chat_requests) == 600

    st = d.stats
    assert st["cases"] == 200 and st["items"] == 600 and st["done"] == 600 and st["cached"] == 0
    fully_wrong, flaky = 20, 8
    assert st["flaky_cases"] == flaky
    assert st["pass_rate"] == pytest.approx((200 - fully_wrong - flaky + flaky / 3) / 200)
    assert st["pass_at_k"]["1"] == pytest.approx(st["pass_rate"])
    assert st["pass_at_k"]["3"] == pytest.approx((200 - fully_wrong) / 200)  # 重复 3 次才有 pass@3
    assert "5" not in st["pass_at_k"] and "10" not in st["pass_at_k"]
    em = st["evaluators"]["exact_match"]
    assert em["n"] == 600 and em["errors"] == 0
    assert em["pass_rate"] == pytest.approx((600 - fully_wrong * 3 - flaky * 2) / 600)
    assert st["by_tag"]["even"]["cases"] == 100 and st["by_tag"]["odd"]["cases"] == 100
    assert st["latency_ms"]["p50"] >= 20 and st["latency_ms"]["p95"] >= st["latency_ms"]["p50"]
    assert st["tokens"]["prompt"] > 0 and st["error_rate"] == 0.0 and st["score_error_rate"] == 0.0
    items = await ev.items(rid, status="done")
    assert len(items) == 600
    assert st["cost"] == pytest.approx(sum(i.cost for i in items)) and st["cost_new"] == st["cost"]
    assert st["cost"] == pytest.approx(d.progress["cost_new"])
    flaky_items = await ev.items(rid, **{"cls": "flaky"})
    assert len(flaky_items) == flaky * 3


async def test_a4_rate_limit_backs_off_and_finally_succeeds(ev, big_provider):
    """模型持续 429：agent 的 LLM 层重试用尽后 Item 以可重试错误失败，调度器按退避重试，最终成功。"""
    from evalp.runtime.llm.client import RetryPolicy

    ev.ctx.svc(LLMRegistry).llm.retry = RetryPolicy(max_retries=1, base_delay=0.01, max_delay=0.02)
    v = await ev.config_version()
    await ev.dataset("rl", make_cases(6))
    ev.fake.fail(429, times=14, retry_after=0.01, message="rate limited")
    d, _ = await ev.run(v, "asset://dataset/rl@latest", concurrency=3)
    assert d.status == "completed" and d.stats["failed"] == 0 and d.stats["pass_rate"] == 1.0
    items = await ev.items(d.id)
    retried = [i for i in items if i.attempts > 1]
    assert retried, "应该有 Item 经历过调度器级别的重试"
    for it in retried:
        detail = await ev.evals.get_item(d.id, it.id)
        fa = detail.usage["failed_attempts"]
        assert len(fa) == it.attempts - 1 and fa[0]["error"]["code"]
    # 计费：每个 Item 只记最终成功的那次；失败的尝试不计入 Item 成本，只出现在 cost_wasted
    costs = {round(i.cost, 8) for i in items}
    assert len(costs) == 1
    assert d.stats["cost_new"] == pytest.approx(6 * costs.pop())
    assert d.stats["cost_wasted"] == pytest.approx(0.0)  # 429 的请求没有产生费用
    assert len(ev.fake.chat_requests) == 14 + 6  # 14 次被拒 + 6 次成功


async def test_a4_gives_up_after_max_attempts_and_can_retry_failed(ev, big_provider):
    from evalp.runtime.llm.client import RetryPolicy
    from evalp.services.settings import SettingsService

    ev.ctx.svc(LLMRegistry).llm.retry = RetryPolicy(max_retries=0, base_delay=0.01, max_delay=0.02)
    await ev.ctx.svc(SettingsService).update({"scheduler.max_attempts": 2})
    v = await ev.config_version()
    await ev.dataset("rl", make_cases(2))
    ev.fake.fail(429, times=None, retry_after=0.01)  # 一直限流
    d, _ = await ev.run(v, "asset://dataset/rl@latest")
    assert d.status == "completed_with_errors" and d.stats["failed"] == 2
    items = await ev.items(d.id)
    assert all(i.status == "failed" and i.attempts == 2 and i.error["retriable"] is True for i in items)
    assert d.stats["pass_rate"] == 0.0 and d.stats["error_rate"] == 1.0
    ev.fake.clear_faults()
    assert (await ev.evals.retry_failed(d.id)) == {"retried": 2}
    d2 = await ev.evals.wait_finished(d.id, timeout=30, interval=0.05)
    assert d2.status == "completed" and d2.stats["pass_rate"] == 1.0


def snapshot_data_dir(ctx, dest):
    """在持有写锁（没有写入进行）时复制数据目录，模拟进程在这一刻崩溃。"""
    src = ctx.config.data_dir
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.log", "logs", "*-shm"))


async def test_a4_restart_resumes_without_rerun_or_double_billing(ev, tmp_path, big_provider):
    total = 40
    v = await ev.config_version()
    await ev.dataset("rs", make_cases(total))
    ev.fake.latency = 0.15
    await ev.sched.set_workers(6)
    created = await ev.create(v, "asset://dataset/rs@latest", concurrency=6)
    rid = created.runs[0].id

    async def partly_done():
        p = await ev.evals.progress(rid)
        return p["done"] >= 10 and p["running"] > 0

    await wait_until(partly_done, timeout=60)
    dest = tmp_path / "crashed"
    async with ev.ctx.db.write_lock:  # 此刻没有写事务：拿到一个一致的"崩溃现场"
        snapshot_data_dir(ev.ctx, dest)
    # 旧进程"死亡"：停止它的调度器，不再有请求发往模型
    await ev.sched.stop(grace=0)
    await asyncio.sleep(0.3)
    base_requests = len(ev.fake.chat_requests)

    ctx2 = await AppContext.create(load_config(dest))
    try:
        async with ctx2.db.read() as s:
            statuses = dict(
                (await s.execute(select(EvalItem.status, func.count()).group_by(EvalItem.status))).all()
            )
            running_jobs = (
                await s.execute(select(func.count()).select_from(JobRow).where(JobRow.status == "running"))
            ).scalar_one()
        assert statuses.get("running", 0) > 0 and running_jobs >= statuses["running"]
        already = statuses.get("done", 0) + statuses.get("scoring", 0)
        assert already >= 10
        to_run = total - already  # 崩溃时 pending / running 的 Item 需要（重新）执行

        ctx2.svc(LLMRegistry).set_transport(StreamingASGITransport(ev.fake.app))
        ev.fake.latency = 0.0
        sched2 = ctx2.svc(Scheduler)
        sched2.poll_interval, sched2.backoff_base = 0.05, 0.05
        sched2.workers_override = 8
        await start_eval_runtime(ctx2)
        svc2 = ctx2.svc(EvalService)
        d = await svc2.wait_finished(rid, timeout=60, interval=0.05)
        await sched2.stop(grace=5)

        assert d.status == "completed" and d.progress["done"] == total and d.stats["pass_rate"] == 1.0
        # 已经完成的 Item 没有重跑：重启后发给模型的请求数 == 需要执行的 Item 数
        assert len(ev.fake.chat_requests) - base_requests == to_run
        async with ctx2.db.read() as s:
            costs = (await s.execute(select(EvalItem.cost).where(EvalItem.run_id == rid))).scalars().all()
            abandoned = (
                await s.execute(select(func.count()).select_from(Session).where(Session.status == "error"))
            ).scalar_one()
        # 不重复计费：每个 Item 只记一次成功执行的成本，所以全部相等，汇总 = 40 × 单次
        assert len({round(c, 8) for c in costs}) == 1 and d.stats["cost"] == pytest.approx(total * costs[0])
        assert d.progress["cost_new"] == pytest.approx(total * costs[0])
        # 崩溃时在跑的 Item 留下的废弃会话被标记为 error，浪费的成本单独记账
        assert abandoned == statuses["running"]
        assert d.stats["cost_wasted"] >= 0
        items = await svc2.list_items(rid, limit=200)
        interrupted = [i for i in items.items if i.usage and i.usage.get("failed_attempts")]
        assert len(interrupted) == statuses["running"]
        assert all(i.usage["failed_attempts"][0]["error"]["code"] == "INTERRUPTED" for i in interrupted)
        assert isinstance(ctx2.svc(SessionService), SessionService)
    finally:
        await ctx2.close()
