"""evalp-sdk：装饰器、TurnContext 的埋点、mock、stdio 与 HTTP 两种运行方式（不依赖平台后端）。"""

import asyncio
import io
import json
import time
import urllib.error
import urllib.request

import pytest
from evalp_sdk import Message, TurnContext, agent

REQ = {
    "protocol": "evalp-agent/1",
    "session_id": "ses_1",
    "turn_id": "trn_1",
    "history": [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}],
    "input": {"role": "user", "content": "新问题"},
    "params": {"top_k": 3},
    "variables": {"lang": "zh"},
    "tool_mocks": {"search": [{"match": {"q": "a"}, "output": {"hits": 1}}, {"default": {"hits": 0}}]},
    "seed": 7,
    "deadline_ms": 1000,
}


async def run(a, request=REQ):
    events: list[dict] = []
    status = await a.execute(request, events.append)
    return status, events


def types(events):
    return [e["type"] for e in events]


async def test_str_return_autogenerates_events():
    @agent
    async def a(ctx: TurnContext):
        return (
            f"{ctx.input.text()}|{len(ctx.history)}|{ctx.params['top_k']}|{ctx.variables['lang']}|{ctx.seed}"
        )

    status, ev = await run(a)
    assert status == "completed" and types(ev) == ["turn.start", "message.assistant", "turn.end"]
    assert ev[0]["payload"]["history_len"] == 2
    assert ev[1]["payload"]["message"] == {"role": "assistant", "content": "新问题|2|3|zh|7"}
    assert ev[2]["payload"]["status"] == "completed" and ev[2]["payload"]["latency_ms"] >= 0
    assert all("ts" in e for e in ev)


async def test_sync_handler_and_message_return():
    @agent
    def a(ctx: TurnContext):
        ctx.log("同步 handler", level="warn")
        return Message.assistant("好")

    status, ev = await run(a)
    assert status == "completed" and types(ev) == ["turn.start", "log", "message.assistant", "turn.end"]
    assert ev[1]["payload"] == {"level": "warn", "message": "同步 handler"}


async def test_exception_becomes_error_and_turn_end():
    @agent
    async def a(ctx):
        raise ValueError("坏了")

    status, ev = await run(a)
    assert status == "error" and types(ev) == ["turn.start", "error", "turn.end"]
    assert ev[1]["payload"]["code"] == "AGENT_ERROR" and "ValueError: 坏了" in ev[1]["payload"]["message"]
    assert ev[2]["payload"]["status"] == "error"


@pytest.mark.parametrize("value", [None, 123, Message(role="user", content="x")])
async def test_bad_return_is_error(value):
    @agent
    async def a(ctx):
        return value

    status, ev = await run(a)
    assert status == "error" and types(ev)[-2:] == ["error", "turn.end"]


async def test_cancel_emits_cancelled_turn_end():
    @agent
    async def a(ctx):
        await asyncio.sleep(30)

    events: list[dict] = []
    task = asyncio.create_task(a.execute(REQ, events.append))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[-1]["type"] == "turn.end" and events[-1]["payload"]["status"] == "cancelled"


async def test_span_llm_tool_events_and_usage():
    @agent
    async def a(ctx: TurnContext):
        async with ctx.span("retrieve", query="q") as s:
            s.set(n=2)
            async with ctx.llm_call(
                model="m", provider="p", messages=[{"role": "user", "content": "x"}]
            ) as c:
                c.record(
                    content="内容", usage={"prompt_tokens": 10, "completion_tokens": 4}, finish_reason="stop"
                )
        async with ctx.tool_call("search", {"q": "a"}) as t:
            assert t.has_mock and t.mock_output == {"hits": 1}
            t.result(t.mock_output)
        async with ctx.tool_call("search", {"q": "zzz"}) as t2:
            assert t2.mock_output == {"hits": 0}
            t2.result(t2.mock_output)
        async with ctx.tool_call("other", {}) as t3:
            assert not t3.has_mock
            t3.error("没有这个工具")
        ctx.report_usage(prompt_tokens=1, completion_tokens=1)
        return "ok"

    status, ev = await run(a)
    assert status == "completed"
    assert types(ev) == [
        "turn.start", "span.start", "llm.request", "llm.response", "span.end",
        "tool.call", "tool.result", "tool.call", "tool.result", "tool.call", "tool.result",
        "message.assistant", "turn.end",
    ]  # fmt: skip
    by = {(e["type"], e.get("step_id")): e for e in ev}
    assert by[("llm.request", "s2")]["parent_step_id"] == "s1"  # 嵌套在 span 里
    assert by[("llm.response", "s2")]["payload"]["usage"]["prompt_tokens"] == 10
    assert by[("span.end", "s1")]["payload"]["attrs"] == {"query": "q", "n": 2}
    results = [e["payload"] for e in ev if e["type"] == "tool.result"]
    assert results[0]["output"] == {"hits": 1} and results[0].get("mocked") is True
    assert results[2]["is_error"] is True
    end = ev[-1]["payload"]
    assert end["usage"]["prompt_tokens"] == 11 and end["usage"]["completion_tokens"] == 5


