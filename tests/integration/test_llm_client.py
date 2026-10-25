"""LLMClient + LLMRegistry（经 Fake OpenAI 内存传输层）。"""

import asyncio
import time

import pytest
from evalp.errors import ProviderError, SpecInvalid
from evalp.runtime.llm.client import LLMClient, RetryPolicy
from evalp.runtime.llm.ratelimit import PRIORITY_EVAL, PRIORITY_INTERACTIVE
from evalp.runtime.llm.registry import LLMRegistry
from evalp.runtime.messages import Message, ToolCall
from evalp.schemas.provider import ProviderUpdate
from evalp.services.provider import ProviderService

from tests.fixtures.fake_openai import Call, FakeError, Reply

MSGS = [Message(role="system", content="你是助手"), Message.user("你好")]


@pytest.fixture
def llm(ctx, fake_openai) -> LLMClient:
    client = ctx.svc(LLMRegistry).llm
    client.retry = RetryPolicy(base_delay=0.01, max_delay=0.05)
    return client


async def collect(stream):
    return [c async for c in stream]


# ------------------------------------------------------------------ 基本流式 / 非流式


async def test_stream_text_and_response(llm, fake_openai):
    fake_openai.enqueue("你好，我是助手，很高兴认识你")
    stream = llm.stream_chat("fake", "fake-model", MSGS, None, {"temperature": 0.2})
    chunks = await collect(stream)
    assert "".join(c.text for c in chunks) == "你好，我是助手，很高兴认识你"
    assert len(chunks) > 2 and all(c.tool_call_delta is None for c in chunks)
    r = stream.response
    assert r.content == "你好，我是助手，很高兴认识你"
    assert r.finish_reason == "stop" and r.tool_calls == []
    assert r.usage.prompt_tokens > 0 and r.usage.completion_tokens > 0 and not r.usage.estimated
    assert r.latency_ms >= r.ttft_ms >= 0 and r.attempts == 1
    # cost = (prompt * 2 + completion * 6) / 1e6
    assert r.cost == pytest.approx((r.usage.prompt_tokens * 2 + r.usage.completion_tokens * 6) / 1e6)
    assert r.to_message() == Message(role="assistant", content=r.content)
    # 请求内容
    req = fake_openai.chat_requests[0]
    assert req.stream and req.body["stream_options"] == {"include_usage": True}
    assert req.body["temperature"] == 0.2 and req.model == "fake-model"
    assert req.headers["authorization"] == "Bearer sk-fake-key"
    assert [m["role"] for m in req.messages] == ["system", "user"]


async def test_response_before_exhaustion_raises(llm, fake_openai):
    stream = llm.stream_chat("fake", "fake-model", MSGS, None)
    with pytest.raises(RuntimeError):
        _ = stream.response
    assert (await stream.result()).content == "echo: 你好"


async def test_stream_assembles_tool_calls(llm, fake_openai):
    fake_openai.enqueue(
        Reply(
            content="我来查一下",
            tool_calls=[
                Call("query_order", {"order_id": "A001", "备注": "加急"}, id="call_a"),
                ("get_weather", {"city": "北京"}),
            ],
            chunk_size=2,
        )
    )
    tools = [{"type": "function", "function": {"name": "query_order", "parameters": {"type": "object"}}}]
    stream = llm.stream_chat("fake", "fake-model", MSGS, tools)
    chunks = await collect(stream)
    deltas = [c.tool_call_delta for c in chunks if c.tool_call_delta]
    assert deltas[0]["index"] == 0 and deltas[0]["id"] == "call_a" and deltas[0]["name"] == "query_order"
    assert any(d["index"] == 1 and d.get("name") == "get_weather" for d in deltas)
    assert sum(1 for d in deltas if "arguments" in d) > 2  # 参数被拆成多个片段
    r = stream.response
    assert r.content == "我来查一下" and r.finish_reason == "tool_calls"
    assert r.tool_calls[0] == ToolCall(
        id="call_a", name="query_order", arguments={"order_id": "A001", "备注": "加急"}
    )
    assert r.tool_calls[1].name == "get_weather" and r.tool_calls[1].arguments == {"city": "北京"}
    assert r.tool_calls[1].id.startswith("call_fake_")
    assert fake_openai.chat_requests[0].tools == tools
    assert r.to_message().tool_calls == r.tool_calls
    assert r.event_fields()["tool_calls"][0]["arguments"] == {"order_id": "A001", "备注": "加急"}


