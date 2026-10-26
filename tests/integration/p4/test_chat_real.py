"""chat：对真实应用（进程内）+ fake OpenAI 的流式显示与 MCP chat 工具。"""

from __future__ import annotations

import sys

import pytest
from evalp.cli.chat import run_turn
from evalp.client import EvalpClient
from evalp.mcp.server import build_server
from mcp import Client

from tests.fixtures.fake_openai import Call
from tests.integration.p2a.conftest import TOOL_YAML, Api


@pytest.fixture
async def published(client, fake_openai):
    api = Api(client)
    await api.asset("tool", "query-order", TOOL_YAML)
    out = await api.agent(
        "support",
        spec={
            "kind": "config",
            "model": {"provider": "fake", "name": "fake-model"},
            "prompts": {"system": "你是客服。"},
            "tools": ["asset://tool/query-order@latest"],
        },
    )
    await api.publish(out["draft"])


async def test_run_turn_streams_text_and_tool_lines(client, published, fake_openai, capsys):
    fake_openai.add_rule(match="订单", reply=[Call("query_order", {"order_id": "A001"})], times=1)
    fake_openai.add_rule(match={"has_tool": True}, reply="订单已发货，请耐心等待")
    c = EvalpClient(http=client)
    from evalp import actions

    session = await actions.create_session(c, "support", "v1")
    status = await run_turn(c, session["id"], "我的订单 A001 到哪了")
    out = capsys.readouterr().out
    assert status == "ok"
    assert "调用工具 query_order" in out and "A001" in out
    assert "订单已发货，请耐心等待" in out
    assert out.count("订单已发货，请耐心等待") == 1  # 流式显示过就不再重复打印最终回复


async def test_chat_tool_against_real_app(client, published, fake_openai, project_dir):
    fake_openai.enqueue("您好，有什么可以帮您？", "好的，已记录。")
    server = build_server(project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as mc:
        r = await mc.call_tool("chat", {"message": "你好"})
        out = r.content[0].text
        assert not r.is_error, out
        assert "您好，有什么可以帮您？" in out and "session_id" in out
        sid = out.split("session_id: `")[1].split("`")[0]
        out2 = (await mc.call_tool("chat", {"message": "再见", "session_id": sid})).content[0].text
        assert "好的，已记录。" in out2 and sid in out2


# Windows 上 loop.add_signal_handler 不可用，run_turn 走的是 chat 命令里的 KeyboardInterrupt 回退；而且
# os.kill(pid, SIGINT) 在 Windows 上是 TerminateProcess（退出码 2），会直接杀掉 pytest 进程
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 信号语义")
async def test_ctrl_c_cancels_current_turn(client, published, fake_openai, capsys):
    import asyncio
    import os
    import signal

    from evalp import actions

    fake_openai.slow(30)
    c = EvalpClient(http=client)
    session = await actions.create_session(c, "support", "v1")
    asyncio.get_running_loop().call_later(0.6, os.kill, os.getpid(), signal.SIGINT)
    status = await asyncio.wait_for(run_turn(c, session["id"], "你好"), 15)
    assert status == "cancelled"
    assert "正在中断" in capsys.readouterr().out
    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turns"][0]["status"] == "cancelled"
