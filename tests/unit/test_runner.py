"""Runner：事件顺序校验与自动补齐、超时 / 取消、异常转 error 事件、用量汇总、TurnOutput 组装。"""

import asyncio
import time

import pytest
from evalp.errors import ProviderError
from evalp.runtime.adapters.base import AgentAdapter, RunContext
from evalp.runtime.events import ev
from evalp.runtime.messages import Message
from evalp.runtime.runner import PARTIAL_TEXT_MAX, Runner

USER = Message.user("你好")
FINAL = Message(role="assistant", content="你好呀")


class ScriptAdapter(AgentAdapter):
    """按脚本产出事件的假 Adapter；脚本里的数字表示 sleep 秒数，异常实例会被抛出。"""

    def __init__(self, script):
        self.script = script
        self.cleaned = False
        self.started = asyncio.Event()

    async def run_turn(self, ctx, history, user_input):
        self.started.set()
        try:
            for item in self.script:
                if isinstance(item, int | float):
                    await asyncio.sleep(item)
                elif isinstance(item, BaseException):
                    raise item
                elif callable(item):
                    item(ctx)
                else:
                    yield item
        finally:
            self.cleaned = True


def make_ctx(timeout=5.0):
    return RunContext(
        session_id="ses_x", turn_id="trn_x", version=None, app=None, deadline=time.monotonic() + timeout
    )


async def run(adapter, ctx=None, history=None, **kw):
    events = []

    async def emit(e):
        events.append(e)

    result = await Runner().run(adapter, ctx or make_ctx(), history or [], USER, emit, **kw)
    return result, events


def types(events):
    return [e.type for e in events]


async def test_auto_fill_turn_start_and_end():
    adapter = ScriptAdapter([ev("message.assistant", message=FINAL)])
    result, events = await run(
        adapter, history=[Message.user("旧"), Message(role="assistant", content="旧答")]
    )
    assert types(events) == ["turn.start", "message.assistant", "turn.end"]
    assert (
        events[0].payload["input"] == {"role": "user", "content": "你好"}
        and events[0].payload["history_len"] == 2
    )
    assert all(e.turn_id == "trn_x" for e in events)
    end = events[-1].payload
    assert end["status"] == "completed" and end["output"]["final"]["content"] == "你好呀"
    assert result.status == "ok" and result.output.final == FINAL and result.output.messages == [FINAL]
    assert result.error is None and result.end_status == "completed"


async def test_adapter_turn_start_is_normalised_and_duplicates_dropped():
    adapter = ScriptAdapter(
        [
            ev("turn.start", input={"role": "user", "content": "被篡改"}, history_len=99, custom="保留"),
            ev("turn.start", input={"role": "user", "content": "重复"}, history_len=0),
            ev("message.assistant", message=FINAL),
        ]
    )
    _, events = await run(adapter)
    assert types(events) == ["turn.start", "message.assistant", "turn.end"]
    p = events[0].payload
    assert p["input"]["content"] == "你好" and p["history_len"] == 0 and p["custom"] == "保留"


async def test_events_after_agent_turn_end_are_dropped_and_hints_used():
    adapter = ScriptAdapter(
        [
            ev("message.assistant", message=FINAL),
            ev("turn.end", status="completed", usage={"prompt_tokens": 12, "completion_tokens": 3}, cost=0.5),
            ev("log", message="结束之后的杂音"),
        ]
    )
    result, events = await run(adapter)
    assert types(events) == ["turn.start", "message.assistant", "turn.end"]
    assert result.usage["prompt_tokens"] == 12 and result.usage["completion_tokens"] == 3
    assert result.usage["llm_calls"] == 0 and result.cost == 0.5
    assert events[-1].payload["usage"]["prompt_tokens"] == 12


async def test_turn_end_output_used_when_no_message_event():
    adapter = ScriptAdapter([ev("turn.end", status="completed", output={"final": FINAL.model_dump()})])
    result, events = await run(adapter)
    assert types(events) == ["turn.start", "message.assistant", "turn.end"]
    assert result.status == "ok" and result.output.final.content == "你好呀"


