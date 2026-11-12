"""内置 HiAgent 适配器：多段输出、Trace 观测失败不影响本轮、用时口径、业务桥构造。

上游 SSE 与 Trace 全部是手写的虚构数据，不访问真实服务。
"""

import asyncio
import json

import httpx
import pytest
import respx
from evalp.runtime.adapters.hiagent_adapter import HiAgentAdapter
from evalp.runtime.adapters.registry import validate_binding
from evalp.services.session import Idle, SessionService

from tests.integration.adapter_env import configure_production, make_credential

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
    ref = await make_credential(client, secret)
    await configure_production(client, {
        "id": "hiagent", "version": "1.0.0", "credential_ref": ref, "config": {
            "base_url": "https://hiagent.example", "app_id": "app-test", "user_id": "u",
            "published_version": "47", "observe_base_url": "https://observe.example",
            "observation_wait_s": wait, **(extra or {}),
        },
    })
    version = await world.code_version({"transport": "adapter"})
    return version, await world.session(version)


def mock_upstream(router, handler=None):
    router.post(CREATE).mock(return_value=httpx.Response(
        200, json={"Conversation": {"AppConversationID": "conv-1"}}))
    router.post(CHAT).mock(return_value=httpx.Response(
        200, text=SSE, headers={"Content-Type": "text/event-stream"}))
    if handler is not None:
        router.post(OBSERVE).mock(side_effect=handler)


class Gate:
    """让 Trace 在放行前一直处于"尚未完整"，用来观察补齐之前的状态。"""

    def __init__(self, spans, trace=TRACE, calls=None):
        self.open = False
        self.spans, self.trace, self.calls = spans, trace, calls if calls is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        self.calls.append(action)
        if action == "GetTraceInfo":
            trace = self.trace if self.open else {**self.trace, "EndTime": None}
            return httpx.Response(200, json={"Result": trace})
        return httpx.Response(200, json={"Result": {"Items": self.spans, "Total": len(self.spans),
                                                    "HasMore": False}})


async def test_turn_ends_without_waiting_for_trace_and_trace_backfills_in_background(world, client):
    _, session = await make_session(world, client)
    gate = Gate([llm_span("a"), llm_span("b", output=json.dumps({"content": "第二段。"})), tool_span("t1")])
    with respx.mock() as router:
        mock_upstream(router, gate)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        # 本轮已结束：回复、token 已有，规范用时等待远端，指标标为同步中
        assert turn.status == "ok", turn.error
        assert turn.output.final.content == "第一段，第二段。"
        assert turn.usage["trace_status"] == "pending"
        assert turn.latency_ms is None and turn.ttft_ms is None
        assert turn.usage["prompt_tokens"] == 9 and turn.usage["local_latency_ms"] is not None
        assert (await world.sessions.get_session(session.id)).status == "idle"
        types = await world.types(session.id)
        assert types[-1] == "turn.end" and "llm.request" not in types
        assert "GetTraceInfo" in gate.calls and "ListTraceSpans" not in gate.calls  # 不完整时不拉明细

        gate.open = True
        done = await world.sessions.wait_trace(turn.id)
    assert gate.calls.count("ListTraceSpans") == 1
    assert done.usage["trace_status"] == "partial"  # 明细 token 与整轮不一致
    assert done.latency_ms == 20000 and done.ttft_ms is None  # 远端用时；本地值只在 usage 里
    assert done.usage["llm_calls"] == 2 and done.usage["tool_calls"] == 1
    assert done.usage["prompt_tokens"] == 9  # 整轮以 SSE 为准
    assert done.usage["token_detail_mismatch"]["prompt_tokens"] == {"turn": 9, "details": 8}
    assert done.usage["local_latency_ms"] is not None and done.usage["observe_ms"] >= 0
    events = await world.events(session.id)
    end_seq = next(e.seq for e in events if e.type == "turn.end")
    late = [e for e in events if e.seq > end_seq]
    assert {"llm.request", "llm.response", "tool.result", "metrics"} <= {e.type for e in late}
    assert all(e.turn_id == turn.id for e in late)
    assert [e.payload["messages"] for e in late if e.type == "llm.request"] == \
        [[{"role": "user", "content": "查一下订单"}]] * 2
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    stats = (await world.sessions.get_session(session.id)).stats
    assert stats["llm_calls"] == 2 and stats["tool_calls"] == 1 and stats["prompt_tokens"] == 9


