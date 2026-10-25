"""HttpAdapter：L1（JSON）、L2（NDJSON，用 evalp-sdk 写的示例 agent）、错误分类、取消 / 超时 / 健康检查。"""

import asyncio
import time

import pytest
from evalp.runtime.adapters.http_adapter import HttpAdapter
from evalp.spec.models import parse_spec

from tests.fixtures import sdk_agents
from tests.fixtures.agent_servers import RawAgentServer, json_response, ndjson_response

L1_OK = {
    "output": {"role": "assistant", "content": "L1 的回复"},
    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
}


@pytest.fixture
def sdk_server():
    handle = sdk_agents.handle.start_http(0)
    yield handle
    handle.close()


def types(events):
    return [e.type for e in events]


# ---------------------------------------------------------------- L2：evalp-sdk 示例 agent


async def test_l2_sdk_agent_full_trace(world, sdk_server):
    v = await world.http_version(sdk_server.url)
    s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.output.final.content == "echo[0]: 你好"
    assert t.usage == {"prompt_tokens": 11, "completion_tokens": 7, "llm_calls": 1, "tool_calls": 1}
    events = await world.events(s.id)
    assert types(events) == [
        "turn.start", "span.start", "span.end", "llm.request", "llm.response",
        "tool.call", "tool.result", "log", "message.assistant", "turn.end",
    ]  # fmt: skip
    assert events[0].payload["history_len"] == 0
    assert [e.step_id for e in events[1:8]] == ["s1", "s1", "s2", "s2", "s3", "s3", None]
    span_end = events[2].payload
    assert span_end["name"] == "retrieve" and span_end["attrs"] == {"query": "你好", "n": 2}
    # 第二轮：历史 = 上一轮的 user + assistant（代码型 agent 只记录最终回复）
    t2 = await world.sessions.run_turn(s.id, "再来")
    assert t2.output.final.content == "echo[2]: 再来"
    assert t2.idx == 1


async def test_request_contains_protocol_fields(world):
    with RawAgentServer(lambda h, b: json_response(h, L1_OK)) as srv:
        v = await world.http_version(
            srv.url, params={"retrieval_top_k": 8, "seed": 42}, runtime={"turn_timeout_s": 120}
        )
        s = await world.session(v, variables={"lang": "zh"})
        await world.sessions.run_turn(s.id, "你好")
        t2 = await world.sessions.run_turn(
            s.id, "第二问", variables={"extra": 1}, tool_mocks={"q": [{"default": 1}]}
        )
    first, second = (r["body"] for r in srv.requests)
    assert (
        first["protocol"] == "evalp-agent/1"
        and first["session_id"] == s.id
        and first["turn_id"].startswith("trn_")
    )
    assert first["history"] == [] and first["input"] == {"role": "user", "content": "你好"}
    assert first["params"] == {"retrieval_top_k": 8, "seed": 42} and first["variables"] == {"lang": "zh"}
    assert first["tool_mocks"] is None and first["seed"] == 42
    assert 100_000 < first["deadline_ms"] <= 120_000
    assert second["history"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "L1 的回复"},
    ]
    assert second["variables"] == {"lang": "zh", "extra": 1} and second["tool_mocks"] == {
        "q": [{"default": 1}]
    }
    assert t2.status == "ok"


async def test_variables_and_tool_mocks_reach_sdk_agent(world, sdk_server):
    v = await world.http_version(sdk_server.url)
    s = await world.session(v, variables={"city": "杭州"})
    t = await world.sessions.run_turn(s.id, "var:city")
    assert t.output.final.content == "杭州"
    mocks = {"query_order": [{"match": {"order_id": "A001"}, "output": {"status": "已发货"}}]}
    t = await world.sessions.run_turn(s.id, "tools", tool_mocks=mocks)
    assert "已发货" in t.output.final.content
    res = [e for e in await world.events(s.id) if e.type == "tool.result"][-1].payload
    assert res["mocked"] is True and res["output"] == {"status": "已发货"}


async def test_sdk_exception_becomes_turn_error(world, sdk_server):
    v = await world.http_version(sdk_server.url)
    s, t = await world.chat(v, "boom")
    assert t.status == "error" and t.error.code == "AGENT_ERROR" and "agent 内部出错了" in t.error.message
    events = await world.events(s.id)
    assert types(events)[-2:] == ["error", "turn.end"] and "traceback" in events[-2].payload