async def test_invalid_tool_arguments_kept_as_string(llm, fake_openai):
    fake_openai.enqueue([Call("f", '{"a": 1, oops')])
    r = await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert r.tool_calls[0].arguments == '{"a": 1, oops'
    fake_openai.enqueue([Call("f", '"just a string"'), Call("g", "")])
    r = await llm.chat("fake", "fake-model", MSGS, None)
    assert r.tool_calls[0].arguments == '"just a string"' and r.tool_calls[1].arguments == {}


async def test_tool_messages_serialized_to_openai_format(llm, fake_openai):
    history = [
        Message.user("查订单"),
        Message(
            role="assistant", content=None, tool_calls=[ToolCall(id="c1", name="q", arguments={"id": "中"})]
        ),
        Message(role="tool", tool_call_id="c1", content="已发货"),
    ]
    await llm.chat("fake", "fake-model", history, None)
    sent = fake_openai.chat_requests[0].messages
    assert sent[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "q", "arguments": '{"id": "中"}'}}
        ],
    }
    assert sent[2] == {"role": "tool", "content": "已发货", "tool_call_id": "c1"}
    assert fake_openai.chat_requests[0].has_tool_message


async def test_params_passthrough_and_extra_body(llm, fake_openai):
    await llm.chat(
        "fake", "fake-model", MSGS, [], {"temperature": 0, "max_tokens": 50, "top_k": 20, "seed": 7}
    )
    body = fake_openai.chat_requests[0].body
    assert body["temperature"] == 0 and body["max_tokens"] == 50 and body["seed"] == 7
    assert body["top_k"] == 20  # 非标准参数走 extra_body，合并到顶层
    assert "tools" not in body  # 空列表不发送


async def test_non_stream_chat(llm, fake_openai):
    fake_openai.enqueue(Reply(content="完整回复", usage={"prompt_tokens": 100, "completion_tokens": 40}))
    r = await llm.chat("fake", "fake-model", MSGS, None)
    assert r.content == "完整回复" and r.usage.prompt_tokens == 100 and r.usage.completion_tokens == 40
    assert r.cost == pytest.approx((100 * 2 + 40 * 6) / 1e6)
    assert not fake_openai.chat_requests[0].stream
    assert "stream_options" not in fake_openai.chat_requests[0].body


async def test_cost_none_when_model_has_no_price(llm, fake_openai):
    r = await llm.chat("fake", "other-model", MSGS, None)
    assert r.cost is None and r.usage.total_tokens > 0


# ------------------------------------------------------------------ usage


async def test_usage_estimated_when_server_returns_none(llm, fake_openai):
    fake_openai.stream_usage = False
    r = await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert r.usage.estimated is True and r.usage.prompt_tokens > 5 and r.usage.completion_tokens > 0
    assert r.cost is not None and r.cost > 0
    fake_openai.enqueue(Reply(content="x" * 200, usage=False))
    r2 = await llm.chat("fake", "fake-model", MSGS, None)
    assert r2.usage.estimated is True and r2.usage.completion_tokens > 20


async def test_stream_options_unsupported_degrades(llm, fake_openai):
    fake_openai.supports_stream_options = False
    r = await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert r.content == "echo: 你好" and r.attempts == 1  # 降级不算重试
    first, second = fake_openai.chat_requests
    assert "stream_options" in first.body and "stream_options" not in second.body
    assert r.usage.estimated is True  # 没有 usage，走估算
    # 记住降级结果：后续请求不再带 stream_options
    await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert "stream_options" not in fake_openai.chat_requests[2].body
    assert len(fake_openai.chat_requests) == 3


# ------------------------------------------------------------------ 重试


async def test_retry_429_honors_retry_after(ctx, fake_openai):
    llm = ctx.svc(LLMRegistry).llm
    llm.retry = RetryPolicy(base_delay=0.001)  # 退避很小，等待时间只能来自 Retry-After
    fake_openai.fail(429, times=2, retry_after=0.15)
    start = time.monotonic()
    r = await llm.chat("fake", "fake-model", MSGS, None)
    elapsed = time.monotonic() - start
    assert r.content == "echo: 你好" and r.attempts == 3
    assert elapsed >= 0.28  # 两次各等约 0.15s
    assert len(fake_openai.chat_requests) == 3


