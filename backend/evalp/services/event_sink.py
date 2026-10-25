"""EventSink：事件的分配 seq、发布到 EventBus、批量落库（详设 7.2）。

- 每个会话的每次执行一个实例：``EventSink(ctx, session_id, start_seq)``，``start_seq`` 为会话当前的
  ``last_seq``；
- ``emit()``：先发布、后落库。``llm.delta`` 只发布（没有 seq）；其余事件分配 seq 后入缓冲，缓冲达到
  ``events.flush_batch`` 条、遇到 ``turn.end`` / ``error``，或后台定时器
  （``events.flush_interval_ms``）到点时批量写库；
- 脱敏：入缓冲前对 payload 做 ``redact()``，因此总线上和库里的内容一致（都已脱敏）；
- 大 payload：序列化后超过 ``events.payload_inline_limit_kb`` 的，完整内容转存为 blob
  （``blobs`` 表登记并 incref），库里的 payload 换成 ``{"$blob": hash, ...}``，并保留体积很小的顶层字段
  （如 name、call_id、model、usage），以便步骤对齐等逻辑不必读取 blob；总线上发布的仍是完整内容；
- 写库失败只记录日志、保留缓冲并在下次重试，不会中断本轮执行；``close()`` 做最后一次 flush。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from sqlalchemy import insert, update

from evalp.context import AppContext
from evalp.logging import get_logger
from evalp.runtime.events import AgentEvent, redact
from evalp.store.models import Event, Session

log = get_logger(__name__)

IMMEDIATE_FLUSH_TYPES = frozenset({"turn.end", "error"})
BLOB_KEY = "$blob"
SUMMARY_FIELD_LIMIT = 256  # 转存为 blob 后，库里仍保留的顶层字段的最大序列化长度（字节）


def session_topic(session_id: str) -> str:
    """会话事件的 EventBus 主题。"""
    return f"session:{session_id}"


def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def blob_stub(payload: dict[str, Any], blob_hash: str) -> dict[str, Any]:
    """转存为 blob 后留在库里的 payload：``$blob`` + 体积很小的顶层字段。"""
    stub: dict[str, Any] = {BLOB_KEY: blob_hash}
    for k, v in payload.items():
        if k != BLOB_KEY and len(_dumps(v)) <= SUMMARY_FIELD_LIMIT:
            stub[k] = v
    return stub


class EventSink:
    """分配 seq、发布并批量落库。"""

    def __init__(self, ctx: AppContext, session_id: str, start_seq: int = 0, *, turn_id: str | None = None):
        cfg = ctx.config.events
        self.ctx = ctx
        self.session_id = session_id
        self.turn_id = turn_id
        self.flush_interval = cfg.flush_interval_ms / 1000
        self.flush_batch = cfg.flush_batch
        self.inline_limit = cfg.payload_inline_limit_kb * 1024
        self.topic = session_topic(session_id)
        self._seq = start_seq
        self._buf: list[AgentEvent] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._timer: asyncio.Task[None] | None = None
        self._closed = False
        self.flushes = 0  # 成功写库的批次数（测试 / 诊断用）

    @property
    def last_seq(self) -> int:
        """已分配的最大 seq。"""
        return self._seq

    @property
    def pending(self) -> int:
        """尚未落库的事件数。"""
        return len(self._buf)

    # ------------------------------------------------------------ 发布

    async def emit(self, event: AgentEvent) -> None:
        """发布事件；需要落库的进入缓冲。"""
        if event.turn_id is None and self.turn_id is not None:
            event = event.with_turn(self.turn_id)
        if event.type == "llm.delta":
            self.ctx.bus.publish(self.topic, event.with_seq(None))
            return
        self._seq += 1
        safe = event.model_copy(update={"seq": self._seq, "payload": redact(event.payload)})
        self.ctx.bus.publish(self.topic, safe)
        self._buf.append(safe)
        if len(self._buf) >= self.flush_batch or event.type in IMMEDIATE_FLUSH_TYPES:
            await self._safe_flush()
        else:
            self._start_timer()
            self._wake.set()

    # ------------------------------------------------------------ 落库

    def _start_timer(self) -> None:
        if self._timer is None and not self._closed:
            self._timer = asyncio.create_task(self._timer_loop(), name=f"event-sink-{self.session_id}")

    async def _timer_loop(self) -> None:
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            await asyncio.sleep(self.flush_interval)
            await self._safe_flush()

    async def _safe_flush(self) -> None:
        try:
            await self.flush()
        except Exception as e:  # noqa: BLE001 - 写库失败不能中断本轮；缓冲保留，下次重试
            log.error("event_sink.flush_failed", session_id=self.session_id, error=f"{type(e).__name__}: {e}")

    async def flush(self) -> None:
        """把缓冲中的事件在一个事务里写库，并更新 ``sessions.last_seq``。失败时抛异常，缓冲保持不变。"""
        async with self._lock:
            if not self._buf:
                return
            batch = list(self._buf)
            task = asyncio.ensure_future(self._write(batch))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # 调用方被取消：写库任务继续跑完，再按结果决定是否保留缓冲，避免重复写入
                await asyncio.wait({task})
                if not task.cancelled() and task.exception() is None:
                    del self._buf[: len(batch)]
                raise
            del self._buf[: len(batch)]
            self.flushes += 1

    async def _write(self, batch: list[AgentEvent]) -> None:
        rows: list[dict[str, Any]] = []
        blob_hashes: list[str] = []
        for e in batch:
            payload = e.payload
            raw = _dumps(payload)
            if len(raw) > self.inline_limit:
                h = await self.ctx.blobs.put_bytes(raw)
                blob_hashes.append(h)
                payload = blob_stub(payload, h)
            rows.append(
                {
                    "session_id": self.session_id,
                    "seq": e.seq,
                    "turn_id": e.turn_id,
                    "ts": e.ts,
                    "type": e.type,
                    "step_id": e.step_id,
                    "parent_step_id": e.parent_step_id,
                    "payload": payload,
                }
            )
        max_seq = max(r["seq"] for r in rows)
        async with self.ctx.db.write() as s:
            await s.execute(insert(Event), rows)
            await s.execute(
                update(Session)
                .where(Session.id == self.session_id, Session.last_seq < max_seq)
                .values(last_seq=max_seq)
            )
            for h in blob_hashes:
                await self.ctx.blobs.incref(h, session=s)

    async def close(self) -> None:
        """停止定时器并做最后一次 flush。"""
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._timer
            self._timer = None
        await self._safe_flush()
        if self._buf:
            log.error("event_sink.events_lost", session_id=self.session_id, count=len(self._buf))
            self._buf.clear()
