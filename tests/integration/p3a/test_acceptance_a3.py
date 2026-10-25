"""验收 A3（详设 15.2）：配置型、HTTP、子进程三种 agent 都能对话；trace 完整（LLM 请求和响应、
工具调用、用量）；取消一轮后状态正确。"""

import asyncio

import pytest
from evalp.runtime.events import AgentEvent

from tests.fixtures import sdk_agents
from tests.fixtures.fake_openai import Call

from .conftest import SEARCH_TOOL_YAML


@pytest.fixture
def sdk_server():
    handle = sdk_agents.handle.start_http(0)
    yield handle
    handle.close()


def assert_complete_trace(events: list[AgentEvent], turn, *, tools: bool = True):
    types = [e.type for e in events]
    assert types[0] == "turn.start" and types[-1] == "turn.end"
    assert types.count("turn.start") == types.count("turn.end") == 1
    assert "llm.request" in types and "llm.response" in types
    if tools:
        assert "tool.call" in types and "tool.result" in types
    assert "message.assistant" in types
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    # 成对：每个 llm.request / tool.call 都有同 step_id 的结束事件
    for start, end in (("llm.request", "llm.response"), ("tool.call", "tool.result")):
        assert sorted(e.step_id for e in events if e.type == start) == sorted(
            e.step_id for e in events if e.type == end
        )
    # 用量：turn.end 与轮次一致，且来自 llm.response
    resp_tokens = sum(
        (e.payload.get("usage") or {}).get("prompt_tokens", 0) for e in events if e.type == "llm.response"
    )
    assert turn.usage["prompt_tokens"] == resp_tokens > 0
    assert turn.usage["llm_calls"] >= 1 and (not tools or turn.usage["tool_calls"] >= 1)
    assert (
        events[-1].payload["status"] == "completed"
        and events[-1].payload["usage"]["prompt_tokens"] == resp_tokens
    )


async def test_a3_config_agent(world, fake_openai):
    await world.api.asset("tool", "q", SEARCH_TOOL_YAML)
    v = await world.config_version({"tools": ["asset://tool/q@latest"]})
    fake_openai.add_rule(match={"has_tool": True}, reply="订单已发货")
    fake_openai.add_rule(match="订单", reply=[Call("query_order", {"order_id": "A001"})])
    s, t = await world.chat(v, "查订单 A001")
    assert t.status == "ok" and t.output.final.content == "订单已发货"
    assert_complete_trace(await world.events(s.id), t)


async def test_a3_http_agent(world, sdk_server):
    v = await world.http_version(sdk_server.url)
    s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.output.final.content.startswith("echo")
    assert_complete_trace(await world.events(s.id), t)


async def test_a3_subprocess_agent(world):
    v = await world.subprocess_version()
    s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.output.final.content.startswith("echo")
    assert_complete_trace(await world.events(s.id), t)


async def _cancel_and_check(world, v, text, *, fake=None):
    s = await world.session(v)
    turn = await world.sessions.run_turn(s.id, text, wait=False)
    for _ in range(250):
        if world.sessions.running_turn_id(s.id):
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.4)  # 让 agent 真正开始（子进程需要启动时间）
    await world.sessions.cancel_turn(turn.id)
    t = await world.sessions.get_turn(turn.id)
    assert t.status == "cancelled" and t.error is None and t.finished_at
    events = await world.events(s.id)
    assert events[0].type == "turn.start" and events[-1].type == "turn.end"
    assert events[-1].payload["status"] == "cancelled"
    detail = await world.sessions.get(s.id)
    assert detail.session.status == "idle" and detail.session.stats["turns"] == 1
    assert await world.sessions.get_history(s.id) == []  # 取消的轮次不进历史
    return s


async def test_a3_cancel_config(world, fake_openai):
    v = await world.config_version()
    fake_openai.slow(30)
    await _cancel_and_check(world, v, "慢")


async def test_a3_cancel_http(world, sdk_server):
    v = await world.http_version(sdk_server.url)
    await _cancel_and_check(world, v, "sleep:30")
    for _ in range(100):
        if not sdk_server._tasks:
            break
        await asyncio.sleep(0.05)
    assert not sdk_server._tasks  # agent 侧的 handler 也被取消了


async def test_a3_cancel_subprocess_kills_tree(world, tmp_path):
    import psutil

    pidfile = tmp_path / "pids"
    v = await world.subprocess_version(env={"AGENT_PIDFILE": str(pidfile)})
    await _cancel_and_check(world, v, "spawn:30")
    parent, child = (int(x) for x in pidfile.read_text().split())
    for _ in range(100):
        if not psutil.pid_exists(parent) and not psutil.pid_exists(child):
            break
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(parent) and not psutil.pid_exists(child)