async def test_retry_500_then_success_stream(llm, fake_openai):
    fake_openai.fail(500, times=1)
    fake_openai.fail(503, times=1)
    r = await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert r.content == "echo: 你好" and r.attempts == 3


async def test_non_retriable_error_raises_provider_error(llm, fake_openai):
    fake_openai.enqueue(FakeError(400, "context length exceeded"))
    with pytest.raises(ProviderError) as ei:
        await llm.chat("fake", "fake-model", MSGS, None)
    d = ei.value.details
    assert d["status"] == 400 and d["retriable"] is False and d["attempts"] == 1
    assert d["provider"] == "fake" and "context length" in ei.value.message
    assert ei.value.code == "PROVIDER_ERROR" and len(fake_openai.chat_requests) == 1
    fake_openai.required_api_key = "another"
    with pytest.raises(ProviderError) as ei:
        await llm.stream_chat("fake", "fake-model", MSGS, None).result()
    assert ei.value.details["status"] == 401 and ei.value.details["retriable"] is False


async def test_retries_exhausted(ctx, fake_openai):
    llm = ctx.svc(LLMRegistry).llm
    llm.retry = RetryPolicy(max_retries=2, base_delay=0.001)
    fake_openai.fail(500, times=None)
    with pytest.raises(ProviderError) as ei:
        await llm.chat("fake", "fake-model", MSGS, None)
    assert ei.value.details["retriable"] is True and ei.value.details["status"] == 500
    assert ei.value.details["attempts"] == 3 and len(fake_openai.chat_requests) == 3


async def test_retry_on_timeout(llm, fake_openai):
    fake_openai.slow(0.5, times=1)  # 第一次响应太慢
    r = await llm.chat("fake", "fake-model", MSGS, None, {"timeout": 0.1})
    assert r.attempts == 2 and r.content == "echo: 你好"
    fake_openai.slow_first_token(0.5, times=1)
    r = await llm.stream_chat("fake", "fake-model", MSGS, None, {"timeout": 0.1}).result()
    assert r.attempts == 2


async def test_timeout_exhausted_is_retriable_provider_error(ctx, fake_openai):
    llm = ctx.svc(LLMRegistry).llm
    llm.retry = RetryPolicy(max_retries=1, base_delay=0.001)
    fake_openai.slow(0.5)
    with pytest.raises(ProviderError) as ei:
        await llm.chat("fake", "fake-model", MSGS, None, {"timeout": 0.05})
    assert ei.value.details["retriable"] is True and ei.value.details["status"] is None


async def test_no_retry_after_first_token(llm, fake_openai):
    fake_openai.enqueue(Reply(content="这是一段会被截断的很长很长的回复内容"))
    fake_openai.break_stream(after_chunks=2)
    got: list[str] = []
    stream = llm.stream_chat("fake", "fake-model", MSGS, None)
    with pytest.raises(ProviderError) as ei:
        async for c in stream:
            got.append(c.text)
    assert len(got) == 2  # 已输出的增量已经交付给调用方
    assert ei.value.details["retriable"] is True and ei.value.details["attempts"] == 1
    assert len(fake_openai.chat_requests) == 1  # 没有重试
    assert llm.registry._limiters["fake"].inflight == 0  # 许可已归还


async def test_break_before_first_token_is_retried(llm, fake_openai):
    fake_openai.break_stream(after_chunks=0, times=1)
    stream = llm.stream_chat("fake", "fake-model", MSGS, None)
    chunks = await collect(stream)
    assert "".join(c.text for c in chunks) == "echo: 你好" and stream.response.attempts == 2


async def test_api_key_scrubbed_from_error(llm, fake_openai):
    fake_openai.enqueue(FakeError(400, "bad request near sk-fake-key and Bearer abcdefgh12345678"))
    with pytest.raises(ProviderError) as ei:
        await llm.chat("fake", "fake-model", MSGS, None)
    assert "sk-fake-key" not in ei.value.message and "abcdefgh12345678" not in ei.value.message


async def test_unknown_provider(llm):
    with pytest.raises(SpecInvalid):
        await llm.chat("nope", "m", MSGS, None)
    with pytest.raises(SpecInvalid):
        await llm.stream_chat("nope", "m", MSGS, None).result()


# ------------------------------------------------------------------ 限流集成