async def test_llm_call_records_exception():
    @agent
    async def a(ctx: TurnContext):
        async with ctx.llm_call(model="m"):
            raise RuntimeError("llm 挂了")

    status, ev = await run(a)
    assert status == "error"
    resp = next(e for e in ev if e["type"] == "llm.response")
    assert "llm 挂了" in json.dumps(resp["payload"], ensure_ascii=False)


# ---------------------------------------------------------------- stdio


def stdio(a, text):
    out = io.StringIO()
    code = a.run_stdio(stdin=io.StringIO(text), stdout=out)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def test_run_stdio_roundtrip_and_bad_input():
    @agent
    def a(ctx):
        print("print 不会污染协议")
        return "你好"

    code, ev = stdio(a, json.dumps(REQ))
    assert code == 0 and types(ev) == ["turn.start", "message.assistant", "turn.end"]
    code, ev = stdio(a, "不是 JSON")
    assert code == 2 and types(ev) == ["error", "turn.end"] and ev[1]["payload"]["status"] == "error"
    code, ev = stdio(a, "[1, 2]")
    assert code == 2


# ---------------------------------------------------------------- HTTP


@pytest.fixture
def server():
    @agent
    async def a(ctx: TurnContext):
        if ctx.input.text() == "sleep":
            await asyncio.sleep(30)
        return f"收到 {ctx.input.text()}"

    handle = a.start_http(0)
    yield handle
    handle.close()


def post(url, body, timeout=10):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def test_http_health_run_and_404(server):
    with urllib.request.urlopen(f"{server.url}/evalp/health", timeout=5) as r:  # noqa: S310
        body = json.loads(r.read())
    assert body["ok"] is True and body["protocol"] == "evalp-agent/1"
    with post(f"{server.url}/evalp/run", REQ) as r:
        assert "ndjson" in r.headers["Content-Type"]
        ev = [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]
    assert types(ev) == ["turn.start", "message.assistant", "turn.end"]
    assert ev[1]["payload"]["message"]["content"] == "收到 新问题"
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"{server.url}/nope", timeout=5)  # noqa: S310
    assert ei.value.code == 404


def test_http_cancel_stops_running_turn(server):
    import threading

    got: list[dict] = []

    def call():
        with post(f"{server.url}/evalp/run", {**REQ, "input": {"role": "user", "content": "sleep"}}, 20) as r:
            got.extend(json.loads(line) for line in r.read().decode().splitlines() if line.strip())

    t = threading.Thread(target=call)
    t.start()
    for _ in range(100):
        if server._tasks:
            break
        time.sleep(0.02)
    assert server._tasks
    with post(f"{server.url}/evalp/cancel", {"turn_id": "trn_1"}) as r:
        assert json.loads(r.read())["ok"] in (True, False)
    t.join(10)
    assert not t.is_alive() and got[-1]["type"] == "turn.end" and got[-1]["payload"]["status"] == "cancelled"
    assert not server._tasks


def test_http_bad_request_body(server):
    req = urllib.request.Request(f"{server.url}/evalp/run", data=b"not json", method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
    assert ei.value.code == 400
