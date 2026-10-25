"""LLM 客户端（详设 6.8）：OpenAI 兼容协议的流式 / 非流式调用，带限流、重试、用量与计费。

典型用法（Adapter / 评估器里）::

    llm = ctx.svc(LLMRegistry).llm
    stream = llm.stream_chat("fake", "fake-model", messages, tools, {"temperature": 0}, priority=10)
    async for chunk in stream:                    # LLMChunk(text=..., tool_call_delta=...)
        yield ev("llm.delta", step_id=sid, text=chunk.text, tool_call_delta=chunk.tool_call_delta)
    resp = stream.response                        # 流结束后的 LLMResponse（或 await stream.result()）
    yield ev("llm.response", step_id=sid, **resp.event_fields())
    messages.append(resp.to_message())

    resp = await llm.chat("fake", "fake-model", messages, None, {}, priority=PRIORITY_EVAL)   # 非流式

重试规则：429 / 500 / 502 / 503 / 504 / 超时 / 连接错误按指数退避重试（见 :class:`RetryPolicy`，
优先遵守 ``Retry-After``）；**只在收到第一个 token 之前重试**，开始输出后出错直接抛
``ProviderError``（``details.retriable=True``）。不可重试的错误抛 ``ProviderError``
（``details``: provider、model、status、retriable、attempts、error_type）。
Provider 不存在抛 ``SpecInvalid``。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import openai
from pydantic import BaseModel, Field

from evalp.errors import EvalpError, ProviderError
from evalp.logging import get_logger
from evalp.runtime.events import redact
from evalp.runtime.llm.pricing import compute_cost
from evalp.runtime.llm.ratelimit import PRIORITY_EVAL, Permit
from evalp.runtime.llm.tokens import count_messages_tokens, count_tokens
from evalp.runtime.messages import Message, ToolCall

if TYPE_CHECKING:
    from evalp.runtime.llm.registry import LLMRegistry, ProviderHandle

log = get_logger(__name__)

RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_COMPLETION_ESTIMATE = 512  # 未设置 max_tokens 时，TPM 预扣的补全估算
# 直接作为 chat.completions.create 关键字参数传递的 params；其余放入 extra_body
_DIRECT_PARAMS = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "seed",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "response_format",
        "tool_choice",
        "parallel_tool_calls",
        "user",
        "logprobs",
        "top_logprobs",
        "reasoning_effort",
    }
)
_RESERVED_PARAMS = frozenset({"model", "messages", "tools", "stream", "stream_options", "timeout", "n"})


# --------------------------------------------------------------------------- 数据模型


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMChunk(BaseModel):
    """流式增量：text 为文本增量；tool_call_delta 形如 ``{index, id?, name?, arguments?}``（参数片段）。"""

    text: str | None = None
    tool_call_delta: dict[str, Any] | None = None


class LLMResponse(BaseModel):
    """一次调用合并后的完整结果。"""

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    ttft_ms: int | None = None
    cost: float | None = None
    model: str | None = None  # 服务端返回的模型名
    attempts: int = 1  # 实际请求次数（含重试）
    queue_ms: int = 0  # 在限流器里排队的时间

    def to_message(self) -> Message:
        """转为 assistant 消息。"""
        return Message(role="assistant", content=self.content, tool_calls=self.tool_calls or None)

    def event_fields(self) -> dict[str, Any]:
        """``ev("llm.response", **resp.event_fields())`` 所需的 payload 字段。"""
        return {
            "content": self.content,
            "tool_calls": [tc.model_dump(mode="json") for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": self.usage.model_dump(),
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "cost": self.cost,
        }


@dataclass
class RetryPolicy:
    """重试策略：指数退避（基础 1s、倍数 2、±20% 抖动、单次上限 30s、最多 4 次重试）。"""

    max_retries: int = 4
    base_delay: float = 1.0
    factor: float = 2.0
    jitter: float = 0.2
    max_delay: float = 30.0
    max_retry_after: float = 120.0  # 服务端要求等待的上限

    def delay(self, retry_index: int, retry_after: float | None = None) -> float:
        """第 retry_index（从 0 开始）次重试前的等待时间。"""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_retry_after)
        raw = self.base_delay * (self.factor**retry_index)
        raw *= 1 + random.uniform(-self.jitter, self.jitter)
        return min(raw, self.max_delay)


# --------------------------------------------------------------------------- 请求 / 响应转换


def tool_call_to_openai(tc: ToolCall) -> dict[str, Any]:
    args = tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments, ensure_ascii=False)
    return {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": args}}


def messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    """Message 列表 -> OpenAI 请求格式。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role}
        if m.tool_calls:
            d["content"] = m.content  # 允许为 None
            d["tool_calls"] = [tool_call_to_openai(tc) for tc in m.tool_calls]
        else:
            d["content"] = m.content if m.content is not None else ""
        if m.tool_call_id is not None:
            d["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            d["name"] = m.name
        out.append(d)
    return out


def parse_arguments(raw: str) -> dict[str, Any] | str:
    """解析工具参数；不是合法 JSON 对象时保留原字符串。"""
    if not raw or not raw.strip():
        return {}
    try:
        val = json.loads(raw)
    except ValueError:
        return raw
    return val if isinstance(val, dict) else raw


class _Accumulator:
    """把流式增量合并成完整响应。"""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: Usage | None = None
        self.model: str | None = None
        self._last_index = -1

    def feed(self, chunk: Any) -> list[LLMChunk]:
        """吸收一个原始 chunk，返回需要转发给调用方的增量。"""
        out: list[LLMChunk] = []
        if getattr(chunk, "model", None):
            self.model = chunk.model
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self.usage = Usage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
        for choice in getattr(chunk, "choices", None) or []:
            if getattr(choice, "index", 0) not in (0, None):
                continue
            delta = getattr(choice, "delta", None)
            if delta is not None:
                content = getattr(delta, "content", None)
                if content:
                    self.text.append(content)
                    out.append(LLMChunk(text=content))
                for tc in getattr(delta, "tool_calls", None) or []:
                    out.append(self._feed_tool_call(tc))
            if getattr(choice, "finish_reason", None):
                self.finish_reason = choice.finish_reason
        return out

    def _feed_tool_call(self, tc: Any) -> LLMChunk:
        fn = getattr(tc, "function", None)
        idx = getattr(tc, "index", None)
        tc_id = getattr(tc, "id", None)
        if idx is None:  # 个别服务端不给 index：有新 id 视为新调用，否则续写上一个
            idx = self._last_index + 1 if (tc_id or self._last_index < 0) else self._last_index
        self._last_index = idx
        slot = self.calls.setdefault(idx, {"id": None, "name": "", "args": []})
        name = getattr(fn, "name", None) if fn else None
        args = getattr(fn, "arguments", None) if fn else None
        if tc_id:
            slot["id"] = tc_id
        if name:
            slot["name"] += name
        if args:
            slot["args"].append(args)
        delta: dict[str, Any] = {"index": idx}
        if tc_id:
            delta["id"] = tc_id
        if name:
            delta["name"] = name
        if args:
            delta["arguments"] = args
        return LLMChunk(tool_call_delta=delta)

    @property
    def content(self) -> str | None:
        return "".join(self.text) or None

    def tool_calls(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for idx in sorted(self.calls):
            slot = self.calls[idx]
            calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{idx}",
                    name=slot["name"],
                    arguments=parse_arguments("".join(slot["args"])),
                )
            )
        return calls

    def estimated_completion_tokens(self) -> int:
        n = count_tokens("".join(self.text))
        for slot in self.calls.values():
            n += count_tokens(slot["name"]) + count_tokens("".join(slot["args"]))
        return n


