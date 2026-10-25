"""Fake OpenAI Server 自身的行为（规则、队列、故障注入、流式格式、真实 HTTP）。"""

import json
import re

import httpx
import pytest
from evalp.runtime.llm.client import LLMClient, RetryPolicy
from evalp.runtime.llm.registry import LLMRegistry
from evalp.runtime.messages import Message
from evalp.schemas.provider import ModelInfo, ProviderCreate
from evalp.services.provider import ProviderService

from tests.fixtures.fake_openai import Call, FakeError, FakeOpenAI, Reply, StreamingASGITransport

URL = "http://fake.test/v1/chat/completions"


@pytest.fixture
async def http():
    fake = FakeOpenAI()
    async with httpx.AsyncClient(
        transport=StreamingASGITransport(fake.app), base_url="http://fake.test"
    ) as c:
        c.fake = fake  # type: ignore[attr-defined]
        yield c


def body(text: str, **extra):
    return {"model": "fake-model", "messages": [{"role": "user", "content": text}], **extra}


async def ask(http, text, **extra):
    r = await http.post("/v1/chat/completions", json=body(text, **extra))
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def test_default_echo_and_models(http):
    assert await ask(http, "你好") == "echo: 你好"
    r = (await http.get("/v1/models")).json()
    assert r["object"] == "list" and r["data"][0]["id"] == "fake-model"
    assert [x.path for x in http.fake.requests] == ["/v1/chat/completions", "/v1/models"]
    assert len(http.fake.chat_requests) == 1
    http.fake.reset()
    assert http.fake.requests == []


async def test_rule_matching_forms(http):
    f = http.fake
    f.add_rule(match="订单", reply="订单规则")
    f.add_rule(match=re.compile(r"退款\s*\d+"), reply="正则规则")
    f.add_rule(match={"user": "你好", "model": "special"}, reply="模型规则")
    f.add_rule(match=lambda req: len(req.messages) > 2, reply="回调匹配")
    f.add_rule(match={"has_tool": True}, reply="工具后规则")
    assert await ask(http, "查订单 A1") == "订单规则"
    assert await ask(http, "我要退款 123") == "正则规则"
    assert await ask(http, "退款") == "echo: 退款"
    assert await ask(http, "你好") == "echo: 你好"
    r = await http.post("/v1/chat/completions", json={**body("你好"), "model": "special"})
    assert r.json()["choices"][0]["message"]["content"] == "模型规则"
    msgs = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
        {"role": "user", "content": "z"},
    ]
    r = await http.post("/v1/chat/completions", json={"model": "m", "messages": msgs})
    assert r.json()["choices"][0]["message"]["content"] == "回调匹配"
    msgs = [{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "c", "content": "res"}]
    r = await http.post("/v1/chat/completions", json={"model": "m", "messages": msgs})
    assert r.json()["choices"][0]["message"]["content"] == "工具后规则"
    with pytest.raises(ValueError):
        f.add_rule(match={"bogus": 1}, reply="x")


async def test_rule_times_queue_and_precedence(http):
    f = http.fake
    rule = f.add_rule(match="a", reply="规则 a", times=1)
    f.enqueue("队列 1", "队列 2")
    assert await ask(http, "a") == "规则 a"  # 规则优先于队列
    assert await ask(http, "a") == "队列 1"  # 规则用完后落到队列
    assert await ask(http, "b") == "队列 2"
    assert await ask(http, "b") == "echo: b"
    assert rule.hits == 1


async def test_callback_reply_sync_async_and_kinds(http):
    f = http.fake
    f.add_rule(match="同步", reply=lambda req: f"回调: {req.last_user}")

    async def acb(req):
        return [Call("tool_x", {"q": req.last_user})]

    f.add_rule(match="异步", reply=acb)
    f.add_rule(match="err", reply=lambda req: FakeError(503, "overloaded"))
    f.add_rule(match="dict", reply={"content": "字典回复", "finish_reason": "length"})
    assert await ask(http, "同步") == "回调: 同步"
    r = (await http.post("/v1/chat/completions", json=body("异步"))).json()["choices"][0]
    assert r["finish_reason"] == "tool_calls" and r["message"]["content"] is None
    assert r["message"]["tool_calls"][0]["function"] == {"name": "tool_x", "arguments": '{"q": "异步"}'}
    assert (await http.post("/v1/chat/completions", json=body("err"))).status_code == 503
    r = (await http.post("/v1/chat/completions", json=body("dict"))).json()["choices"][0]
    assert r["message"]["content"] == "字典回复" and r["finish_reason"] == "length"


async def test_fault_injection_status_and_retry_after(http):
    f = http.fake
    f.fail(429, times=2, retry_after=1.5)
    f.fail(500, times=1)
    r1 = await http.post("/v1/chat/completions", json=body("x"))
    assert r1.status_code == 429 and r1.headers["retry-after"] == "1.5"
    assert r1.json()["error"]["message"]
    assert (await http.post("/v1/chat/completions", json=body("x"))).status_code == 429
    assert (await http.post("/v1/chat/completions", json=body("x"))).status_code == 500
    assert (await http.post("/v1/chat/completions", json=body("x"))).status_code == 200
    assert len(f.chat_requests) == 4  # 失败的请求也被记录


