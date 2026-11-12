"""平台适配器接口：默认能力拒绝、HiAgent Trace 重查能力。"""

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from evalp.errors import AgentError
from evalp.runtime.adapters.base import PlatformAdapter, TraceRefreshRequest
from evalp.runtime.adapters.binding import NormalizedBinding
from evalp.runtime.adapters.normalize import assert_canonical
from evalp.runtime.adapters.platforms.hiagent.adapter import HiAgentAdapter
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.trace import TraceOnlyTarget, trace_events

OBSERVE = "https://observe.example/api/observe"
TRACE = {"TraceID": "tr-1", "StartTime": "2026-11-01T00:00:00Z",
         "EndTime": "2026-11-01T00:00:20Z", "Status": {"Code": "OK"}}
SPAN = {
    "Context": {"TraceID": "tr-1", "SpanID": "a"},
    "Name": "model",
    "StartTime": "2026-11-01T00:00:01Z",
    "EndTime": "2026-11-01T00:00:03Z",
    "Attributes": {
        "gen_ai.span_type": "llm", "message_id": "msg-1", "app_id": "app-test",
        "gen_ai.agent.version": "47", "input_tokens": 9, "output_tokens": 3,
        "input": json.dumps([{"role": "user", "content": "查订单"}]),
        "output": json.dumps({"content": "已发货"}),
    },
}
CONFIG = {
    "base_url": "https://hiagent.example", "app_id": "app-test", "user_id": "u",
    "published_version": "47", "workspace_id": "ws-1",
    "observe_base_url": "https://observe.example",
}


def adapter() -> HiAgentAdapter:
    binding = NormalizedBinding("hiagent", 1, CONFIG, "adapter:prv_x")
    return HiAgentAdapter(binding, SimpleNamespace())


def observe(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["WorkspaceID"] == "ws-1"
    if request.url.params["Action"] == "GetTraceInfo":
        return httpx.Response(200, json={"Result": TRACE})
    return httpx.Response(200, json={"Result": {"Items": [SPAN], "Total": 1, "HasMore": False}})


async def test_default_platform_adapter_reports_capability_unavailable():
    class Slim(PlatformAdapter):
        async def run_turn(self, ctx, history, user_input):
            if False:  # pragma: no cover - 使函数成为异步生成器
                yield

    request = TraceRefreshRequest("s", "t", None, None, "m")
    with pytest.raises(AgentError) as exc:
        [event async for event in Slim().refresh_trace(request)]
    assert exc.value.code == "CAPABILITY_UNAVAILABLE"


async def test_hiagent_refresh_trace_returns_canonical_remote_metrics():
    subject = adapter()
    request = TraceRefreshRequest(
        session_id="s", turn_id="t", binding=subject.binding,
        credential={"api_key": "k", "observe_headers": {"Cookie": "c"}},
        message_id="msg-1", previous_usage={"prompt_tokens": 9, "completion_tokens": 3},
    )
    with respx.mock() as router:
        router.post(OBSERVE).mock(side_effect=observe)
        events = [event async for event in subject.refresh_trace(request)]

    assert_canonical(events)
    metrics = events[-1].payload
    assert metrics["trace_status"] == "complete"
    assert metrics["upstream_run_ms"] == 20000
    assert metrics["usage"]["prompt_tokens"] == 9 and metrics["usage"]["completion_tokens"] == 3


@pytest.mark.parametrize(("value", "expected"), [
    (9, 9), ("9", 9), (" 009 ", 9), (0, 0), ("0", 0),
    (None, None), (True, None), (-1, None), ("bad", None), (1.5, None), ("1.5", None),
])
def test_trace_usage_normalizes_numeric_attributes_before_aggregation(value, expected):
    span = {**SPAN, "Attributes": {**SPAN["Attributes"], "input_tokens": value}}
    events = trace_events(
        HiAgentConfig.model_validate(CONFIG), TraceOnlyTarget("msg-1"), TRACE,
        {"Items": [span], "Total": 1, "HasMore": False},
    )
    response = next(event.payload for event in events if event.type == "llm.response")
    assert response["usage"].get("prompt_tokens") == expected
    metrics = events[-1].payload
    assert metrics["trace_status"] == ("complete" if expected is not None else "partial")
    if expected is not None:
        assert metrics["usage"]["prompt_tokens"] == expected
        assert metrics["usage"]["completion_tokens"] == 3
    else:
        assert metrics["usage"].get("prompt_tokens") is None