def _retry_after(exc: BaseException) -> float | None:
    """从 APIStatusError 的响应头解析建议等待秒数。"""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    ms = headers.get("retry-after-ms")
    if ms:
        try:
            return float(ms) / 1000.0
        except ValueError:
            pass
    val = headers.get("retry-after")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(val).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


@dataclass
class _Failure:
    retriable: bool
    status: int | None
    message: str
    retry_after: float | None
    error_type: str


def _is_transport_error(exc: BaseException) -> bool:
    """httpx / httpx2 的传输层错误（连接失败、对端断开、超时）。"""
    for mod_name in ("httpx", "httpx2"):
        mod = sys.modules.get(mod_name)
        if mod is not None and isinstance(exc, (mod.TransportError, mod.TimeoutException)):
            return True
    return False


def classify(exc: BaseException) -> _Failure:
    """把底层异常归类为可重试 / 不可重试。"""
    etype = type(exc).__name__
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        return _Failure(status in RETRIABLE_STATUS, status, exc.message or str(exc), _retry_after(exc), etype)
    if isinstance(exc, openai.APITimeoutError | TimeoutError):
        return _Failure(True, None, "请求超时", None, etype)
    if isinstance(exc, openai.APIConnectionError) or _is_transport_error(exc):
        return _Failure(True, None, f"连接错误: {exc}", None, etype)
    if isinstance(exc, openai.APIError):  # 流中的 error 事件等
        status = getattr(exc, "status_code", None)
        return _Failure(status in RETRIABLE_STATUS, status, exc.message or str(exc), None, etype)
    return _Failure(False, None, f"{etype}: {exc}", None, etype)


