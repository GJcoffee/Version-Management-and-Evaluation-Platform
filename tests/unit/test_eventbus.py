import asyncio

import pytest
from evalp.runtime.eventbus import HEARTBEAT, EventBus, SubscriptionClosed


async def test_multiple_subscribers_and_topics():
    bus = EventBus()
    a1, a2, b = bus.subscribe("session:a"), bus.subscribe("session:a"), bus.subscribe("session:b")
    assert bus.publish("session:a", {"n": 1}) == 2
    assert bus.publish("session:a", {"n": 2}) == 2
    assert bus.publish("session:none", 1) == 0
    assert [await a1.get(), await a1.get()] == [{"n": 1}, {"n": 2}]
    assert [await a2.get(), await a2.get()] == [{"n": 1}, {"n": 2}]
    assert b.qsize() == 0
    assert bus.subscriber_count() == 3 and bus.subscriber_count("session:a") == 2


async def test_async_iteration_and_close():
    bus = EventBus()
    sub = bus.subscribe("t")
    got = []

    async def consume():
        async for ev in sub:
            got.append(ev)

    task = asyncio.create_task(consume())
    for i in range(3):
        bus.publish("t", i)
    await asyncio.sleep(0.01)
    sub.close()
    await asyncio.wait_for(task, 1)  # close 后迭代结束
    assert got == [0, 1, 2]
    assert bus.subscriber_count() == 0
    assert bus.publish("t", 99) == 0
    sub.close()  # 幂等


async def test_close_delivers_buffered_events_first():
    bus = EventBus()
    sub = bus.subscribe("t")
    bus.publish("t", "a")
    sub.close()
    assert await sub.get() == "a"
    with pytest.raises(SubscriptionClosed):
        await sub.get()


async def test_queue_full_disconnects_only_slow_subscriber():
    bus = EventBus(capacity=1000)
    slow, fast = bus.subscribe("t"), bus.subscribe("t")
    drained = []
    for i in range(1000):
        bus.publish("t", i)
        drained.append(await fast.get())
    assert not slow.closed and slow.qsize() == 1000
    bus.publish("t", 1000)  # 第 1001 条：slow 队列已满 -> 断开
    assert slow.closed and slow.overflowed
    assert not fast.closed and not fast.overflowed
    assert await fast.get() == 1000
    assert bus.subscriber_count("t") == 1
    # 被断开的订阅者仍能读完已缓冲的 1000 条，然后迭代结束
    items = [ev async for ev in slow]
    assert items == list(range(1000))
    # 之后的发布不再投递给它
    bus.publish("t", 1001)
    assert slow.qsize() == 0


async def test_default_capacity_is_1000():
    from evalp.runtime.eventbus import QUEUE_CAPACITY

    assert QUEUE_CAPACITY == 1000 and EventBus().capacity == 1000


async def test_heartbeat_and_timeout():
    bus = EventBus()
    sub = bus.subscribe("t")
    assert await sub.get_with_heartbeat(0.02) is HEARTBEAT
    with pytest.raises(asyncio.TimeoutError):
        await sub.get(0.01)
    bus.publish("t", "x")
    assert await sub.get_with_heartbeat(1) == "x"


async def test_async_with_unsubscribes():
    bus = EventBus()
    async with bus.subscribe("t") as sub:
        assert bus.subscriber_count("t") == 1
        bus.publish("t", 1)
        assert await sub.get() == 1
    assert bus.subscriber_count() == 0


async def test_bus_close_ends_all():
    bus = EventBus()
    subs = [bus.subscribe("a"), bus.subscribe("b")]
    bus.close()
    for s in subs:
        assert [x async for x in s] == []