async def test_missing_final_message_is_protocol_error():
    result, events = await run(ScriptAdapter([ev("log", message="只有日志")]))
    assert types(events) == ["turn.start", "log", "error", "turn.end"]
    assert result.status == "error" and result.error["code"] == "AGENT_PROTOCOL_ERROR"
    assert "message.assistant" in result.error["message"]
    assert (
        events[-1].payload["status"] == "error"
        and events[-1].payload["error"]["code"] == "AGENT_PROTOCOL_ERROR"
    )


async def test_empty_adapter_still_gets_start_and_end():
    result, events = await run(ScriptAdapter([]))
    assert types(events) == ["turn.start", "error", "turn.end"] and result.status == "error"


async def test_invalid_assistant_message_is_error():
    bad = ev("message.assistant", message={"role": "robot", "content": "x"})
    result, events = await run(ScriptAdapter([bad]))
    assert result.status == "error" and result.error["code"] == "AGENT_PROTOCOL_ERROR"
    assert types(events)[-1] == "turn.end"


async def test_error_event_marks_turn_failed():
    adapter = ScriptAdapter(
        [
            ev("message.assistant", message=FINAL),
            ev("error", code="TOOL_DOWN", message="挂了", retriable=True),
        ]
    )
    result, events = await run(adapter)
    assert types(events) == ["turn.start", "message.assistant", "error", "turn.end"]
    assert result.status == "error" and result.output is None
    assert result.error == {"code": "TOOL_DOWN", "message": "挂了", "retriable": True}


async def test_agent_reports_error_status_without_error_event():
    result, events = await run(ScriptAdapter([ev("turn.end", status="error")]))
    assert result.status == "error" and result.error["code"] == "AGENT_ERROR"
    assert types(events) == ["turn.start", "error", "turn.end"]


async def test_exception_becomes_error_event():
    result, events = await run(ScriptAdapter([ev("log", message="x"), RuntimeError("炸了")]))
    assert types(events) == ["turn.start", "log", "error", "turn.end"]
    assert result.status == "error" and result.error["code"] == "INTERNAL_ERROR"
    assert "RuntimeError: 炸了" in result.error["message"] and result.error["retriable"] is False


async def test_evalp_error_keeps_code_and_retriable():
    exc = ProviderError("模型挂了", {"retriable": True})
    result, _ = await run(ScriptAdapter([exc]))
    assert result.error == {"code": "PROVIDER_ERROR", "message": "模型挂了", "retriable": True}


async def test_non_event_object_is_error():
    class Weird(AgentAdapter):
        async def run_turn(self, ctx, history, user_input):
            yield {"type": "log"}

    result, _ = await run(Weird())
    assert result.status == "error" and "AgentEvent" in result.error["message"]


async def test_timeout_kills_adapter_and_marks_timeout():
    adapter = ScriptAdapter([ev("log", message="开始"), 30, ev("message.assistant", message=FINAL)])
    t0 = time.monotonic()
    result, events = await run(adapter, make_ctx(timeout=0.2))
    assert time.monotonic() - t0 < 3
    assert adapter.cleaned  # Adapter 的 finally 已执行
    assert result.status == "timeout" and result.error["code"] == "TIMEOUT" and result.output is None
    assert types(events) == ["turn.start", "log", "error", "turn.end"]
    assert events[-1].payload["status"] == "timeout"


async def test_adapter_internal_timeout_is_not_turn_timeout():
    result, _ = await run(ScriptAdapter([TimeoutError("下游超时")]))
    assert result.status == "error" and result.error["code"] == "INTERNAL_ERROR"


async def test_cancel_marks_cancelled_and_cleans_up():
    adapter = ScriptAdapter([ev("log", message="开始"), 30])
    ctx = make_ctx(timeout=30)

    async def canceller():
        await adapter.started.wait()
        await asyncio.sleep(0.1)
        ctx.cancel.set()

    task = asyncio.create_task(canceller())
    t0 = time.monotonic()
    result, events = await run(adapter, ctx)
    await task
    assert time.monotonic() - t0 < 3 and adapter.cleaned
    assert result.status == "cancelled" and result.error is None and result.output is None
    assert types(events) == ["turn.start", "log", "turn.end"]
    assert events[-1].payload["status"] == "cancelled"