async def test_trace_string_tokens_fill_missing_sse_turn_usage(world, client):
    _, session = await make_session(world, client)
    frames = [*SSE_FRAMES[:-1], {"event": "message_end", "id": "msg-1"}]
    spans = [llm_span("a", input_tokens="4", output_tokens="1"),
             llm_span("b", input_tokens="5", output_tokens="2")]
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, spans))
        router.post(CHAT).mock(return_value=httpx.Response(
            200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames),
            headers={"Content-Type": "text/event-stream"},
        ))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
    assert done.status == "ok" and done.output == turn.output
    assert done.usage["trace_status"] == "complete"
    assert done.usage["prompt_tokens"] == 9 and done.usage["completion_tokens"] == 3
    assert done.usage["token_source"] == "hiagent_trace"
    assert not done.usage.get("trace_note")
    stats = (await world.sessions.get_session(session.id)).stats
    assert stats["prompt_tokens"] == 9 and stats["completion_tokens"] == 3


@pytest.mark.parametrize("refresh", [False, True])
async def test_core_trace_backfill_and_refresh_deduplicate_wrappers_in_turn_and_session_stats(
    world, client, refresh,
):
    _, session = await make_session(world, client)
    frames = [*SSE_FRAMES[:-1], {"event": "message_end", "id": "msg-1"}]
    wrapper = llm_span("wrapper", input_tokens="4", output_tokens="1")
    core = llm_span("core", input_tokens="4", output_tokens="1", **{"gen_ai.span_type": "core_llm"})
    core["Context"]["ParentSpanID"] = "http"
    transport = {"Context": {"TraceID": "tr-1", "SpanID": "http", "ParentSpanID": "wrapper"}}
    standalone = llm_span("standalone", input_tokens="5", output_tokens="2",
                          **{"gen_ai.span_type": "core_llm"})
    spans = [core, transport, standalone, wrapper]
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [] if refresh else spans))
        router.post(CHAT).mock(return_value=httpx.Response(
            200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames),
            headers={"Content-Type": "text/event-stream"},
        ))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
        if refresh:
            assert done.usage["trace_status"] == "partial"
            router.post(OBSERVE).mock(side_effect=observe(TRACE, spans))
            done = await world.sessions.refresh_trace(turn.id)
        assert done.status == "ok" and done.output == turn.output
        assert done.usage["trace_status"] == "complete"
        assert done.usage["prompt_tokens"] == 9 and done.usage["completion_tokens"] == 3
        assert done.usage["llm_calls"] == 2 and done.usage["llm_api_ms"] == 4000
        responses = [e for e in await world.events(session.id) if e.type == "llm.response"]
        assert {e.step_id for e in responses} == {"llm:wrapper", "llm:standalone"}
        assert len(responses) == 2
        await world.sessions.refresh_trace(turn.id)  # complete 后再查不重复累加
    stats = (await world.sessions.get_session(session.id)).stats
    assert stats["prompt_tokens"] == 9 and stats["completion_tokens"] == 3
    assert stats["llm_calls"] == 2 and stats["turns"] == 1