# --------------------------------------------------------------------------- 流对象


class LLMStream:
    """``stream_chat`` 的返回值：异步迭代器；结束后用 ``.response`` 取合并结果。"""

    def __init__(self) -> None:
        self._gen: AsyncIterator[LLMChunk] | None = None
        self._response: LLMResponse | None = None

    def __aiter__(self) -> LLMStream:
        return self

    async def __anext__(self) -> LLMChunk:
        assert self._gen is not None
        return await self._gen.__anext__()

    @property
    def response(self) -> LLMResponse:
        """合并后的响应；必须在迭代结束之后访问。"""
        if self._response is None:
            raise RuntimeError("流尚未结束：请先迭代完毕，或使用 await stream.result()")
        return self._response

    async def result(self) -> LLMResponse:
        """消费剩余增量并返回合并后的响应。"""
        if self._response is None:
            async for _ in self:
                pass
        return self.response

    async def aclose(self) -> None:
        """提前终止（释放限流许可与连接）。"""
        if self._gen is not None and hasattr(self._gen, "aclose"):
            await self._gen.aclose()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- 客户端


class LLMClient:
    """面向 Adapter / 评估器的 LLM 调用入口，通过 :class:`LLMRegistry` 获取客户端与限流器。"""

    def __init__(
        self,
        registry: LLMRegistry,
        *,
        retry: RetryPolicy | None = None,
        request_timeout: float = 120.0,
    ) -> None:
        self.registry = registry
        self.retry = retry or RetryPolicy()
        self.request_timeout = request_timeout

    # -- 公共 API

    def stream_chat(
        self,
        provider_name: str,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        params: dict[str, Any] | None = None,
        *,
        priority: int = PRIORITY_EVAL,
    ) -> LLMStream:
        """流式对话。返回 :class:`LLMStream`，``async for`` 迭代增量，结束后读 ``.response``。"""
        stream = LLMStream()
        stream._gen = self._stream(stream, provider_name, model, messages, tools, params or {}, priority)
        return stream

    async def chat(
        self,
        provider_name: str,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        params: dict[str, Any] | None = None,
        *,
        priority: int = PRIORITY_EVAL,
    ) -> LLMResponse:
        """非流式对话（同样带限流与重试）。"""
        params = params or {}
        handle = await self.registry.get(provider_name)
        kwargs, timeout = self._request_kwargs(model, messages, tools, params)
        est = self._estimate(handle, messages, tools, params)
        attempts = 0
        queue_ms = 0
        while True:
            attempts += 1
            permit = await handle.limiter.acquire(est, priority)
            queue_ms += permit.queued_ms
            t0 = time.perf_counter()
            actual = 0
            try:
                raw = await handle.client.chat.completions.create(timeout=timeout, **kwargs)
                resp = self._parse_complete(handle, model, messages, tools, raw, t0)
                resp.attempts, resp.queue_ms = attempts, queue_ms
                actual = resp.usage.total_tokens
                return resp
            except EvalpError:
                raise
            except Exception as e:
                failure = classify(e)
                delay = self._plan_retry(handle, model, failure, attempts, e, mid_stream=False)
            finally:
                permit.release(actual)
            await asyncio.sleep(delay)

    # -- 内部

    def _request_kwargs(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], float]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages_to_openai(messages)}
        if tools:
            kwargs["tools"] = tools
        extra: dict[str, Any] = {}
        for k, v in params.items():
            if k in _RESERVED_PARAMS or v is None:
                continue
            (kwargs if k in _DIRECT_PARAMS else extra)[k] = v
        if extra:
            kwargs["extra_body"] = extra
        timeout = float(params.get("timeout") or self.request_timeout)
        return kwargs, timeout

    def _estimate(
        self,
        handle: ProviderHandle,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        params: dict[str, Any],
    ) -> int:
        """TPM 预扣值：prompt 估算 + max_tokens。未配置 TPM 时不计算。"""
        if handle.limiter.limits.tpm_limit is None:
            return 0
        max_out = (
            params.get("max_tokens") or params.get("max_completion_tokens") or DEFAULT_COMPLETION_ESTIMATE
        )
        return count_messages_tokens(messages, tools) + int(max_out)

    def _plan_retry(
        self,
        handle: ProviderHandle,
        model: str,
        failure: _Failure,
        attempts: int,
        exc: BaseException,
        *,
        mid_stream: bool,
    ) -> float:
        """决定是否重试：返回等待秒数；不重试则抛 ProviderError。"""
        if failure.retriable and not mid_stream and attempts <= self.retry.max_retries:
            delay = self.retry.delay(attempts - 1, failure.retry_after)
            log.warning(
                "llm.retry",
                provider=handle.name,
                model=model,
                status=failure.status,
                attempt=attempts,
                delay=round(delay, 3),
                error=failure.message[:200],
            )
            return delay
        raise self._provider_error(handle, model, failure, attempts, exc, mid_stream=mid_stream) from exc

    @staticmethod
    def _provider_error(
        handle: ProviderHandle,
        model: str,
        failure: _Failure,
        attempts: int,
        exc: BaseException,
        *,
        mid_stream: bool,
    ) -> ProviderError:
        msg = failure.message
        if handle.api_key:
            msg = msg.replace(handle.api_key, "***")
        msg = redact(msg)
        where = "（已开始输出，不再重试）" if mid_stream else ""
        status_txt = f" HTTP {failure.status}" if failure.status else ""
        return ProviderError(
            f"模型服务 {handle.name} 调用失败{status_txt}{where}: {msg}",
            {
                "provider": handle.name,
                "model": model,
                "status": failure.status,
                "retriable": failure.retriable,
                "attempts": attempts,
                "error_type": failure.error_type,
            },
        )

    def _finalize_usage(
        self,
        usage: Usage | None,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        completion_estimate: int,
    ) -> Usage:
        if usage is not None:
            return usage
        return Usage(
            prompt_tokens=count_messages_tokens(messages, tools),
            completion_tokens=completion_estimate,
            estimated=True,
        )

    def _parse_complete(
        self,
        handle: ProviderHandle,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        raw: Any,
        t0: float,
    ) -> LLMResponse:
        choices = getattr(raw, "choices", None)
        if not choices:
            raise ValueError("响应中没有 choices")
        choice = choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        est_completion = count_tokens(msg.content or "")
        for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
            fn = tc.function
            calls.append(
                ToolCall(id=tc.id or f"call_{i}", name=fn.name, arguments=parse_arguments(fn.arguments or ""))
            )
            est_completion += count_tokens(fn.name) + count_tokens(fn.arguments or "")
        usage = None
        if getattr(raw, "usage", None) is not None:
            usage = Usage(
                prompt_tokens=raw.usage.prompt_tokens or 0, completion_tokens=raw.usage.completion_tokens or 0
            )
        usage = self._finalize_usage(usage, messages, tools, est_completion)
        latency = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(
            content=msg.content or None,
            tool_calls=calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            latency_ms=latency,
            ttft_ms=latency,
            cost=compute_cost(handle.model_info(model), usage.prompt_tokens, usage.completion_tokens),
            model=getattr(raw, "model", None),
        )

    async def _stream(
        self,
        stream: LLMStream,
        provider_name: str,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        params: dict[str, Any],
        priority: int,
    ) -> AsyncIterator[LLMChunk]:
        handle = await self.registry.get(provider_name)
        kwargs, timeout = self._request_kwargs(model, messages, tools, params)
        est = self._estimate(handle, messages, tools, params)
        attempts = 0
        queue_ms = 0
        yielded = False
        while True:
            attempts += 1
            permit: Permit = await handle.limiter.acquire(est, priority)
            queue_ms += permit.queued_ms
            acc = _Accumulator()
            raw_stream: Any = None
            use_usage_opt = handle.supports_stream_usage
            extra: dict[str, Any] = {"stream_options": {"include_usage": True}} if use_usage_opt else {}
            t0 = time.perf_counter()
            ttft: int | None = None
            actual = 0
            delay = 0.0
            try:
                raw_stream = await handle.client.chat.completions.create(
                    timeout=timeout, stream=True, **extra, **kwargs
                )
                async for raw_chunk in raw_stream:
                    for out in acc.feed(raw_chunk):
                        if ttft is None:
                            ttft = int((time.perf_counter() - t0) * 1000)
                        yielded = True
                        yield out
                latency = int((time.perf_counter() - t0) * 1000)
                usage = self._finalize_usage(acc.usage, messages, tools, acc.estimated_completion_tokens())
                actual = usage.total_tokens
                stream._response = LLMResponse(
                    content=acc.content,
                    tool_calls=acc.tool_calls(),
                    finish_reason=acc.finish_reason,
                    usage=usage,
                    latency_ms=latency,
                    ttft_ms=ttft if ttft is not None else latency,
                    cost=compute_cost(handle.model_info(model), usage.prompt_tokens, usage.completion_tokens),
                    model=acc.model,
                    attempts=attempts,
                    queue_ms=queue_ms,
                )
                return
            except EvalpError:
                raise
            except Exception as e:
                failure = classify(e)
                if yielded:
                    actual = count_messages_tokens(messages, tools) + acc.estimated_completion_tokens()
                if (
                    use_usage_opt
                    and not yielded
                    and failure.status in (400, 422)
                    and any(w in failure.message.lower() for w in ("stream_options", "include_usage"))
                ):
                    # 服务端不支持 include_usage：降级后立即重发，不计入重试次数
                    handle.supports_stream_usage = False
                    attempts -= 1
                    log.info("llm.stream_usage_unsupported", provider=handle.name)
                    continue
                delay = self._plan_retry(handle, model, failure, attempts, e, mid_stream=yielded)
            finally:
                if raw_stream is not None:
                    try:
                        await raw_stream.close()
                    except Exception:  # 关闭失败不影响主流程
                        pass
                permit.release(actual)
            await asyncio.sleep(delay)