async def test_headers_env_substitution(world, monkeypatch):
    monkeypatch.setenv("MY_AGENT_TOKEN", "tok-123456")
    with RawAgentServer(lambda h, b: json_response(h, L1_OK)) as srv:
        v = await world.http_version(
            srv.url, http={"base_url": srv.url, "headers": {"Authorization": "Bearer ${env:MY_AGENT_TOKEN}"}}
        )
        _, t = await world.chat(v, "hi")
    assert t.status == "ok"
    assert srv.requests[0]["headers"]["Authorization"] == "Bearer tok-123456"


async def test_missing_env_var_fails_turn_readably(world, monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    with RawAgentServer(lambda h, b: json_response(h, L1_OK)) as srv:
        v = await world.http_version(
            srv.url, http={"base_url": srv.url, "headers": {"X-Key": "${env:NOPE_TOKEN}"}}
        )
        _, t = await world.chat(v, "hi")
    assert t.status == "error" and "NOPE_TOKEN" in t.error.message and srv.requests == []


# ---------------------------------------------------------------- L1


async def test_l1_json_response(world):
    with RawAgentServer(lambda h, b: json_response(h, {**L1_OK, "cost": 0.25})) as srv:
        v = await world.http_version(srv.url)
        s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.output.final.content == "L1 的回复"
    assert t.usage["prompt_tokens"] == 9 and t.usage["completion_tokens"] == 4 and t.cost == 0.25
    assert types(await world.events(s.id)) == ["turn.start", "message.assistant", "turn.end"]


async def test_l1_messages_become_history(world):
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "name": "t", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
        {"role": "assistant", "content": "最终"},
    ]
    with RawAgentServer(lambda h, b: json_response(h, {"output": msgs[-1], "messages": msgs})) as srv:
        v = await world.http_version(srv.url)
        s, t = await world.chat(v, "a")
        await world.sessions.run_turn(s.id, "b")
    assert [m.role for m in t.output.messages] == ["assistant", "tool", "assistant"]
    assert [m["role"] for m in srv.requests[1]["body"]["history"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


# ---------------------------------------------------------------- 错误分类


@pytest.mark.parametrize(
    "status,retriable",
    [(429, True), (502, True), (503, True), (504, True), (500, False), (400, False), (404, False)],
)
async def test_http_status_retriable_classification(world, status, retriable):
    with RawAgentServer(lambda h, b: json_response(h, {"detail": "出错了"}, status)) as srv:
        v = await world.http_version(srv.url)
        s, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "AGENT_ERROR" and t.error.retriable is retriable
    assert f"HTTP {status}" in t.error.message and "出错了" in t.error.message
    events = await world.events(s.id)
    err = next(e for e in events if e.type == "error")
    assert err.payload["retriable"] is retriable and err.payload["status"] == status


async def test_connection_refused_is_retriable(world):
    v = await world.http_version("http://127.0.0.1:9")
    _, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "AGENT_UNREACHABLE" and t.error.retriable is True


@pytest.mark.parametrize(
    "lines,expect",
    [
        (['{"type":"log","payload":{"message":"ok"}}', "这不是 JSON"], "第 2 行不是合法的 JSON"),
        (['{"type":"nope","payload":{}}'], "未知的事件类型"),
        (['{"type":"tool.call","payload":{"name":"x"}}'], "tool.call 的 payload 不合法"),
        (['{"payload":{}}'], "缺少字符串类型的 type"),
        (['["数组"]'], "事件必须是 JSON 对象"),
        (['{"type":"log","payload":"字符串"}'], "payload 必须是对象"),
    ],
)
async def test_invalid_ndjson_gives_readable_error(world, lines, expect):
    with RawAgentServer(lambda h, b: ndjson_response(h, lines)) as srv:
        v = await world.http_version(srv.url)
        s, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "AGENT_PROTOCOL_ERROR" and t.error.retriable is False
    assert expect in t.error.message
    events = await world.events(s.id)
    assert types(events)[0] == "turn.start" and types(events)[-1] == "turn.end"


@pytest.mark.parametrize(
    "body,expect",
    [
        (b"not json", "不是合法的 JSON"),
        ({"nothing": 1}, "output"),
        ({"output": "str"}, "必须是对象"),
        ({"output": {"role": "user", "content": "x"}}, "assistant"),
    ],
)
async def test_invalid_l1_gives_readable_error(world, body, expect):
    with RawAgentServer(lambda h, b: json_response(h, body)) as srv:
        v = await world.http_version(srv.url)
        _, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "AGENT_PROTOCOL_ERROR" and expect in t.error.message


async def test_ndjson_without_final_message_is_error(world):
    with RawAgentServer(
        lambda h, b: ndjson_response(h, [{"type": "log", "payload": {"message": "x"}}])
    ) as srv:
        v = await world.http_version(srv.url)
        _, t = await world.chat(v, "hi")
    assert t.status == "error" and "message.assistant" in t.error.message


# ---------------------------------------------------------------- 取消 / 超时 / 健康检查


async def test_cancel_notifies_agent_and_disconnects(world):
    def slow(h, body):
        ndjson_response(h, [{"type": "log", "payload": {"message": "开始"}}])
        time.sleep(30)

    with RawAgentServer(slow) as srv:
        v = await world.http_version(srv.url)
        s = await world.session(v)
        turn = await world.sessions.run_turn(s.id, "慢", wait=False)
        for _ in range(100):
            if srv.requests:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        t0 = time.monotonic()
        await world.sessions.cancel_turn(turn.id)
        assert time.monotonic() - t0 < 5
        for _ in range(100):
            if srv.cancels:
                break
            await asyncio.sleep(0.02)
    t = await world.sessions.get_turn(turn.id)
    assert t.status == "cancelled" and t.error is None
    assert srv.cancels[0]["turn_id"] == turn.id and srv.cancels[0]["session_id"] == s.id
    assert (await world.sessions.get(s.id)).session.status == "idle"


async def test_timeout_cancels_sdk_agent(world, sdk_server):
    v = await world.http_version(sdk_server.url, runtime={"turn_timeout_s": 0.5})
    t0 = time.monotonic()
    s, t = await world.chat(v, "sleep:30")
    assert time.monotonic() - t0 < 5
    assert t.status == "timeout" and t.error.code == "TIMEOUT"
    assert (await world.types(s.id))[-2:] == ["error", "turn.end"]
    # 平台断开 / 调用 cancel 后，SDK 里的 handler 已被取消
    for _ in range(100):
        if not sdk_server._tasks:
            break
        await asyncio.sleep(0.05)
    assert not sdk_server._tasks
    assert (await world.sessions.get(s.id)).session.status == "error"


async def test_healthcheck(world, sdk_server):
    adapter = HttpAdapter(
        parse_spec({"kind": "code", "transport": "http", "http": {"base_url": sdk_server.url}})
    )
    r = await adapter.healthcheck()
    assert r.ok and r.details["protocol"] == "evalp-agent/1" and r.latency_ms is not None
    await adapter.aclose()
    bad = HttpAdapter(
        parse_spec({"kind": "code", "transport": "http", "http": {"base_url": "http://127.0.0.1:9"}})
    )
    r = await bad.healthcheck()
    assert not r.ok and "无法访问" in r.message
    await bad.aclose()
    with RawAgentServer(lambda h, b: None, health={"ok": False}) as srv:
        a = HttpAdapter(parse_spec({"kind": "code", "transport": "http", "http": {"base_url": srv.url}}))
        assert not (await a.healthcheck()).ok
        await a.aclose()
    with RawAgentServer(lambda h, b: None, health=500) as srv:
        a = HttpAdapter(parse_spec({"kind": "code", "transport": "http", "http": {"base_url": srv.url}}))
        r = await a.healthcheck()
        assert not r.ok and "500" in r.message
        await a.aclose()
    with RawAgentServer(lambda h, b: None, health={"ok": True, "protocol": "other/9"}) as srv:
        a = HttpAdapter(parse_spec({"kind": "code", "transport": "http", "http": {"base_url": srv.url}}))
        r = await a.healthcheck()
        assert r.ok and "other/9" in r.message
        await a.aclose()
