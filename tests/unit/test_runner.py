"""Runner：事件顺序校验与自动补齐、超时 / 取消、异常转 error 事件、用量汇总、TurnOutput 组装。"""

import asyncio
import time

import pytest
from evalp.errors import ProviderError
from evalp.runtime.adapters.base import AgentAdapter, RunContext
from evalp.runtime.events import ev
from evalp.runtime.messages import Message
from evalp.runtime.runner import Runner

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
