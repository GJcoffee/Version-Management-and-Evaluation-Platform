"""内置 HiAgent 适配器：多段输出、Trace 观测失败不影响本轮、用时口径、业务桥构造。

上游 SSE 与 Trace 全部是手写的虚构数据，不访问真实服务。
"""

import json

import httpx
import pytest
import respx
from evalp.runtime.adapters.hiagent_adapter import HiAgentAdapter
from evalp.runtime.adapters.registry import validate_binding

CHAT = "https://hiagent.example/api/v1/chat_query_v2"
CREATE = "https://hiagent.example/api/v1/create_conversation"
OBSERVE = "https://observe.example/api/observe"

SSE_FRAMES = [
    {"event": "message_start", "id": "msg-1", "conversation_id": "conv-1"},
    {"event": "message_output_start", "id": "seg-1"},
    {"event": "message", "answer": "第一段，"},
    {"event": "agent_thought", "id": "tool-1", "tool": "查订单", "tool_input": "{}"},
    {"event": "agent_thought_end", "id": "tool-1", "observation": "已发货", "tool_latency": 0.2},
    {"event": "message_output_start", "id": "seg-2"},
    {"event": "message", "answer": "第二段。"},
    {"event": "message_end", "id": "msg-1", "input_tokens": 9, "output_tokens": 3},
]
SSE = "".join(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n" for frame in SSE_FRAMES)

TRACE = {"TraceID": "tr-1", "StartTime": "2026-11-01T00:00:00Z", "EndTime": "2026-11-01T00:00:20Z",
         "Status": {"Code": "OK"}}


def llm_span(sid, **over):
    attrs = {"gen_ai.span_type": "llm", "message_id": "msg-1", "app_id": "app-test",
             "gen_ai.agent.version": "v47", "input_tokens": 4, "output_tokens": 1,
             "input": json.dumps([{"role": "user", "content": "查一下订单"}], ensure_ascii=False),
             "output": json.dumps({"content": "第一段，"}, ensure_ascii=False), **over}
    return {"Context": {"TraceID": "tr-1", "SpanID": sid}, "Name": "豆包",
            "StartTime": "2026-11-01T00:00:01Z", "EndTime": "2026-11-01T00:00:03Z", "Attributes": attrs}


def tool_span(sid):
    return {"Context": {"TraceID": "tr-1", "SpanID": sid}, "Name": "查订单",
            "StartTime": "2026-11-01T00:00:03Z", "EndTime": "2026-11-01T00:00:04Z",
            "Attributes": {"gen_ai.span_type": "tool", "message_id": "msg-1", "app_id": "app-test",
                           "gen_ai.agent.version": "v47", "call_id": "tool-1"}}


def observe(trace, spans, *, calls=None):
    """按 Action 分派 GetTraceInfo / ListTraceSpans；traces 可以是按次序返回的列表。"""
    seen = {"GetTraceInfo": 0, "ListTraceSpans": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        seen[action] += 1
        if calls is not None:
            calls.append(action)
        if action == "GetTraceInfo":
            value = trace[min(seen[action], len(trace)) - 1] if isinstance(trace, list) else trace
            return httpx.Response(200, json={"Result": value})
        return httpx.Response(200, json={"Result": {"Items": spans, "Total": len(spans), "HasMore": False}})

    return handler


async def make_session(world, client, *, wait=2.0, observe_headers=True, extra=None):
    secret = {"api_key": "key-1", **({"observe_headers": {"Cookie": "c"}} if observe_headers else {})}
    ref = (await client.post("/api/v1/adapters/credentials", json={
        "name": "t", "secret": json.dumps(secret), "adapter_id": "hiagent",
    })).json()["ref"]
    version = await world.code_version({"transport": "adapter", "adapter": {
        "id": "hiagent", "version": "1.0.0", "credential_ref": ref, "config": {
            "base_url": "https://hiagent.example", "app_id": "app-test", "user_id": "u",
            "published_version": "47", "observe_base_url": "https://observe.example",
            "observation_wait_s": wait, **(extra or {}),
        },
    }})
    return version, await world.session(version)


def mock_upstream(router, handler=None):
    router.post(CREATE).mock(return_value=httpx.Response(
        200, json={"Conversation": {"AppConversationID": "conv-1"}}))
    router.post(CHAT).mock(return_value=httpx.Response(
        200, text=SSE, headers={"Content-Type": "text/event-stream"}))
    if handler is not None:
        router.post(OBSERVE).mock(side_effect=handler)


async def test_multi_segment_reply_and_every_model_call_with_input(world, client):
    _, session = await make_session(world, client)
    # 版本写法与配置不同、明细 token 与整轮不等：仍要展示每次调用
    spans = [llm_span("a"), llm_span("b", output=json.dumps({"content": "第二段。"}, ensure_ascii=False)),
             tool_span("t1")]
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, spans))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    assert turn.status == "ok", turn.error
    assert turn.output.final.content == "第一段，第二段。"
    events = await world.events(session.id)
    requests = [e for e in events if e.type == "llm.request"]
    assert [r.payload["messages"] for r in requests] == [[{"role": "user", "content": "查一下订单"}]] * 2
    assert len([e for e in events if e.type == "llm.response"]) == 2
    assert turn.usage["llm_calls"] == 2 and turn.usage["tool_calls"] == 1
    assert turn.usage["prompt_tokens"] == 9  # 整轮以 SSE 为准
    assert turn.usage["token_detail_mismatch"]["prompt_tokens"] == {"turn": 9, "details": 8}


