"""真实协议身份、缺失指标、重复更新和当前 Trace 过滤。"""

import asyncio
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


def test_trace_rejects_other_message_before_emitting_any_updates():
    m = mapper()
    m.message_id = "current"
    trace = {"TraceID": "tr", "StartTime": "2026-11-01T00:00:00Z",
             "EndTime": "2026-11-01T00:00:01Z", "Status": {"Code": "OK"}}
    span = {"Context": {"TraceID": "tr", "SpanID": "sp"}, "Name": "model",
            "StartTime": trace["StartTime"], "EndTime": trace["EndTime"],
            "Attributes": {"gen_ai.span_type": "llm", "message_id": "other",
                           "app_id": "a", "gen_ai.agent.version": "47",
                           "input_tokens": 5, "output_tokens": 2, "output": "hi"}}
    page = {"Items": [span], "Total": 1, "HasMore": False}
    with pytest.raises(ValueError):
        trace_events(m.config, m, trace, page)
    span["Attributes"]["message_id"] = "current"
    events = trace_events(m.config, m, trace, page)
    assert len([e for e in events if e.type == "llm.response"]) == 1
    assert events[-1].payload["llm_api_ms"] == 1000
    assert m.usage["prompt_tokens"] == 5


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