@pytest.mark.parametrize("sse_tokens", [False, True])
async def test_force_refresh_replaces_cached_trace_tokens_but_preserves_original_sse_total(
    world, client, sse_tokens,
):
    _, session = await make_session(world, client)
    end = {"event": "message_end", "id": "msg-1"}
    if sse_tokens:
        end.update(input_tokens=5, output_tokens=2)
    frames = [*SSE_FRAMES[:-1], end]
    wrapper = llm_span("wrapper", input_tokens=5, output_tokens=2)
    core = llm_span("core", input_tokens=5, output_tokens=2, **{"gen_ai.span_type": "core_llm"})
    core["Context"]["ParentSpanID"] = "wrapper"
    extra = llm_span("extra", input_tokens=5, output_tokens=2, **{"gen_ai.span_type": "core_llm"})
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [wrapper]))
        chat = router.post(CHAT).mock(return_value=httpx.Response(
            200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames),
            headers={"Content-Type": "text/event-stream"},
        ))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        before = await world.sessions.wait_trace(turn.id)
        snapshot = (await world.sessions.get_session(session.id)).adapter_snapshot
        assert before.usage["trace_status"] == "complete" and before.usage["token_source"] == "hiagent_trace"
        assert before.usage["prompt_tokens"] == 5
        query = router.post(OBSERVE).mock(side_effect=observe(TRACE, [wrapper, core, extra]))
        query_count = query.call_count
        await world.sessions.refresh_trace(turn.id)
        assert query.call_count == query_count  # 默认仍跳过 complete
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={"force": True})
        assert response.status_code == 200, response.text
        done = await world.sessions.get_turn(turn.id)
        assert done.status == before.status and done.input == before.input and done.output == before.output
        assert done.started_at == before.started_at and done.finished_at == before.finished_at
        assert done.usage["llm_calls"] == 2
        assert done.usage["prompt_tokens"] == (5 if sse_tokens else 10)
        assert done.usage["completion_tokens"] == (2 if sse_tokens else 4)
        assert done.usage["trace_status"] == ("partial" if sse_tokens else "complete")
        assert done.usage["trace_refresh"]["force"] is True
        if sse_tokens:
            assert done.usage["token_detail_mismatch"]["prompt_tokens"] == {"turn": 5, "details": 10}
        stats = (await world.sessions.get_session(session.id)).stats
        assert stats["prompt_tokens"] == done.usage["prompt_tokens"]
        assert stats["completion_tokens"] == done.usage["completion_tokens"]
        assert stats["llm_calls"] == 2 and stats["turns"] == 1
        await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={"force": True})
        after = await world.sessions.get_session(session.id)
        assert after.stats == stats and after.adapter_snapshot == snapshot
        assert chat.call_count == 1  # 重查只请求 Observe，不再次运行模型


async def test_failed_force_refresh_preserves_complete_metrics_and_records_attempt(world, client):
    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [llm_span("one", input_tokens=9, output_tokens=3)]))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        before = await world.sessions.wait_trace(turn.id)
        stats = (await world.sessions.get_session(session.id)).stats
        router.post(OBSERVE).mock(return_value=httpx.Response(403))
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={"force": True})
    assert response.status_code == 200
    done = await world.sessions.get_turn(turn.id)
    assert {k: v for k, v in done.usage.items() if k != "trace_refresh"} == before.usage
    assert done.usage["trace_refresh"]["result_status"] == "unavailable"
    assert done.usage["trace_refresh"]["error"]
    assert done.latency_ms == before.latency_ms and done.output == before.output
    assert (await world.sessions.get_session(session.id)).stats == stats


async def test_refresh_string_tokens_repairs_missing_usage_without_double_counting(world, client):
    _, session = await make_session(world, client)
    frames = [*SSE_FRAMES[:-1], {"event": "message_end", "id": "msg-1"}]
    spans = [llm_span("a", input_tokens="4", output_tokens="1"),
             llm_span("b", input_tokens="5", output_tokens=None)]
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, spans))
        router.post(CHAT).mock(return_value=httpx.Response(
            200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames),
            headers={"Content-Type": "text/event-stream"},
        ))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        partial = await world.sessions.wait_trace(turn.id)
        assert partial.usage["trace_status"] == "partial"
        assert not partial.usage.get("prompt_tokens")
        snapshot = (await world.sessions.get_session(session.id)).adapter_snapshot

        fixed = [spans[0], llm_span("b", input_tokens="5", output_tokens="2")]
        router.post(OBSERVE).mock(side_effect=observe(TRACE, fixed))
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={
            "config_source": "current", "environment": "production",
        })
        assert response.status_code == 200, response.text
        done = await world.sessions.get_turn(turn.id)
        assert done.status == "ok" and done.output == partial.output
        assert done.usage["trace_status"] == "complete"
        assert done.usage["prompt_tokens"] == 9 and done.usage["completion_tokens"] == 3
        assert not done.usage.get("trace_note")
        assert (await world.sessions.get_session(session.id)).adapter_snapshot == snapshot
        await world.sessions.refresh_trace(turn.id)  # 已 complete 的轮次再次查询不重复计数
    stats = (await world.sessions.get_session(session.id)).stats
    assert stats["prompt_tokens"] == 9 and stats["completion_tokens"] == 3
    assert stats["llm_calls"] == 2 and stats["turns"] == 1


