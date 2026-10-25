"""进程内 EventBus（详设 7.3）：按主题发布 / 订阅。

- 主题：``session:{id}``、``eval:{run_id}``、``compare:{id}``；
- 每个订阅者一个有界队列（容量 1000）；队列满时断开该订阅者（``overflowed=True``），
  前端带 ``Last-Event-ID`` 重连后从数据库补齐；
- ``publish`` 与 ``subscribe`` 都是同步、非阻塞调用，必须在事件循环线程中调用。

用法::

    sub = bus.subscribe("session:ses_x")
    try:
        async for ev in sub:          # 被关闭或溢出断开后迭代结束
            ...
    finally:
        sub.close()

    # 带心跳（SSE 用）
    ev = await sub.get_with_heartbeat(15)   # 超时返回 HEARTBEAT
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from evalp.logging import get_logger

log = get_logger(__name__)

QUEUE_CAPACITY = 1000


class _Heartbeat:
    def __repr__(self) -> str:
        return "HEARTBEAT"


HEARTBEAT = _Heartbeat()
_CLOSE = object()


class SubscriptionClosed(Exception):
    """订阅已关闭（主动关闭、总线关闭或队列溢出被断开）。"""


class Subscription:
    """一个订阅者：可异步迭代、可关闭，也可用作 ``async with``。"""

    def __init__(self, topic: str, capacity: int, on_close: Callable[[Subscription], None]) -> None:
        self.topic = topic
        self.overflowed = False
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self._on_close = on_close

    @property
    def closed(self) -> bool:
        return self._closed

    def qsize(self) -> int:
        return self._queue.qsize()

    # -- 发布侧调用
    def _offer(self, event: Any) -> bool:
        """投递事件；队列已满返回 False。"""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            return False
        return True

    # -- 订阅侧
    def close(self) -> None:
        """关闭订阅（幂等）。已缓冲的事件仍可被读完，之后迭代结束。"""
        if self._closed:
            return
        self._closed = True
        self._on_close(self)
        try:
            self._queue.put_nowait(_CLOSE)  # 唤醒正在等待的读取者；队列满时读取者必有数据可读
        except asyncio.QueueFull:
            pass

    async def get(self, timeout: float | None = None) -> Any:
        """取下一个事件。超时抛 ``asyncio.TimeoutError``；订阅关闭且缓冲读完后抛 ``SubscriptionClosed``。"""
        if not self._queue.empty():
            item = self._queue.get_nowait()
        elif self._closed:
            raise SubscriptionClosed(self.topic)
        elif timeout is None:
            item = await self._queue.get()
        else:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        if item is _CLOSE:
            raise SubscriptionClosed(self.topic)
        return item

    async def get_with_heartbeat(self, interval: float = 15.0) -> Any:
        """同 get，但等待超过 interval 秒时返回 ``HEARTBEAT`` 而不是抛超时。"""
        try:
            return await self.get(interval)
        except TimeoutError:
            return HEARTBEAT

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.get()
        except SubscriptionClosed:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class EventBus:
    """按主题的发布订阅总线。"""

    def __init__(self, capacity: int = QUEUE_CAPACITY) -> None:
        self.capacity = capacity
        self._subs: dict[str, set[Subscription]] = {}

    def subscribe(self, topic: str) -> Subscription:
        """订阅主题，返回订阅对象。"""
        sub = Subscription(topic, self.capacity, self._remove)
        self._subs.setdefault(topic, set()).add(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.topic)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.topic]

    def publish(self, topic: str, event: Any) -> int:
        """向主题的所有订阅者投递事件，返回成功投递的订阅者数。队列满的订阅者会被断开。"""
        subs = self._subs.get(topic)
        if not subs:
            return 0
        delivered = 0
        for sub in list(subs):
            if sub._offer(event):
                delivered += 1
            else:
                sub.overflowed = True
                log.warning("eventbus.subscriber_dropped", topic=topic, reason="queue_full")
                sub.close()
        return delivered

    def subscriber_count(self, topic: str | None = None) -> int:
        """订阅者数量（可限定主题）。"""
        if topic is not None:
            return len(self._subs.get(topic, ()))
        return sum(len(v) for v in self._subs.values())

    def close(self) -> None:
        """关闭所有订阅。"""
        for subs in list(self._subs.values()):
            for sub in list(subs):
                sub.close()
