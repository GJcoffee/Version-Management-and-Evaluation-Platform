"""HiAgent SSE 分帧：保留事件 ID，兼容多行 data、尾帧与 JSON 包装字符串。"""

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def decoded(value: Any) -> Any:
    """允许 JSON 包装字符串，无法解析时保留原值。"""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return json.loads(value)
    return value


async def sse_frames(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """按空行分帧，保留上游事件 ID，支持多行 data 和尾帧。"""
    lines: list[str] = []
    event, identity = "", ""
    async for line in response.aiter_lines():
        if line == "":
            if lines:
                value = decoded(decoded("\n".join(lines)))
                if isinstance(value, dict):
                    yield {**value, "event": value.get("event") or event, "_event_id": identity}
            lines, event, identity = [], "", ""
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("id:"):
            identity = line[3:].strip()
    if lines:
        value = decoded(decoded("\n".join(lines)))
        if isinstance(value, dict):
            yield {**value, "event": value.get("event") or event, "_event_id": identity}