async def test_cancel_before_start():
    ctx = make_ctx()
    ctx.cancel.set()
    result, events = await run(ScriptAdapter([30]), ctx)
    assert result.status == "cancelled" and types(events)[-1] == "turn.end"


async def test_outer_task_cancellation_still_finishes_turn():
    adapter = ScriptAdapter([30])
    events = []

    async def emit(e):
        events.append(e)

    task = asyncio.create_task(Runner().run(adapter, make_ctx(30), [], USER, emit))
    await adapter.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.cleaned and types(events)[-1] == "turn.end" and events[-1].payload["status"] == "cancelled"


async def test_usage_cost_ttft_aggregation():
    def resp(p, c, cost):
        return ev(
            "llm.response", content="x", cost=cost, latency_ms=1,
            usage={"prompt_tokens": p, "completion_tokens": c, "estimated": c == 7},
        )  # fmt: skip

    adapter = ScriptAdapter(
        [
            0.05,
            ev("llm.delta", step_id="s1", text="你"),
            resp(10, 5, 0.001),
            ev("tool.call", step_id="s2", call_id="c1", name="a", arguments={}),
            ev("tool.call", step_id="s3", call_id="c2", name="b", arguments={}),
            resp(20, 7, 0.002),
            ev("message.assistant", message=FINAL),
        ]
    )
    result, events = await run(adapter)
    assert result.usage == {
        "prompt_tokens": 30, "completion_tokens": 12, "llm_calls": 2, "tool_calls": 2, "estimated": True,
    }  # fmt: skip
    assert result.cost == pytest.approx(0.003)
    assert result.ttft_ms is not None and 40 <= result.ttft_ms < result.latency_ms + 1
    end = events[-1].payload
    assert (
        end["ttft_ms"] == result.ttft_ms
        and end["latency_ms"] == result.latency_ms
        and end["cost"] == result.cost
    )


async def test_ttft_from_first_response_when_no_delta():
    result, _ = await run(
        ScriptAdapter([0.05, ev("llm.response", content="x"), ev("message.assistant", message=FINAL)])
    )
    assert result.ttft_ms is not None and result.ttft_ms >= 40


async def test_output_messages_come_from_new_messages():
    tool_call_msg = Message(
        role="assistant", content=None, tool_calls=[{"id": "c", "name": "t", "arguments": {}}]
    )
    tool_msg = Message(role="tool", tool_call_id="c", content="r")

    def fill(ctx):
        ctx.new_messages.extend([tool_call_msg, tool_msg, FINAL])

    result, _ = await run(ScriptAdapter([fill, ev("message.assistant", message=FINAL)]))
    assert [m.role for m in result.output.messages] == ["assistant", "tool", "assistant"]


async def test_before_end_runs_before_turn_end_is_emitted():
    order = []

    async def before_end(result):
        order.append(("before_end", result.status))

    events = []

    async def emit(e):
        order.append(e.type)
        events.append(e)

    await Runner().run(
        ScriptAdapter([ev("message.assistant", message=FINAL)]),
        make_ctx(),
        [],
        USER,
        emit,
        before_end=before_end,
    )
    assert order == ["turn.start", "message.assistant", ("before_end", "ok"), "turn.end"]


async def test_before_end_failure_does_not_block_turn_end():
    async def before_end(result):
        raise RuntimeError("db down")

    events = []

    async def emit(e):
        events.append(e)

    result = await Runner().run(
        ScriptAdapter([ev("message.assistant", message=FINAL)]),
        make_ctx(),
        [],
        USER,
        emit,
        before_end=before_end,
    )
    assert result.status == "ok" and events[-1].type == "turn.end"


# ---------------------------------------------------------------- 部分文本（取消 / 出错时保留已流出的内容）


