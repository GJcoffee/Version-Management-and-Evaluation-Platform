"""规范事件口径：类型白名单、序列单调与摘要（各平台适配器契约测试共用）。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from evalp.runtime.events import AgentEvent

CANONICAL_EVENT_TYPES = frozenset({
    "turn.start", "turn.end",
    "llm.request", "llm.response", "llm.reasoning", "llm.delta",
    "tool.call", "tool.result",
    "metrics", "error", "log", "message.assistant",
    "span.start", "span.end", "source.raw",
})
TRACE_STATUSES = frozenset({"pending", "complete", "partial", "unavailable"})


def assert_canonical(events: list[AgentEvent]) -> None:
    """基本口径校验；契约测试与适配器实现共用，不依赖具体平台。"""
    unknown = {e.type for e in events} - CANONICAL_EVENT_TYPES
    assert not unknown, f"出现未归一化的事件类型: {sorted(unknown)}"
    seqs = [e.seq for e in events if e.seq is not None]
    assert seqs == sorted(seqs), "事件 seq 必须单调递增"
    assert len(seqs) == len(set(seqs)), "事件 seq 不能重复"
    for event in events:
        if event.type == "llm.response":
            usage = event.payload.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens"):
                value = usage.get(key)
                assert value is None or (isinstance(value, int) and not isinstance(value, bool)), (
                    f"llm.response.{key} 必须是整数或缺失"
                )
        if event.type == "metrics" and "trace_status" in event.payload:
            status = event.payload["trace_status"]
            assert status in TRACE_STATUSES, f"未知 trace_status: {status}"


def canonical_summary(events: list[AgentEvent]) -> dict[str, Any]:
    """契约测试用的稳定摘要：正文、调用次数、SSE 用量与整轮远端指标。"""
    types = Counter(e.type for e in events)
    text = "".join(
        str(e.payload.get("text") or "")
        for e in events if e.type == "llm.delta"
    )
    calls = {
        str(e.payload.get("call_id"))
        for e in events
        if e.type in ("tool.call", "tool.result") and e.payload.get("call_id") is not None
    }
    responses = {e.step_id for e in events if e.type == "llm.response"}
    turn_metrics = next(
        (e.payload for e in reversed(events)
         if e.type == "metrics" and e.payload.get("scope") == "turn"),
        {},
    )
    usage = turn_metrics.get("usage") or {}
    return {
        "text": text,
        "llm_calls": len(responses),
        "tool_calls": len(calls),
        "event_counts": dict(sorted(types.items())),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        },
        "turn": {
            key: turn_metrics[key]
            for key in ("trace_status", "observation_status", "upstream_run_ms",
                        "llm_api_ms", "tool_execution_ms", "llm_calls", "trace_note")
            if key in turn_metrics
        },
    }