async def test_next_turn_can_start_while_the_previous_trace_is_still_syncing(world, client):
    _, session = await make_session(world, client)
    gate = Gate([llm_span("a")])
    with respx.mock() as router:
        mock_upstream(router, gate)
        first = await world.sessions.run_turn(session.id, "一", wait=True)
        second = await world.sessions.run_turn(session.id, "二", wait=True)  # 不会 SessionBusy
        assert first.usage["trace_status"] == second.usage["trace_status"] == "pending"
        gate.open = True
        await world.sessions.wait_traces(session.id)
    turns = (await world.sessions.get(session.id)).turns
    assert [t.latency_ms for t in turns] == [20000, 20000]
    assert (await world.sessions.get_session(session.id)).stats["llm_calls"] == 2
    seqs = [e.seq for e in await world.events(session.id)]
    assert seqs == list(range(1, len(seqs) + 1))


async def test_sse_stays_open_until_the_trace_is_synced(world, client):
    _, session = await make_session(world, client)
    gate = Gate([llm_span("a", input_tokens=9, output_tokens=3)])
    seen: list = []

    async def consume():
        async for item in world.sessions.stream(session.id):
            seen.append(item)

    with respx.mock() as router:
        mock_upstream(router, gate)
        started = await world.sessions.run_turn(session.id, "查订单", wait=False)
        consumer = asyncio.create_task(consume())
        await world.sessions.wait_turn(started.id)
        await asyncio.sleep(0.3)
        assert not consumer.done()  # 轮次结束了，但补齐期间不发 idle
        assert any(getattr(i, "type", None) == "turn.end" for i in seen)
        gate.open = True
        await asyncio.wait_for(consumer, 5)
    assert isinstance(seen[-1], Idle)
    assert [i.type for i in seen[:-1]].index("turn.end") < [i.type for i in seen[:-1]].index("llm.response")
    assert seen[-2].type == "metrics" and seen[-2].payload["trace_status"] == "complete"


async def test_sse_opened_during_sync_replays_then_waits_for_the_backfill(world, client):
    _, session = await make_session(world, client)
    gate = Gate([llm_span("a", input_tokens=9, output_tokens=3)])
    seen: list = []

    async def consume():
        async for item in world.sessions.stream(session.id):
            seen.append(item)

    with respx.mock() as router:
        mock_upstream(router, gate)
        await world.sessions.run_turn(session.id, "查订单", wait=True)
        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        assert not consumer.done() and seen[-1].type == "turn.end"
        gate.open = True
        await asyncio.wait_for(consumer, 5)
    assert isinstance(seen[-1], Idle) and seen[-2].payload["trace_status"] == "complete"


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
        done = await world.sessions.wait_trace(turn.id)
    assert done.status == "ok" and done.usage["trace_status"] in ("complete", "partial", "unavailable")


async def test_unavailable_trace_says_why_and_never_substitutes_the_local_latency(world, client):
    _, session = await make_session(world, client, observe_headers=False)
    with respx.mock() as router:
        mock_upstream(router)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
    assert done.status == "ok", done.error
    assert done.usage["trace_status"] == "unavailable"
    assert "凭据" in done.usage["trace_note"]
    assert done.usage["llm_calls"] == 0 and done.usage["llm_calls_status"] == "unknown"
    assert done.latency_ms is None and done.ttft_ms is None
    assert done.usage["local_latency_ms"] >= 0  # 本地测量只留作诊断


async def test_deterministic_trace_problem_is_not_retried(world, client):
    _, session = await make_session(world, client, wait=5)
    calls: list[str] = []
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [tool_span("t1")], calls=calls))  # 没有模型调用
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
    assert done.status == "ok", done.error
    assert calls == ["GetTraceInfo", "ListTraceSpans"]
    assert done.usage["observe_ms"] < 2000
    assert "模型调用" in done.usage["trace_note"]
    assert done.usage["trace_status"] == "partial" and done.latency_ms == 20000