def delta(text, step="s1"):
    return ev("llm.delta", step_id=step, text=text)


async def test_cancel_keeps_streamed_partial_text():
    adapter = ScriptAdapter([ev("llm.request", step_id="s1", model="m"), delta("你"), delta("好"), 30])
    ctx = make_ctx(timeout=30)

    async def canceller():
        await adapter.started.wait()
        await asyncio.sleep(0.1)
        ctx.cancel.set()

    task = asyncio.create_task(canceller())
    result, events = await run(adapter, ctx)
    await task
    assert result.status == "cancelled" and result.output is None and result.partial_text == "你好"
    end = events[-1].payload
    assert end["status"] == "cancelled" and end["partial_text"] == "你好" and end["output"] is None


async def test_error_after_deltas_keeps_partial_text():
    adapter = ScriptAdapter([delta("前半"), delta("句"), RuntimeError("连接断了")])
    result, events = await run(adapter)
    assert result.status == "error" and result.partial_text == "前半句"
    assert events[-1].payload["partial_text"] == "前半句"


async def test_llm_response_replaces_deltas_so_only_unfinished_call_is_partial():
    adapter = ScriptAdapter(
        [
            delta("已完成的一次调用", "s1"),
            ev("llm.response", step_id="s1", content="已完成的一次调用", finish_reason="tool_calls"),
            delta("第二次调用写到一半", "s2"),
            RuntimeError("boom"),
        ]
    )
    result, _ = await run(adapter)
    assert result.partial_text == "第二次调用写到一半"


async def test_no_partial_text_when_ok_or_nothing_streamed():
    ok, events = await run(
        ScriptAdapter([delta("你好呀"), ev("message.assistant", message=FINAL)]),
    )
    assert ok.status == "ok" and ok.partial_text is None and "partial_text" not in events[-1].payload
    failed, events = await run(ScriptAdapter([RuntimeError("x")]))
    assert (
        failed.status == "error" and failed.partial_text is None and "partial_text" not in events[-1].payload
    )


async def test_partial_text_is_truncated():
    adapter = ScriptAdapter([delta("长" * (PARTIAL_TEXT_MAX + 50)), RuntimeError("x")])
    result, _ = await run(adapter)
    assert len(result.partial_text) == PARTIAL_TEXT_MAX + 1 and result.partial_text.endswith("…")


async def test_tool_calls_with_the_same_call_id_in_different_steps_are_counted_separately():
    # 提供方没给 id 时，每个步骤内的 id 都是 call_0，不能互相覆盖。
    adapter = ScriptAdapter([
        ev("tool.call", step_id="s1:t0", call_id="call_0", name="f", arguments={}),
        ev("tool.result", step_id="s1:t0", call_id="call_0", name="f", output=1),
        ev("tool.call", step_id="s2:t0", call_id="call_0", name="f", arguments={}),
        ev("tool.result", step_id="s2:t0", call_id="call_0", name="f", output=2),
        ev("tool.result", step_id="s2:t0", call_id="call_0", name="f", output=2),
        ev("message.assistant", message=FINAL),
    ])
    result, _ = await run(adapter)
    assert result.usage["tool_calls"] == 2


async def test_latency_stops_at_the_final_reply_and_ignores_later_backfill():
    adapter = ScriptAdapter([
        0.05,
        ev("message.assistant", message=FINAL),
        0.6,  # 回答之后的补查（例如 Trace），不属于本轮用时
        ev("metrics", scope="turn", trace_fetch_ms=600),
    ])
    result, events = await run(adapter)
    assert result.status == "ok"
    assert 40 <= result.latency_ms < 400
    assert events[-1].payload["latency_ms"] == result.latency_ms
    assert result.usage["trace_fetch_ms"] == 600


async def test_latency_of_a_failed_turn_still_covers_the_whole_run():
    adapter = ScriptAdapter([ev("message.assistant", message=FINAL), 0.2, RuntimeError("补查后失败")])
    result, _ = await run(adapter)
    assert result.status == "error" and result.latency_ms >= 200


