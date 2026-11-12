"""兼容层：Trace 实现已迁移到 ``evalp.runtime.adapters.platforms.hiagent.trace``。"""

from evalp.runtime.adapters.platforms.hiagent.trace import (
    TraceNotReady,
    TraceOnlyTarget,
    collect_trace,
    duration,
    failure_reason,
    input_messages,
    output_text,
    refresh_trace,
    retryable,
    trace_events,
    unavailable,
)

__all__ = [
    "TraceNotReady",
    "TraceOnlyTarget",
    "collect_trace",
    "duration",
    "failure_reason",
    "input_messages",
    "output_text",
    "refresh_trace",
    "retryable",
    "trace_events",
    "unavailable",
]