async def test_incomplete_trace_is_polled_without_listing_spans(world, client):
    _, session = await make_session(world, client, wait=5)
    pending = {**TRACE, "EndTime": None}
    calls: list[str] = []
    with respx.mock() as router:
        mock_upstream(router, observe([pending, {"TraceID": None}, TRACE], [llm_span("a")], calls=calls))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        assert turn.latency_ms is None
        done = await world.sessions.wait_trace(turn.id)
    assert calls == ["GetTraceInfo"] * 3 + ["ListTraceSpans"]
    assert done.usage["llm_calls"] == 1 and done.usage["upstream_run_ms"] == 20000
    assert done.latency_ms == 20000 and done.usage["observe_ms"] >= 900


async def test_trace_that_never_completes_becomes_unavailable_with_the_reason(world, client):
    _, session = await make_session(world, client, wait=1)
    calls: list[str] = []
    with respx.mock() as router:
        mock_upstream(router, observe({**TRACE, "EndTime": None}, [], calls=calls))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
    assert "ListTraceSpans" not in calls
    assert done.usage["trace_status"] == "unavailable" and "尚未完整" in done.usage["trace_note"]
    assert done.latency_ms is None


async def test_failed_turn_with_a_message_id_still_backfills_and_stays_failed(world, client):
    _, session = await make_session(world, client)
    failing = "".join(f"data: {json.dumps(f)}\n\n" for f in [
        {"event": "message_start", "id": "msg-1", "conversation_id": "conv-1"}, {"event": "message_error"}])
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [llm_span("a", input_tokens=9, output_tokens=3)]))
        router.post(CHAT).mock(return_value=httpx.Response(
            200, text=failing, headers={"Content-Type": "text/event-stream"}))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        assert turn.status == "error" and turn.usage["trace_status"] == "pending"
        done = await world.sessions.wait_trace(turn.id)
    assert done.status == "error"
    assert done.usage["trace_status"] == "complete" and done.latency_ms == 20000


async def test_pending_turns_are_marked_unavailable_after_a_restart(world, client):
    _, session = await make_session(world, client)
    gate = Gate([llm_span("a")])
    with respx.mock() as router:
        mock_upstream(router, gate)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        assert turn.usage["trace_status"] == "pending"
        await world.sessions.aclose()  # 进程退出：补齐被中断，库里仍是 pending
    assert (await world.sessions.get_turn(turn.id)).usage["trace_status"] == "pending"
    before = (await world.sessions.get_session(session.id)).last_seq
    restarted = SessionService(world.ctx)
    assert await restarted.recover() == 1
    done = await restarted.get_turn(turn.id)
    assert done.usage["trace_status"] == "unavailable"
    assert done.usage["trace_note"] == "服务重启，Trace 补齐中断" and done.latency_ms is None
    events = await restarted.get_events(session.id, after_seq=before)
    assert [e.type for e in events] == ["metrics"] and events[0].turn_id == turn.id
    assert events[0].payload["trace_status"] == "unavailable"


