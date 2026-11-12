"""业务桥接已核验的 HiAgent Trace 用时转换为整轮指标。"""

import math
from typing import Any

BRIDGE_TRACE_SOURCE = "actual_upstream_trace_timestamps"


def duration_ms(value: Any) -> int | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return int(value)


def bridge_trace_metrics(payload: dict[str, Any]) -> dict[str, Any] | None:
    """仅采用带真实 Trace 来源及有效整轮用时的日志，不用调用累计替代整轮。"""
    run_ms = duration_ms(payload.get("upstream_run_ms"))
    if payload.get("metric_source") != BRIDGE_TRACE_SOURCE or run_ms is None:
        return None
    metrics: dict[str, Any] = {
        "scope": "turn",
        "source": "hiagent_trace",
        "metric_source": BRIDGE_TRACE_SOURCE,
        "trace_status": "complete",
        "upstream_run_ms": run_ms,
    }
    for key in ("llm_api_ms", "tool_execution_ms"):
        if (value := duration_ms(payload.get(key))) is not None:
            metrics[key] = value
    return metrics
