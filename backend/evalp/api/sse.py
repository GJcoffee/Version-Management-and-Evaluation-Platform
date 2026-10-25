"""SSE 工具（详设 7.4）。

帧格式：``id: {seq}`` / ``event: {type}`` / ``data: {json}``，空行结束；心跳为注释帧 ``: ping``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

PING_FRAME = ": ping\n\n"
HEARTBEAT_INTERVAL = 15.0


def format_sse(
    data: Any = None,
    *,
    event: str | None = None,
    id: str | int | None = None,  # noqa: A002
    retry: int | None = None,
) -> str:
    """格式化一个 SSE 帧。

    data 为 str 时原样输出（多行拆成多个 ``data:`` 行），其余类型（dict / list / Pydantic 模型）
    以 JSON（ensure_ascii=False）输出。id 为 None 时不输出 ``id:`` 行（如 delta 事件）。
    """
    lines: list[str] = []
    if id is not None:
        lines.append(f"id: {id}")
    if event:
        lines.append(f"event: {event}")
    if retry is not None:
        lines.append(f"retry: {retry}")
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for line in payload.split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


async def with_heartbeat(
    source: AsyncIterator[str], interval: float = HEARTBEAT_INTERVAL
) -> AsyncIterator[str]:
    """包装异步帧迭代器：source 在 interval 秒内没有产出时插入 ``: ping`` 心跳帧。"""
    it = source.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield PING_FRAME
                continue
            task, pending = pending, None
            try:
                frame = task.result()
            except StopAsyncIteration:
                return
            yield frame
    finally:
        if pending is not None:
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration, Exception):  # noqa: BLE001
                pass
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


def sse_response(
    frames: AsyncIterator[str],
    *,
    heartbeat: float | None = HEARTBEAT_INTERVAL,
    headers: Mapping[str, str] | None = None,
) -> StreamingResponse:
    """把帧迭代器包装成 SSE 响应（默认带 15s 心跳；heartbeat=None 关闭）。"""
    body = with_heartbeat(frames, heartbeat) if heartbeat else frames
    h = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    if headers:
        h.update(headers)
    return StreamingResponse(body, media_type="text/event-stream", headers=h)
