"""RateLimiter：并发、RPM、TPM（预扣与结算）、优先级、限额热更新。"""

import asyncio
import time

import pytest
from evalp.runtime.llm.ratelimit import (
    PRIORITY_EVAL,
    PRIORITY_INTERACTIVE,
    PRIORITY_REPLAY,
    Limits,
    RateLimiter,
)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_unlimited_status():
    lim = RateLimiter("p", limits=Limits(max_concurrency=4))
    p = await lim.acquire(100)
    st = lim.status()
    assert st["inflight"] == 1 and st["rpm_left"] is None and st["tpm_left"] is None
    p.release()
    assert lim.status()["inflight"] == 0


async def test_concurrency_limit():
    lim = RateLimiter("p", limits=Limits(max_concurrency=2))
    a = await lim.acquire()
    b = await lim.acquire()
    third = asyncio.create_task(lim.acquire())
    await settle()
    assert not third.done() and lim.status()["waiting"] == 1 and lim.inflight == 2
    a.release()
    c = await asyncio.wait_for(third, 1)
    assert lim.inflight == 2
    b.release()
    c.release()
    assert lim.inflight == 0
    a.release()  # 幂等
    assert lim.inflight == 0


async def test_slot_context_manager_releases_on_error():
    lim = RateLimiter("p", limits=Limits(max_concurrency=1))
    with pytest.raises(RuntimeError):
        async with lim.slot(0):
            assert lim.inflight == 1
            raise RuntimeError("x")
    assert lim.inflight == 0


async def test_rpm_token_bucket():
    lim = RateLimiter("p", limits=Limits(rpm_limit=300, max_concurrency=1000))  # 5 个 / 秒，容量 300
    for _ in range(300):
        (await lim.acquire()).release()
    assert lim.status()["rpm_left"] <= 1
    start = time.monotonic()
    (await asyncio.wait_for(lim.acquire(), 3)).release()
    assert 0.1 < time.monotonic() - start < 1.0  # 约 0.2s 补一个


async def test_rpm_left_decreases():
    lim = RateLimiter("p", limits=Limits(rpm_limit=60, max_concurrency=10))
    for _ in range(5):
        await lim.acquire()
    assert 54 <= lim.status()["rpm_left"] <= 55


async def test_tpm_prededuct_and_refund():
    lim = RateLimiter("p", limits=Limits(tpm_limit=60000, max_concurrency=10))
    p = await lim.acquire(30000)
    assert 29900 <= lim.status()["tpm_left"] <= 30100  # 预扣
    p.release(actual_tokens=1000)  # 实际只用了 1000，多退
    assert 58900 <= lim.status()["tpm_left"] <= 59300


async def test_tpm_release_without_actual_keeps_estimate():
    lim = RateLimiter("p", limits=Limits(tpm_limit=60000, max_concurrency=10))
    (await lim.acquire(30000)).release()
    assert lim.status()["tpm_left"] <= 30500


async def test_tpm_overspend_is_charged_and_blocks_next():
    lim = RateLimiter("p", limits=Limits(tpm_limit=60000, max_concurrency=10))  # 1000 token / 秒
    p = await lim.acquire(1000)
    p.set_actual(60500)  # 实际远超预扣：补扣
    p.release()
    assert lim.status()["tpm_left"] == 0
    start = time.monotonic()
    (await asyncio.wait_for(lim.acquire(300), 3)).release(0)  # 欠账 ~500 + 需要 300，约等 0.8s
    assert 0.4 < time.monotonic() - start < 2.0


async def test_tpm_blocks_until_refill():
    lim = RateLimiter("p", limits=Limits(tpm_limit=60000, max_concurrency=10))
    await lim.acquire(60000)
    start = time.monotonic()
    await asyncio.wait_for(lim.acquire(300), 3)  # 需要等 0.3s
    assert 0.15 < time.monotonic() - start < 1.0


async def test_oversized_request_is_clamped_not_stuck():
    lim = RateLimiter("p", limits=Limits(tpm_limit=1200, max_concurrency=10))
    p = await asyncio.wait_for(lim.acquire(10**6), 1)
    p.release(100)


async def test_priority_order():
    lim = RateLimiter("p", limits=Limits(max_concurrency=1))
    holder = await lim.acquire()
    order: list[str] = []

    async def worker(name: str, prio: int):
        permit = await lim.acquire(0, prio)
        order.append(name)
        await asyncio.sleep(0)
        permit.release()

    tasks = []
    for name, prio in [("eval1", PRIORITY_EVAL), ("replay", PRIORITY_REPLAY), ("ui1", PRIORITY_INTERACTIVE),
                       ("eval2", PRIORITY_EVAL), ("ui2", PRIORITY_INTERACTIVE)]:  # fmt: skip
        tasks.append(asyncio.create_task(worker(name, prio)))
        await settle()
    assert lim.status()["waiting"] == 5
    holder.release()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert order == ["ui1", "ui2", "replay", "eval1", "eval2"]  # 优先级高的先；同级先到先得


async def test_cancelled_waiter_leaves_queue_and_does_not_leak():
    lim = RateLimiter("p", limits=Limits(max_concurrency=1))
    holder = await lim.acquire()
    t1 = asyncio.create_task(lim.acquire())
    t2 = asyncio.create_task(lim.acquire())
    await settle()
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    assert lim.status()["waiting"] == 1
    holder.release()
    p2 = await asyncio.wait_for(t2, 1)
    assert lim.inflight == 1
    p2.release()
    assert lim.inflight == 0


async def test_limits_source_refresh_with_ttl():
    current = Limits(rpm_limit=None, max_concurrency=1)
    calls = 0

    async def source():
        nonlocal calls
        calls += 1
        return current

    lim = RateLimiter("p", source, ttl=0.2)
    (await lim.acquire()).release()
    (await lim.acquire()).release()
    assert calls == 1  # 缓存期内不重复读取
    current = Limits(rpm_limit=120, tpm_limit=6000, max_concurrency=3)
    await asyncio.sleep(0.25)
    (await lim.acquire()).release()
    assert calls == 2
    st = lim.status()
    assert st["max_concurrency"] == 3 and st["rpm_limit"] == 120 and st["rpm_left"] is not None


async def test_raising_concurrency_wakes_waiters():
    lim = RateLimiter("p", limits=Limits(max_concurrency=1))
    await lim.acquire()
    t = asyncio.create_task(lim.acquire())
    await settle()
    assert not t.done()
    lim.set_limits(Limits(max_concurrency=2))
    await asyncio.wait_for(t, 1)


async def test_limits_source_failure_keeps_old_limits():
    ok = True

    async def source():
        if not ok:
            raise RuntimeError("db down")
        return Limits(max_concurrency=5)

    lim = RateLimiter("p", source, ttl=0)
    (await lim.acquire()).release()
    ok = False
    (await lim.acquire()).release()
    assert lim.limits.max_concurrency == 5