# ---------------------------------------------------------------- 远端为主：事后补齐

def remote_ctx(late):
    ctx = make_ctx()
    ctx.late_observe = late
    return ctx


SSE_PHASE = [
    ev("llm.delta", step_id="stage:1", text="你好", source="hiagent_sse"),
    ev("tool.call", step_id="tool:c", call_id="c", name="查", source="hiagent_sse"),
    ev("tool.result", step_id="tool:c", call_id="c", name="查", output=1, latency_ms=200,
       source="hiagent_sse"),
    ev("message.assistant", message=FINAL),
    ev("metrics", scope="turn", usage={"prompt_tokens": 9, "completion_tokens": 3, "source": "hiagent_sse",
                                       "observation_status": "partial"}, transport_ms=1234),
]


def trace_stream(*events):
    async def late():
        for e in events:
            yield e
    return late


async def test_remote_turn_has_no_canonical_timing_until_observed_and_keeps_local_as_diagnostics():
    ctx = remote_ctx(trace_stream())
    result, events = await run(ScriptAdapter([0.05, *SSE_PHASE]), ctx)
    assert result.status == "ok"
    assert result.latency_ms is None and result.ttft_ms is None
    assert events[-1].payload["latency_ms"] is None and events[-1].payload["ttft_ms"] is None
    assert result.usage["trace_status"] == "pending"
    assert result.usage["local_latency_ms"] >= 40 and result.usage["local_ttft_ms"] is not None
    assert result.usage["prompt_tokens"] == 9 and result.usage["tool_calls"] == 1  # SSE 整轮 token 已有
    assert result.usage["transport_ms"] == 1234


async def test_observe_rewrites_canonical_metrics_to_the_remote_values():
    trace = [
        ev("llm.request", step_id="llm:a", model="m", source="hiagent_trace"),
        ev("llm.response", step_id="llm:a", usage={"prompt_tokens": 4, "completion_tokens": 1},
           latency_ms=2000, source="hiagent_trace"),
        ev("tool.result", step_id="tool:c", call_id="c", name="查", output=1, latency_ms=1000,
           source="hiagent_trace"),
        ev("metrics", scope="turn", source="hiagent_trace", usage={"prompt_tokens": 9, "completion_tokens": 3,
           "source": "hiagent_sse", "observation_status": "complete"}, llm_calls=1, llm_api_ms=2000,
           tool_execution_ms=1000, upstream_run_ms=20000, trace_status="complete"),
    ]
    ctx = remote_ctx(trace_stream(*trace))
    result, _ = await run(ScriptAdapter(SSE_PHASE), ctx)
    emitted = []

    async def emit(e):
        emitted.append(e)

    final = await Runner().observe(ctx, result, emit)
    assert types(emitted) == ["llm.request", "llm.response", "tool.result", "metrics"]
    assert all(e.turn_id == "trn_x" for e in emitted)
    assert final.latency_ms == 20000 and final.ttft_ms is None
    assert final.usage["trace_status"] == "complete" and final.usage["upstream_run_ms"] == 20000
    assert final.usage["llm_calls"] == 1
    assert final.usage["tool_calls"] == 1  # SSE 与 Trace 的同一次工具调用只算一次
    assert final.usage["prompt_tokens"] == 9 and final.usage["llm_calls_status"] == "confirmed"
    assert final.usage["local_latency_ms"] == result.usage["local_latency_ms"]  # 本地值只作诊断
    assert "observe_ms" in final.usage


async def test_unavailable_trace_leaves_latency_empty_instead_of_using_the_local_value():
    unavailable = ev("metrics", scope="turn", trace_status="unavailable",
                     trace_note="凭据未提供 observe_headers")
    ctx = remote_ctx(trace_stream(unavailable))
    result, _ = await run(ScriptAdapter([0.05, *SSE_PHASE]), ctx)

    async def emit(e):
        return None

    final = await Runner().observe(ctx, result, emit)
    assert final.latency_ms is None and final.usage["local_latency_ms"] >= 40
    assert final.usage["trace_status"] == "unavailable" and "凭据" in final.usage["trace_note"]
    assert final.usage["llm_calls_status"] == "unknown"