async def test_stream_format_and_usage(http):
    f = http.fake
    f.enqueue(Reply(content="你好世界", tool_calls=[Call("f", {"a": 1}, id="call_1")], chunk_size=2))
    r = await http.post(
        "/v1/chat/completions", json=body("hi", stream=True, stream_options={"include_usage": True})
    )
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [x[6:] for x in r.text.split("\n\n") if x.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    objs = [json.loads(x) for x in frames[:-1]]
    assert objs[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(o["choices"][0]["delta"].get("content") or "" for o in objs if o["choices"])
    assert text == "你好世界"
    tc = [d for o in objs if o["choices"] for d in o["choices"][0]["delta"].get("tool_calls", [])]
    assert tc[0]["id"] == "call_1" and tc[0]["function"]["name"] == "f"
    assert "".join(d["function"].get("arguments", "") for d in tc) == '{"a": 1}'
    finals = [o for o in objs if o["choices"] and o["choices"][0]["finish_reason"]]
    assert finals[0]["choices"][0]["finish_reason"] == "tool_calls"
    assert objs[-1]["choices"] == [] and objs[-1]["usage"]["total_tokens"] > 0


async def test_stream_without_include_usage_or_when_disabled(http):
    f = http.fake
    r = await http.post("/v1/chat/completions", json=body("hi", stream=True))
    assert '"usage"' not in r.text
    f.stream_usage = False
    r = await http.post(
        "/v1/chat/completions", json=body("hi", stream=True, stream_options={"include_usage": True})
    )
    assert '"usage"' not in r.text


async def test_stream_options_rejected_when_unsupported(http):
    http.fake.supports_stream_options = False
    r = await http.post(
        "/v1/chat/completions", json=body("hi", stream=True, stream_options={"include_usage": True})
    )
    assert r.status_code == 400 and "stream_options" in r.json()["error"]["message"]


async def test_stream_break_is_visible_to_client(http):
    http.fake.break_stream(after_chunks=1)
    with pytest.raises(httpx.RemoteProtocolError):
        await http.post("/v1/chat/completions", json=body("一二三四五六七八", stream=True))


async def test_streaming_transport_honors_first_token_delay_and_timeout(http):
    import time

    http.fake.slow_first_token(0.4, times=1)
    t0 = time.monotonic()
    with pytest.raises(httpx.ReadTimeout):
        await http.post("/v1/chat/completions", json=body("x", stream=True), timeout=0.1)
    assert time.monotonic() - t0 < 0.35
    async with http.stream("POST", "/v1/chat/completions", json=body("x", stream=True)) as r:
        first = time.monotonic()
        async for _ in r.aiter_lines():
            break
        assert time.monotonic() - first < 0.3  # 首块先到，不用等整个响应写完


async def test_required_api_key(http):
    http.fake.required_api_key = "sk-right"
    assert (await http.post("/v1/chat/completions", json=body("x"))).status_code == 401
    r = await http.post("/v1/chat/completions", json=body("x"), headers={"Authorization": "Bearer sk-right"})
    assert r.status_code == 200


async def test_max_inflight_tracking(http):
    import asyncio

    http.fake.latency = 0.1
    await asyncio.gather(*(http.post("/v1/chat/completions", json=body("x")) for _ in range(5)))
    assert http.fake.max_inflight == 5 and http.fake.inflight == 0


# ------------------------------------------------------------------ fake_openai fixture / 真实 HTTP


async def test_fixture_registers_provider(fake_openai, ctx):
    p = await ctx.svc(ProviderService).get_by_name("fake")
    assert p.models[0].name == "fake-model" and p.models[0].price_in == 2.0 and p.has_api_key
    r = await ctx.svc(LLMRegistry).llm.chat("fake", "fake-model", [Message.user("ping")], None)
    assert r.content == "echo: ping" and r.cost and r.cost > 0
    assert fake_openai.chat_requests[0].last_user == "ping"


async def test_real_http_server_fixture(fake_openai_server, ctx):
    assert fake_openai_server.startswith("http://127.0.0.1:") and fake_openai_server.endswith("/v1")
    fake = fake_openai_server.fake
    fake.add_rule(match="工具", reply=[Call("lookup", {"k": "v"})])
    await ctx.svc(ProviderService).create(
        ProviderCreate(
            name="real", base_url=str(fake_openai_server), models=[ModelInfo(name="m", price_in=1.0)]
        )
    )
    llm = LLMClient(ctx.svc(LLMRegistry), retry=RetryPolicy(base_delay=0.01))
    r = await llm.chat("real", "m", [Message.user("普通问题")], None)
    assert r.content == "echo: 普通问题"
    stream = llm.stream_chat("real", "m", [Message.user("用工具")], None)
    chunks = [c async for c in stream]
    assert stream.response.tool_calls[0].name == "lookup" and chunks
    # 真实网络路径上的故障注入与重试
    fake.fail(429, times=1, retry_after=0.05)
    assert (await llm.chat("real", "m", [Message.user("x")], None)).attempts == 2
    fake.break_stream(after_chunks=1)
    from evalp.errors import ProviderError

    with pytest.raises(ProviderError) as ei:
        await llm.stream_chat("real", "m", [Message.user("一二三四五六七八九十")], None).result()
    assert ei.value.details["retriable"] is True
    r = await httpx.AsyncClient().get(f"{fake_openai_server.root}/v1/models")
    assert r.json()["data"][0]["id"] == "fake-model"
    assert len(fake.chat_requests) >= 5