async def test_unavailable_trace_can_be_requeried_until_it_arrives(world, client):
    """Trace 未取得不是终点：稍后重查仍未取得时保留原因，取得后按远端口径回填。"""
    _, session = await make_session(world, client, wait=0.2)
    gate = Gate([llm_span("a")])
    with respx.mock() as router:
        mock_upstream(router, gate)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        done = await world.sessions.wait_trace(turn.id)
        assert done.usage["trace_status"] == "unavailable" and done.latency_ms is None
        before = (await world.sessions.get_session(session.id)).last_seq
        still = (await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")).json()
        assert still["usage"]["trace_status"] == "unavailable" and still["latency_ms"] is None
        assert "尚未完整" in still["usage"]["trace_note"]
        gate.open = True
        refreshed = (await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")).json()
        late = await world.events(session.id, after_seq=before)
    assert refreshed["latency_ms"] == 20000 and refreshed["ttft_ms"] is None
    assert refreshed["usage"]["trace_status"] == "partial"
    assert refreshed["usage"]["llm_calls"] == 1 and refreshed["usage"]["upstream_run_ms"] == 20000
    # 整轮 token 仍沿用 SSE 收到的 9/3，不被明细的 4/1 改写
    assert refreshed["usage"]["prompt_tokens"] == 9 and refreshed["usage"]["completion_tokens"] == 3
    assert late[-1].type == "metrics" and late[-1].turn_id == turn.id
    assert [e.seq for e in late] == list(range(before + 1, before + 1 + len(late)))
    stats = (await world.sessions.get_session(session.id)).stats
    assert stats["llm_calls"] == 1 and stats["prompt_tokens"] == 9 and stats["tool_calls"] == 1


async def test_sse_conversation_id_is_separate_from_app_conversation_id(world, client):
    """AppConversationID 用于请求，SSE conversation_id 是运行标识，不能当成同一个身份。"""
    _, session = await make_session(world, client)
    sse = "".join(f"data: {json.dumps(frame)}\n\n" for frame in [
        {"event": "message_start", "id": "msg-1", "conversation_id": "run-1"},
        {"event": "message", "answer": "ok"},
        {"event": "message_end", "id": "msg-1"},
    ])
    with respx.mock() as router:
        mock_upstream(router)
        router.post(CHAT).mock(return_value=httpx.Response(
            200, text=sse, headers={"Content-Type": "text/event-stream"}))
        turn = await world.sessions.run_turn(session.id, "hi", wait=True)
    assert turn.status == "ok", turn.error
    assert turn.output.final and turn.output.final.content == "ok"


async def test_refresh_trace_rejects_a_turn_without_a_platform_binding(world):
    from evalp.errors import ValidationFailed

    version = await world.config_version()
    session = await world.session(version)
    turn = await world.sessions.run_turn(session.id, "你好", wait=True)
    with pytest.raises(ValidationFailed, match="没有平台适配器接入"):
        await world.sessions.refresh_trace(turn.id)


async def test_refresh_uses_new_credentials_and_observe_url_but_keeps_original_identity(world, client):
    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router)
        router.post(CHAT).mock(return_value=httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            text='data: {"event":"message_start","id":"msg-1","conversation_id":"conv-1"}\n\n'
                 'data: {"event":"message_error"}\n\n'))
        router.post(OBSERVE).mock(return_value=httpx.Response(403))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        before = await world.sessions.wait_trace(turn.id)
    original = (await world.sessions.get_session(session.id)).adapter_snapshot
    fresh = await make_credential(client, {"api_key": "new-key", "observe_headers": {"Cookie": "new-cookie"}})
    changed = {**original["binding"], "credential_ref": fresh, "config": {
        **original["binding"]["config"], "app_id": "different-app", "published_version": "999",
        "workspace_id": "different-workspace", "base_url": "https://different.example",
        "observe_base_url": "https://new-observe.example",
    }}
    await configure_production(client, changed)
    handler = observe(TRACE, [llm_span("a", input_tokens=9, output_tokens=3)])
    requests = []
    def query(request):
        requests.append(request)
        return handler(request)
    with respx.mock() as router:
        router.post("https://new-observe.example/api/observe").mock(side_effect=query)
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")
    assert response.status_code == 200, response.text
    done = response.json()
    assert done["status"] == before.status == "error"
    assert done["output"] == (before.output.model_dump(mode="json") if before.output else None)
    assert done["usage"]["trace_status"] == "complete"
    assert "trace_note" not in done["usage"]
    audit = done["usage"]["trace_refresh"]
    assert audit["credential_ref"] == fresh and audit["message_id"] == "msg-1"
    assert audit["revision"] == 2 and audit["config_source"] == "current"
    assert len(audit["impl_digest"]) == 64
    assert all(request.headers["cookie"] == "new-cookie" for request in requests)
    assert json.loads(requests[0].content) == {"MessageID": "msg-1"}
    assert (await world.sessions.get_session(session.id)).adapter_snapshot == original
    responses = [event for event in await world.events(session.id) if event.type == "llm.response"]
    mismatch = responses[-1].payload["upstream"].get("identity_mismatch", {})
    assert "app_id" not in mismatch
    assert mismatch["version"]["expected"] == "47"


