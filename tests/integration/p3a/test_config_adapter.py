"""ConfigAdapter（经 SessionService + Runner）：多步 / 并行工具、max_steps、变量、知识、tool_mocks。"""

import asyncio

import pytest
from evalp.runtime.messages import Message

from tests.fixtures.fake_openai import Call, Reply

from .conftest import FIXTURES, SEARCH_TOOL_YAML


def by_type(events, t):
    return [e for e in events if e.type == t]


async def test_plain_reply_and_trace(world, fake_openai):
    v = await world.config_version()
    fake_openai.enqueue("您好，请问有什么可以帮您？")
    s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.error is None
    assert t.output.final.content == "您好，请问有什么可以帮您？"
    assert t.output.messages == [t.output.final]
    assert t.usage["llm_calls"] == 1 and t.usage["prompt_tokens"] > 0 and t.usage["tool_calls"] == 0
    assert t.cost > 0 and t.latency_ms >= 0 and t.ttft_ms is not None
    events = await world.events(s.id)
    assert [e.type for e in events] == [
        "turn.start",
        "llm.request",
        "llm.response",
        "message.assistant",
        "turn.end",
    ]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    req = events[1].payload
    assert req["model"] == "fake-model" and req["provider"] == "fake"
    assert req["messages"][0] == {"role": "system", "content": "你是客服助手。"}
    assert events[-1].payload["status"] == "completed"
    assert events[-1].payload["output"]["final"]["content"] == "您好，请问有什么可以帮您？"
    # 请求里真的带了 system 与 user
    assert [m["role"] for m in fake_openai.chat_requests[0].messages] == ["system", "user"]
    detail = await world.sessions.get(s.id)
    assert detail.session.status == "idle" and detail.session.stats["turns"] == 1
    assert detail.session.stats["llm_calls"] == 1 and detail.turns[0].status == "ok"


async def test_multi_step_tool_calls(world, fake_openai):
    await world.api.asset("tool", "q", SEARCH_TOOL_YAML)
    v = await world.config_version({"tools": ["asset://tool/q@latest"]})
    fake_openai.add_rule(match={"has_tool": True}, reply="订单 A001 已发货")
    fake_openai.add_rule(match="订单", reply=[Call("query_order", {"order_id": "A001"}, id="call_1")])
    s, t = await world.chat(v, "查订单 A001")
    assert t.status == "ok" and t.output.final.content == "订单 A001 已发货"
    assert t.usage["llm_calls"] == 2 and t.usage["tool_calls"] == 1
    events = await world.events(s.id)
    types = [e.type for e in events]
    assert types == [
        "turn.start", "llm.request", "llm.response", "tool.call", "tool.result",
        "llm.request", "llm.response", "message.assistant", "turn.end",
    ]  # fmt: skip
    call, result = by_type(events, "tool.call")[0], by_type(events, "tool.result")[0]
    assert call.step_id == result.step_id and call.parent_step_id == events[2].step_id
    assert call.payload == {"call_id": "call_1", "name": "query_order", "arguments": {"order_id": "A001"}}
    assert result.payload["output"] == {"status": "已发货"} and result.payload["is_error"] is False
    # 工具 schema 传给了模型；第二次请求带上 assistant(tool_calls) 与 tool 消息
    assert fake_openai.chat_requests[0].tools[0]["function"]["name"] == "query_order"
    roles = [m["role"] for m in fake_openai.chat_requests[1].messages]
    assert roles == ["system", "user", "assistant", "tool"]
    # 本轮新增的消息 = assistant(tool_calls) + tool + assistant(final)，用于下一轮历史
    msgs = t.output.messages
    assert [m.role for m in msgs] == ["assistant", "tool", "assistant"]
    assert msgs[0].tool_calls[0].name == "query_order" and msgs[1].tool_call_id == "call_1"
    assert msgs[1].content == '{"status":"已发货"}' or "已发货" in msgs[1].content
    # 下一轮历史包含上一轮的工具调用
    fake_openai.reset()
    fake_openai.enqueue("好的")
    t2 = await world.sessions.run_turn(s.id, "谢谢")
    assert t2.idx == 1
    roles = [m["role"] for m in fake_openai.chat_requests[0].messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]