@pytest.mark.parametrize("failure", ["null-status", "unauthorized", "not-json", "api-error", "null-items"])
async def test_trace_failure_never_changes_the_turn_outcome(world, client, failure):
    _, session = await make_session(world, client, wait=1)
    good = llm_span("a")

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        if failure == "unauthorized":
            return httpx.Response(401, json={})
        if failure == "not-json":
            return httpx.Response(200, text="<html>")
        if failure == "api-error":
            return httpx.Response(200, json={"ResponseMetadata": {"Error": {"Code": "NotFound"}}})
        if action == "GetTraceInfo":
            return httpx.Response(200, json={"Result": {**TRACE, "Status": None}})
        items = [None, {"Context": None, "Attributes": None}, good] if failure == "null-items" else [
            {"Context": {"TraceID": "tr-1", "SpanID": "x"}, "Attributes": None},
            {**good, "Attributes": {**good["Attributes"], "output": json.dumps({"generations": [[None]]})}},
        ]
        return httpx.Response(200, json={"Result": {"Items": items, "Total": len(items), "HasMore": False}})

    with respx.mock() as router:
        mock_upstream(router, handler)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    assert turn.status == "ok", turn.error


async def test_unavailable_trace_says_why_instead_of_hiding_the_llm_count(world, client):
    _, session = await make_session(world, client, observe_headers=False)
    with respx.mock() as router:
        mock_upstream(router)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    assert turn.status == "ok", turn.error
    assert turn.usage["trace_status"] == "unavailable"
    assert "凭据" in turn.usage["trace_note"]
    assert turn.usage["llm_calls"] == 0


async def test_deterministic_trace_problem_is_not_retried(world, client):
    _, session = await make_session(world, client, wait=5)
    calls: list[str] = []
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [tool_span("t1")], calls=calls))  # 没有模型调用
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    assert turn.status == "ok", turn.error
    assert calls == ["GetTraceInfo", "ListTraceSpans"]
    assert turn.usage["trace_fetch_ms"] < 2000
    assert "模型调用" in turn.usage["trace_note"]


async def test_trace_not_yet_complete_is_retried_and_its_time_is_not_turn_latency(world, client):
    _, session = await make_session(world, client, wait=5)
    pending = {**TRACE, "EndTime": None}
    calls: list[str] = []
    with respx.mock() as router:
        mock_upstream(router, observe([pending, TRACE], [llm_span("a")], calls=calls))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    assert turn.status == "ok", turn.error
    assert calls.count("GetTraceInfo") == 2
    assert turn.usage["llm_calls"] == 1 and turn.usage["upstream_run_ms"] == 20000
    assert turn.usage["trace_fetch_ms"] >= 450
    assert turn.latency_ms < turn.usage["trace_fetch_ms"]


def test_business_bridge_health_check_uses_the_same_headers_as_a_turn():
    from evalp.runtime.adapters.hiagent_adapter import business_bridge
    from evalp.runtime.adapters.registry import HiAgentConfig
    from evalp.spec.models import parse_spec

    spec = parse_spec({"kind": "code", "transport": "http", "http": {
        "base_url": "http://original.example", "headers": {"X-Tenant": "demo"}}})
    config = HiAgentConfig.model_validate({
        "base_url": "https://hiagent.example", "app_id": "a", "user_id": "u", "published_version": "47",
        "business_bridge": {"base_url": "http://bridge.example", "spec_hash": "0" * 64},
    })
    bridge = business_bridge(config, spec)
    assert bridge._headers() == {"X-Tenant": "demo"}
    assert bridge.base == "http://bridge.example"


async def test_health_check_reaches_the_bridge_with_the_version_headers(world, client):
    version = await world.code_version({
        "transport": "http", "http": {"base_url": "http://original.example", "headers": {"X-Tenant": "demo"}},
    })
    ref = (await client.post("/api/v1/adapters/credentials", json={
        "name": "t", "secret": "key", "adapter_id": "hiagent"})).json()["ref"]
    binding = validate_binding({"id": "hiagent", "version": "1.0.0", "credential_ref": ref, "config": {
        "base_url": "https://hiagent.example", "app_id": "a", "user_id": "u", "published_version": "47",
        "business_bridge": {"base_url": "http://bridge.example", "spec_hash": version["spec_hash"]},
    }})
    with respx.mock() as router:
        route = router.get("http://bridge.example/evalp/health").respond(200, json={"ok": True})
        await HiAgentAdapter(binding, world.ctx).healthcheck()
    assert route.calls[0].request.headers["x-tenant"] == "demo"