async def test_observation_that_raises_is_reported_as_unavailable_not_pending_forever():
    async def broken():
        yield ev("log", message="开始")
        raise RuntimeError("解析炸了")

    ctx = remote_ctx(broken)
    result, _ = await run(ScriptAdapter(SSE_PHASE), ctx)
    emitted = []

    async def emit(e):
        emitted.append(e)

    final = await Runner().observe(ctx, result, emit)
    assert final.usage["trace_status"] == "unavailable" and "RuntimeError" in final.usage["trace_note"]
    assert emitted[-1].type == "metrics" and final.latency_ms is None


async def test_turn_without_late_observation_keeps_its_own_measurements():
    result, events = await run(ScriptAdapter([0.05, ev("llm.delta", step_id="s", text="x"),
                                              ev("message.assistant", message=FINAL)]))
    assert result.latency_ms >= 40 and result.ttft_ms is not None
    assert "trace_status" not in result.usage and "local_latency_ms" not in result.usage


async def test_http_bridge_uses_actual_trace_timing_and_keeps_raw_log_and_usage():
    raw = ev("log", message="耗时分项", metric_source="actual_upstream_trace_timestamps",
             upstream_run_ms=96886, llm_api_ms=80952, tool_execution_ms=14248, trace_query_ms=10000)
    adapter = ScriptAdapter([
        ev("llm.response", step_id="s1", content="答", usage={"prompt_tokens": 9, "completion_tokens": 3}),
        raw, ev("message.assistant", message=FINAL),
        ev("turn.end", status="completed", usage={"prompt_tokens": 999}),
    ])
    result, events = await run(adapter)
    assert result.latency_ms == 96886 and result.ttft_ms is None
    assert result.usage["prompt_tokens"] == 9 and result.usage["llm_calls"] == 1
    assert result.usage["trace_status"] == "complete"
    assert result.usage["llm_api_ms"] == 80952 and result.usage["tool_execution_ms"] == 14248
    assert result.usage["local_latency_ms"] is not None and result.usage["local_ttft_ms"] is not None
    assert "trace_query_ms" not in result.usage
    assert next(e for e in events if e.type == "log").payload == raw.payload
    assert types(events).index("log") + 1 == types(events).index("metrics")
    assert events[-1].payload["latency_ms"] == 96886


@pytest.mark.parametrize("value", [-1, True, "96886", None, float("inf"), float("nan")])
async def test_invalid_bridge_timing_does_not_override_local_duration(value):
    # AgentEvent 直接构造，避免 JSON 序列化先把非有限值改成 null。
    from evalp.runtime.events import AgentEvent

    raw = AgentEvent(type="log", payload={"message": "耗时分项", "upstream_run_ms": value,
                                         "metric_source": "actual_upstream_trace_timestamps"})
    result, events = await run(ScriptAdapter([raw, ev("message.assistant", message=FINAL)]))
    assert result.latency_ms is not None and "trace_status" not in result.usage
    assert "metrics" not in types(events)


async def test_bridge_log_requires_source_and_cannot_replace_explicit_trace_metrics():
    raw = ev("log", message="计时", upstream_run_ms=100, llm_api_ms=90, metric_source="local_clock")
    result, _ = await run(ScriptAdapter([raw, ev("message.assistant", message=FINAL)]))
    assert "upstream_run_ms" not in result.usage
    canonical = ev("metrics", source="hiagent_trace", trace_status="partial", upstream_run_ms=200)
    bridge = ev("log", message="耗时分项", metric_source="actual_upstream_trace_timestamps",
                upstream_run_ms=300)
    result, events = await run(ScriptAdapter([canonical, bridge, ev("message.assistant", message=FINAL)]))
    assert result.latency_ms == 200 and result.usage["trace_status"] == "partial"
    assert types(events).count("metrics") == 1