async def test_partial_refresh_failure_preserves_existing_metrics_and_records_attempt(world, client):
    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router, observe(TRACE, [llm_span("a")]))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        before = await world.sessions.wait_trace(turn.id)
        assert before.usage["trace_status"] == "partial"
        stats = (await world.sessions.get_session(session.id)).stats
        router.post(OBSERVE).mock(return_value=httpx.Response(403))
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")
    done = response.json()
    assert done["usage"]["trace_status"] == "partial"
    assert done["latency_ms"] == before.latency_ms
    assert done["usage"]["llm_calls"] == before.usage["llm_calls"]
    assert done["usage"]["trace_refresh"]["result_status"] == "unavailable"
    assert "Cookie／CSRF" in done["usage"]["trace_refresh"]["error"]
    assert (await world.sessions.get_session(session.id)).stats == stats


async def test_refresh_credential_override_and_original_configuration(world, client):
    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router)
        router.post(OBSERVE).mock(return_value=httpx.Response(403))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        await world.sessions.wait_trace(turn.id)
    fresh = await make_credential(client, {"api_key": "new", "observe_headers": {"Cookie": "new"}})
    with respx.mock() as router:
        route = router.post(OBSERVE).mock(side_effect=observe(TRACE, [llm_span("a")]))
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={
            "config_source": "original", "credential_ref": fresh,
        })
    assert response.status_code == 200
    assert route.calls[0].request.headers["cookie"] == "new"
    assert response.json()["usage"]["trace_refresh"]["config_source"] == "original"
    invalid = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh", json={"credential_ref": "missing"})
    assert invalid.status_code == 400


async def test_refresh_without_message_id_is_explicitly_unavailable(world, client):
    _, session = await make_session(world, client)
    with respx.mock() as router:
        router.post(CREATE).mock(return_value=httpx.Response(403))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
    response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")
    assert response.status_code == 400 and "没有上游消息 ID" in response.json()["error"]["message"]


async def test_refresh_serializes_queries_and_blocks_new_turns(world, client):
    from evalp.errors import SessionBusy

    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router)
        router.post(OBSERVE).mock(return_value=httpx.Response(403))
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        await world.sessions.wait_trace(turn.id)
    started, release = asyncio.Event(), asyncio.Event()
    handler = observe(TRACE, [llm_span("a")])
    async def query(request):
        started.set()
        await release.wait()
        return handler(request)
    with respx.mock() as router:
        router.post(OBSERVE).mock(side_effect=query)
        task = asyncio.create_task(world.sessions.refresh_trace(turn.id))
        await started.wait()
        try:
            duplicate = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")
            assert duplicate.status_code == 400 and "正在重查" in duplicate.json()["error"]["message"]
            with pytest.raises(SessionBusy, match="重查 Trace"):
                await world.sessions.run_turn(session.id, "新的指令")
        finally:
            release.set()
            await task


async def test_connection_check_distinguishes_key_and_cookie_authentication(world, client):
    _, session = await make_session(world, client)
    binding = session.adapter_snapshot["binding"]
    with respx.mock() as router:
        published = router.post("https://hiagent.example/api/v1/query_run_app_process").mock(
            return_value=httpx.Response(200, json={"status": "failed"}))
        observed = router.post(OBSERVE).mock(return_value=httpx.Response(200, json={"Result": {}}))
        result = (await client.post("/api/v1/adapters/check-connection", json=binding)).json()
        assert result["ok"] and result["details"]["llm_requested"] is False
        assert result["details"]["api_key"] == result["details"]["observe"] == "verified"
        published.mock(return_value=httpx.Response(400, json={"ResponseMetadata": {"Error": {
            "Code": "BadRequest.Warning", "Message": "Bad request: only support workflow type app",
        }}}))
        result = (await client.post("/api/v1/adapters/check-connection", json=binding)).json()
        assert result["ok"] and result["details"]["published_probe"] == "chat_app_type_check"
        observed.mock(return_value=httpx.Response(403))
        result = (await client.post("/api/v1/adapters/check-connection", json=binding)).json()
        assert not result["ok"] and "Cookie／CSRF" in result["message"]
        published.mock(return_value=httpx.Response(403))
        result = (await client.post("/api/v1/adapters/check-connection", json=binding)).json()
        assert not result["ok"] and "Key" in result["message"]

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