async def test_cancellation_releases_permit(llm, fake_openai, ctx):
    fake_openai.latency = 5

    async def run():
        await llm.stream_chat("fake", "fake-model", MSGS, None).result()

    t = asyncio.create_task(run())
    await asyncio.sleep(0.2)
    lim = llm.registry._limiters["fake"]
    assert lim.inflight == 1
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert lim.inflight == 0


async def test_priority_orders_queued_requests(llm, fake_openai, ctx):
    await ctx.svc(ProviderService).update(
        (await ctx.svc(ProviderService).get_by_name("fake")).id, ProviderUpdate(max_concurrency=1)
    )
    ctx.svc(LLMRegistry).invalidate()
    fake_openai.latency = 0.15

    async def call(text: str, prio: int):
        return await llm.chat("fake", "fake-model", [Message.user(text)], None, priority=prio)

    first = asyncio.create_task(call("first", PRIORITY_EVAL))
    await asyncio.sleep(0.05)
    low = asyncio.create_task(call("low", PRIORITY_EVAL))
    await asyncio.sleep(0.02)
    high = asyncio.create_task(call("high", PRIORITY_INTERACTIVE))
    await asyncio.gather(first, low, high)
    assert [r.last_user for r in fake_openai.chat_requests] == ["first", "high", "low"]
    assert fake_openai.max_inflight == 1
    assert (await high).queue_ms > 0


async def test_limit_changes_take_effect_and_tpm_settles(ctx, fake_openai):
    registry = ctx.svc(LLMRegistry)
    registry.cache_ttl = 0
    llm = registry.llm
    st = await registry.status("fake")
    assert st["rpm_left"] is None and st["tpm_left"] is None
    svc = ctx.svc(ProviderService)
    pid = (await svc.get_by_name("fake")).id
    await svc.update(pid, ProviderUpdate(rpm_limit=100, tpm_limit=600000, max_concurrency=3))
    r = await llm.chat("fake", "fake-model", MSGS, None, {"max_tokens": 1000})
    st = await registry.status("fake")
    assert st["rpm_limit"] == 100 and st["max_concurrency"] == 3
    assert 97 <= st["rpm_left"] <= 99
    # 预扣 prompt+1000，响应后按实际 usage（几十个）结算
    assert st["tpm_left"] >= 600000 - r.usage.total_tokens - 200


async def test_provider_update_rebuilds_client(ctx, fake_openai):
    registry = ctx.svc(LLMRegistry)
    registry.cache_ttl = 0
    svc = ctx.svc(ProviderService)
    pid = (await svc.get_by_name("fake")).id
    await registry.llm.chat("fake", "fake-model", MSGS, None)
    old = (await registry.get("fake")).client
    await svc.update(pid, ProviderUpdate(api_key="sk-rotated", headers={"X-Team": "a"}))
    await registry.llm.chat("fake", "fake-model", MSGS, None)
    assert (await registry.get("fake")).client is not old
    h = fake_openai.chat_requests[-1].headers
    assert h["authorization"] == "Bearer sk-rotated" and h["x-team"] == "a"
    # 无变化时复用同一个客户端
    same = (await registry.get("fake")).client
    await registry.llm.chat("fake", "fake-model", MSGS, None)
    assert (await registry.get("fake")).client is same


async def test_health_reports_limiter_state(client, ctx, fake_openai):
    registry = ctx.svc(LLMRegistry)
    registry.cache_ttl = 0
    svc = ctx.svc(ProviderService)
    await svc.update((await svc.get_by_name("fake")).id, ProviderUpdate(rpm_limit=100, tpm_limit=6000))
    h = (await client.get("/api/v1/health")).json()
    (p,) = h["providers"]
    assert p["name"] == "fake" and p["inflight"] == 0 and p["rpm_left"] == 100 and p["tpm_left"] == 6000
    fake_openai.latency = 0.4
    task = asyncio.create_task(registry.llm.chat("fake", "fake-model", MSGS, None))
    await asyncio.sleep(0.15)
    (p,) = (await client.get("/api/v1/health")).json()["providers"]
    assert p["inflight"] == 1 and p["rpm_left"] <= 99 and p["tpm_left"] < 6000
    await task
    (p,) = (await client.get("/api/v1/health")).json()["providers"]
    assert p["inflight"] == 0 and p["tpm_left"] > 5800