async def test_parallel_tool_calls_run_concurrently(world, fake_openai, tmp_path):
    slow = f"""\
name: slow
description: 慢工具
parameters: {{type: object, properties: {{n: {{type: integer}}}}}}
impl:
  type: python
  entry: sample_tools:slow_double
  workdir: {FIXTURES}
"""
    # 用两个 mock 之外的并发度验证：并行时总耗时接近单个，串行时约为总和
    await world.api.asset("tool", "slow", slow)
    for parallel in (True, False):
        v = await world.config_version(
            {"tools": ["asset://tool/slow@latest"], "runtime": {"parallel_tool_calls": parallel}},
            name=f"par-{parallel}",
        )
        fake_openai.reset()
        fake_openai.add_rule(
            match={"has_tool": False},
            reply=[
                Call("slow", {"n": 1}, id="c1"),
                Call("slow", {"n": 2}, id="c2"),
                Call("slow", {"n": 3}, id="c3"),
            ],
        )
        fake_openai.add_rule(match={"has_tool": True}, reply="完成")
        s, t = await world.chat(v, "并发")
        assert t.status == "ok", t.error
        events = await world.events(s.id)
        calls = by_type(events, "tool.call")
        results = by_type(events, "tool.result")
        assert [c.payload["call_id"] for c in calls] == ["c1", "c2", "c3"]
        assert sorted(r.payload["call_id"] for r in results) == ["c1", "c2", "c3"]
        assert all(r.payload["output"] == r.payload["output"] and not r.payload["is_error"] for r in results)
        order = [e.type for e in events if e.type in ("tool.call", "tool.result")]
        if parallel:
            assert order[:3] == ["tool.call"] * 3  # 先发出全部调用，再陆续收到结果
        else:
            assert order == ["tool.call", "tool.result"] * 3
        # 不管并行与否，tool 消息顺序与 tool_calls 一致
        tool_msgs = [m for m in fake_openai.chat_requests[1].messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3"]
        assert [m["content"] for m in tool_msgs] == ["2", "4", "6"]
        total_ms = t.latency_ms
        if parallel:
            assert total_ms < 2500  # 3 个各 0.5s 的工具并行
        await asyncio.sleep(0)


async def test_max_steps_exceeded(world, fake_openai):
    await world.api.asset("tool", "q", SEARCH_TOOL_YAML)
    v = await world.config_version({"tools": ["asset://tool/q@latest"], "runtime": {"max_steps": 3}})
    fake_openai.add_rule(reply=[Call("query_order", {"order_id": "A001"})])  # 永远调用工具
    s, t = await world.chat(v, "循环")
    assert t.status == "error" and t.output is None
    assert t.error.code == "MAX_STEPS_EXCEEDED" and t.error.retriable is False and "3" in t.error.message
    assert t.usage["llm_calls"] == 3 and t.usage["tool_calls"] == 3
    events = await world.events(s.id)
    assert events[-2].type == "error" and events[-2].payload["code"] == "MAX_STEPS_EXCEEDED"
    assert events[-1].type == "turn.end" and events[-1].payload["status"] == "error"
    detail = await world.sessions.get(s.id)
    assert detail.session.status == "error"
    # 失败的轮次不进入历史
    assert await world.sessions.get_history(s.id) == []


async def test_template_variables_precedence(world, fake_openai):
    v = await world.config_version(
        {"prompts": {"system": "公司={{ variables.company }}；语气={{ tone }}；渠道={{ variables.channel }}",
                     "variables": {"company": "Spec公司", "tone": "正式", "channel": "web"}}}
    )  # fmt: skip
    # Spec 变量 < 会话变量 < 单轮变量
    s = await world.session(v, variables={"company": "会话公司", "channel": "app"})
    fake_openai.enqueue("a", "b")
    await world.sessions.run_turn(s.id, "q1")
    assert fake_openai.chat_requests[0].system == "公司=会话公司；语气=正式；渠道=app"
    await world.sessions.run_turn(s.id, "q2", variables={"company": "单轮公司"})
    assert fake_openai.chat_requests[1].system == "公司=单轮公司；语气=正式；渠道=app"


async def test_undefined_variable_fails_turn(world, fake_openai):
    v = await world.config_version({"prompts": {"system": "你好 {{ variables.missing }}"}})
    s, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "PROMPT_RENDER_ERROR"
    assert "missing" in t.error.message
    assert fake_openai.chat_requests == []  # 没有调用模型
    assert await world.types(s.id) == ["turn.start", "error", "turn.end"]


async def test_template_is_sandboxed(world, fake_openai):
    v = await world.config_version({"prompts": {"system": "{{ ''.__class__.__mro__ }}"}})
    _, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "PROMPT_RENDER_ERROR"


async def test_prompt_asset_pin_and_inline(world, fake_openai):
    await world.api.asset("prompt", "sys", "我是资产里的提示词：{{ variables.x }}")
    v = await world.config_version(
        {"prompts": {"system": "asset://prompt/sys@latest", "variables": {"x": "1"}}}
    )
    fake_openai.enqueue("ok")
    await world.chat(v, "hi")
    assert fake_openai.chat_requests[0].system == "我是资产里的提示词：1"


async def test_knowledge_inject_and_now(world, fake_openai):
    await world.api.asset("knowledge", "style-guide", "# 风格\n请用敬语。")
    v = await world.config_version(
        {
            "prompts": {"system": "规范：\n{{ knowledge['style-guide'] }}\n今年={{ now.year }}"},
            "knowledge": {"inject": ["asset://knowledge/style-guide@latest"]},
        }
    )
    fake_openai.enqueue("ok")
    _, t = await world.chat(v, "hi")
    assert t.status == "ok"
    system = fake_openai.chat_requests[0].system
    assert "请用敬语。" in system and "今年=20" in system


async def test_knowledge_search_builtin(world, fake_openai):
    faq = "# 退货\n\n签收后 7 天内可以无理由退货，商品需保持完好。\n\n# 发货\n\n下单后 48 小时内发货。\n"
    await world.api.asset("knowledge", "faq", faq)
    v = await world.config_version(
        {
            "tools": [
                {
                    "builtin": "knowledge_search",
                    "with": {"sources": ["asset://knowledge/faq@latest"], "top_k": 2},
                }
            ]
        }
    )
    fake_openai.add_rule(
        match={"has_tool": False}, reply=[Call("knowledge_search", {"query": "退货 7 天"}, id="ks")]
    )
    fake_openai.add_rule(match={"has_tool": True}, reply="可以 7 天无理由退货")
    s, t = await world.chat(v, "能退货吗")
    assert t.status == "ok"
    result = by_type(await world.events(s.id), "tool.result")[0].payload
    assert result["is_error"] is False
    hits = result["output"]
    assert (
        hits
        and hits[0]["source"] == "faq"
        and "7 天" in hits[0]["text"]
        and hits[0]["chunk_id"].startswith("faq#")
    )
    assert len(hits) <= 2


async def test_tool_mocks_override_tool(world, fake_openai):
    real = f"""\
name: query_order
description: 真实实现
parameters: {{type: object, properties: {{order_id: {{type: string}}}}}}
impl: {{type: python, entry: "sample_tools:echo", workdir: {FIXTURES}}}
"""
    await world.api.asset("tool", "q", real)
    v = await world.config_version({"tools": ["asset://tool/q@latest"]})
    fake_openai.add_rule(match={"has_tool": False}, reply=[Call("query_order", {"order_id": "A001"})])
    fake_openai.add_rule(match={"has_tool": True}, reply="done")
    mocks = {"query_order": [{"match": {"order_id": "A001"}, "output": {"status": "被 mock 了"}}]}
    s, t = await world.chat(v, "查", tool_mocks=mocks)
    assert t.status == "ok"
    res = by_type(await world.events(s.id), "tool.result")[0].payload
    assert res["output"] == {"status": "被 mock 了"} and res["mocked"] is True
    # 没有 mock 时用工具自己的实现
    fake_openai.reset()
    fake_openai.add_rule(match={"has_tool": False}, reply=[Call("query_order", {"order_id": "A001"})])
    fake_openai.add_rule(match={"has_tool": True}, reply="done")
    s2, _ = await world.chat(v, "查")
    res2 = by_type(await world.events(s2.id), "tool.result")[0].payload
    assert res2["output"] == {"echo": {"order_id": "A001"}} and res2["mocked"] is False


async def test_unknown_tool_and_bad_arguments_are_fed_back(world, fake_openai):
    await world.api.asset("tool", "q", SEARCH_TOOL_YAML)
    v = await world.config_version({"tools": ["asset://tool/q@latest"]})
    fake_openai.add_rule(match={"has_tool": False}, reply=[Call("nope", {"x": 1}, id="bad")])
    fake_openai.add_rule(match={"has_tool": True}, reply="抱歉")
    s, t = await world.chat(v, "x")
    assert t.status == "ok"  # 工具错误交给模型处理，不让本轮失败
    res = by_type(await world.events(s.id), "tool.result")[0].payload
    assert res["is_error"] is True and "未知工具" in res["output"]


async def test_stream_deltas_are_published_not_stored(world, fake_openai, ctx):
    v = await world.config_version()
    fake_openai.enqueue(Reply(content="一二三四五六七八九十", chunk_size=2))
    s = await world.session(v)
    sub = ctx.bus.subscribe(f"session:{s.id}")
    t = await world.sessions.run_turn(s.id, "数数")
    live = []
    while sub.qsize():
        live.append(await sub.get())
    sub.close()
    deltas = [e for e in live if e.type == "llm.delta"]
    assert len(deltas) >= 3 and all(d.seq is None for d in deltas)
    assert "".join(d.payload["text"] for d in deltas) == "一二三四五六七八九十"
    assert deltas[0].payload["step_id"] == deltas[0].step_id
    assert "llm.delta" not in await world.types(s.id)
    assert [e.seq for e in live if e.type != "llm.delta"] == [1, 2, 3, 4, 5]
    assert t.status == "ok"


async def test_provider_error_becomes_turn_error(world, fake_openai, llm_fast):
    v = await world.config_version()
    fake_openai.fail(400, times=None)
    s, t = await world.chat(v, "hi")
    assert t.status == "error" and t.error.code == "PROVIDER_ERROR" and t.error.retriable is False
    assert await world.types(s.id) == ["turn.start", "llm.request", "error", "turn.end"]


async def test_history_is_passed_to_model(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.enqueue("我是机器人", "你刚才问了名字")
    await world.sessions.run_turn(s.id, "你是谁")
    await world.sessions.run_turn(s.id, "我刚才问了什么")
    msgs = fake_openai.chat_requests[1].messages
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("system", "你是客服助手。"),
        ("user", "你是谁"),
        ("assistant", "我是机器人"),
        ("user", "我刚才问了什么"),
    ]


@pytest.mark.parametrize("bad", [Message(role="assistant", content="x"), {"role": "tool", "content": "x"}])
async def test_input_must_be_user_message(world, bad):
    from evalp.errors import ValidationFailed

    v = await world.config_version()
    s = await world.session(v)
    with pytest.raises(ValidationFailed):
        await world.sessions.run_turn(s.id, bad)
