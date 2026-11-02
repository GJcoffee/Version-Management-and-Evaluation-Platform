"""真实协议身份、缺失指标、重复更新和当前 Trace 过滤。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from evalp.errors import AgentError
from evalp.runtime.adapters.base import AgentAdapter, RunContext
from evalp.runtime.adapters.hiagent_adapter import HiAgentMapper, sse_frames
from evalp.runtime.adapters.hiagent_trace import trace_events
from evalp.runtime.adapters.registry import HiAgentConfig
from evalp.runtime.events import ev
from evalp.runtime.messages import Message
from evalp.runtime.runner import Runner


def mapper():
    return HiAgentMapper(HiAgentConfig(
        base_url="https://example.com", app_id="a", user_id="u", published_version="47",
    ))


def test_tool_id_is_not_message_id_and_duplicate_id_is_counted_once():
    m = mapper()
    m.feed({"event": "message_start", "id": "message-1"})
    request = {"event": "agent_thought", "id": "call-1", "tool": "fetch",
               "tool_input": '{"url":"x"}'}
    assert any(e.type == "tool.call" for e in m.feed(request))
    assert not any(e.type == "tool.call" for e in m.feed(request))
    result = m.feed({"event": "agent_thought_end", "id": "call-1",
                     "observation": '{"ok":true}', "tool_latency": 0.012})
    assert m.message_id == "message-1"
    assert result[-1].payload["latency_ms"] == 12
    assert result[-1].step_id == "tool:call-1"
    m.feed({"event": "message_end", "id": "message-1"})
    assert m.done and m.usage["prompt_tokens"] is None


def test_reasoning_never_becomes_final_output_and_text_is_not_deduplicated():
    m = mapper()
    m.feed({"event": "think_message_output_start", "id": "think-1"})
    events = m.feed({"event": "think_message", "answer": "考虑"})
    assert events[-1].type == "llm.reasoning" and events[-1].persisted
    m.feed({"event": "message", "answer": "好"})
    m.feed({"event": "message", "answer": "好"})
    assert "".join(m.final_parts) == "好好"
    assert not any(e.type == "llm.response" for e in events)
    with pytest.raises(AgentError):
        m.feed({"event": "message_end", "message_id": "m1", "app_version": "48"})


async def test_sse_multiline_wrapped_json_and_tail_frame():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: message\nid: e1\ndata: {"answer":\ndata: "hi"}\n\n'
            yield b'data: "{\\"event\\":\\"message_end\\",\\"id\\":\\"m\\"}"'

    response = httpx.Response(200, stream=Stream())
    frames = [f async for f in sse_frames(response)]
    assert frames[0]["answer"] == "hi" and frames[0]["_event_id"] == "e1"
    assert frames[1]["event"] == "message_end"


async def test_runner_updates_same_llm_and_mcp_call_without_double_count():
    class Adapter(AgentAdapter):
        async def run_turn(self, ctx, history, user_input):
            yield ev("llm.response", step_id="l", content="x", usage={"prompt_tokens": 1})
            yield ev("llm.response", step_id="l", content="x",
                     usage={"prompt_tokens": 5, "completion_tokens": 2})
            yield ev("tool.call", step_id="t", call_id="c", name="f", source="hiagent_sse")
            yield ev("tool.result", step_id="t", call_id="c", name="f", output=1)
            yield ev("tool.result", step_id="t", call_id="c", name="f", output=1)
            yield ev("message.assistant", message=Message(role="assistant", content="x"))
            yield ev("metrics", usage={"prompt_tokens": 7, "completion_tokens": 3,
                                      "source": "hiagent_sse", "observation_status": "partial"})
            yield ev("turn.end", status="completed")

    context = RunContext(session_id="s", turn_id="t", version=SimpleNamespace(), app=SimpleNamespace())
    emitted = []

    async def emit(event):
        emitted.append(event)

    result = await Runner().run(Adapter(), context, [], Message.user("hi"), emit)
    assert result.status == "ok"
    assert result.usage["llm_calls"] == 1 and result.usage["tool_calls"] == 1
    assert result.usage["prompt_tokens"] == 7  # 整轮范围，不再加明细的 5


TRACE = {"TraceID": "tr", "StartTime": "2026-11-01T00:00:00Z",
         "EndTime": "2026-11-01T00:00:20Z", "Status": {"Code": "OK"}}


def llm_span(sid="sp", *, message="current", app="a", version="47", prompt=5, completion=2, **extra):
    attrs = {"gen_ai.span_type": "llm", "message_id": message, "app_id": app,
             "gen_ai.agent.version": version, "input_tokens": prompt, "output_tokens": completion,
             "input": json.dumps([{"role": "user", "content": "你好，帮我查订单"}], ensure_ascii=False),
             "output": json.dumps({"content": "订单已发货"}, ensure_ascii=False), **extra}
    return {"Context": {"TraceID": "tr", "SpanID": sid}, "Name": "model",
            "StartTime": "2026-11-01T00:00:01Z", "EndTime": "2026-11-01T00:00:03Z", "Attributes": attrs}


def page(*spans):
    return {"Items": list(spans), "Total": len(spans), "HasMore": False}


def current_mapper():
    m = mapper()
    m.message_id = "current"
    return m


def of_type(events, kind):
    return [e for e in events if e.type == kind]


def test_multi_segment_output_keeps_every_segment_in_the_final_message():
    m = mapper()
    m.feed({"event": "message_start", "id": "message-1"})
    m.feed({"event": "message_output_start", "id": "seg-1"})
    m.feed({"event": "message", "answer": "第一段，"})
    m.feed({"event": "message_output_start", "id": "seg-2"})
    m.feed({"event": "message", "answer": "第二段。"})
    m.feed({"event": "message_end", "id": "message-1"})
    assert "".join(m.final_parts) == "第一段，第二段。"


def test_trace_skips_a_span_of_another_message_but_keeps_the_rest():
    m = current_mapper()
    events = trace_events(m.config, m, TRACE, page(llm_span("other", message="other"), llm_span("mine")))
    assert [e.payload["upstream"]["span_id"] for e in of_type(events, "llm.response")] == ["mine"]
    skipped = events[-1].payload["trace_skipped"]
    assert skipped == [{"span_id": "other", "reason": "message_id 与本轮不一致"}]
    assert m.usage["prompt_tokens"] == 5


def test_trace_spans_not_fully_listed_is_not_ready_rather_than_failed():
    from evalp.runtime.adapters.hiagent_trace import TraceNotReady

    m = current_mapper()
    with pytest.raises(TraceNotReady):
        trace_events(m.config, m, TRACE, {**page(llm_span()), "HasMore": True})
    with pytest.raises(TraceNotReady):
        trace_events(m.config, m, TRACE, {**page(llm_span()), "Total": 2})


def test_trace_null_fields_are_missing_values_not_crashes():
    m = current_mapper()
    tool = {"Context": {"TraceID": "tr", "SpanID": "tool-1"}, "Status": None,
            "StartTime": "2026-11-01T00:00:03Z", "EndTime": "2026-11-01T00:00:04Z",
            "Attributes": {"gen_ai.span_type": "tool", "message_id": "current", "call_id": "c1"}}
    m.calls["c1"] = {"name": "fetch", "arguments": {}, "step": "tool:c1"}
    spans = [
        {"Context": None, "Attributes": None},
        {"Context": {"TraceID": "tr", "SpanID": "no-attrs"}, "Attributes": None},
        llm_span("null-generations", output=json.dumps({"generations": None})),
        llm_span("hollow-generations", output=json.dumps({"generations": [[None]]})),
        tool,
    ]
    events = trace_events(m.config, m, {**TRACE, "Status": None}, page(*spans))
    assert len(of_type(events, "llm.response")) == 2
    assert of_type(events, "tool.result")[0].payload["is_error"] is False


def test_trace_identity_differences_are_annotated_not_dropped():
    m = current_mapper()
    events = trace_events(m.config, m, TRACE, page(llm_span(app="other-app", version="v47")))
    response = of_type(events, "llm.response")[0]
    assert response.payload["upstream"]["identity_mismatch"] == {
        "app_id": {"expected": "a", "actual": "other-app"},
        "version": {"expected": "47", "actual": "v47"},
    }
    assert "identity_mismatch" in of_type(events, "llm.request")[0].payload["upstream"]


def test_trace_token_details_that_disagree_with_the_turn_total_are_kept():
    m = current_mapper()
    m.usage = {"prompt_tokens": 9, "completion_tokens": 3, "source": "hiagent_sse",
               "observation_status": "partial"}
    events = trace_events(m.config, m, TRACE, page(llm_span("one"), llm_span("two")))
    assert len(of_type(events, "llm.response")) == 2
    assert m.usage["prompt_tokens"] == 9 and m.usage["source"] == "hiagent_sse"
    metrics = events[-1].payload
    assert metrics["token_detail_mismatch"] == {
        "prompt_tokens": {"turn": 9, "details": 10}, "completion_tokens": {"turn": 3, "details": 4},
    }
    assert metrics["observation_status"] == "partial"


def test_llm_request_carries_the_span_input_and_response_the_output():
    m = current_mapper()
    plain = llm_span("plain", input="只有一段提示词")
    events = trace_events(m.config, m, TRACE, page(llm_span("chat"), plain))
    requests = {e.step_id: e.payload for e in of_type(events, "llm.request")}
    assert requests["llm:chat"]["messages"] == [{"role": "user", "content": "你好，帮我查订单"}]
    assert requests["llm:plain"]["messages"] == [{"role": "user", "content": "只有一段提示词"}]
    assert of_type(events, "llm.response")[0].payload["content"] == "订单已发货"


def test_trace_without_any_model_call_is_reported_not_raised():
    m = current_mapper()
    events = trace_events(m.config, m, TRACE, page())
    metrics = events[-1].payload
    assert metrics["trace_status"] == "partial" and "模型调用" in metrics["trace_note"]


def test_trace_events_emit_expected_totals():
    m = current_mapper()
    events = trace_events(m.config, m, TRACE, page(llm_span()))
    assert len(of_type(events, "llm.response")) == 1
    assert events[-1].payload["llm_api_ms"] == 2000 and events[-1].payload["llm_calls"] == 1
    assert events[-1].payload["upstream_run_ms"] == 20000
    assert m.usage["prompt_tokens"] == 5 and m.usage["observation_status"] == "complete"


async def test_cancel_runner_closes_stream():
    closed = asyncio.Event()

    class Adapter(AgentAdapter):
        async def run_turn(self, ctx, history, user_input):
            try:
                yield ev("llm.delta", step_id="x", text="partial")
                ctx.cancel.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    context = RunContext(session_id="s", turn_id="t", version=SimpleNamespace(), app=SimpleNamespace())

    async def emit(event):
        return None

    result = await Runner().run(Adapter(), context, [], Message.user("hi"), emit)
    assert result.status == "cancelled" and closed.is_set()
    assert result.partial_text == "partial"


async def test_cancel_observation_does_not_change_confirmed_execution():
    class Adapter(AgentAdapter):
        async def run_turn(self, ctx, history, user_input):
            yield ev("metrics", execution_status="completed",
                     usage={"observation_status": "partial", "source": "hiagent_sse"})
            yield ev("message.assistant", message=Message(role="assistant", content="done"))
            ctx.cancel.set()
            await asyncio.Event().wait()

    context = RunContext(session_id="s", turn_id="t", version=SimpleNamespace(), app=SimpleNamespace())

    async def emit(event):
        return None

    result = await Runner().run(Adapter(), context, [], Message.user("hi"), emit)
    assert result.status == "ok"
    assert result.output.final.content == "done"
    assert result.usage["observation_status"] == "partial"
