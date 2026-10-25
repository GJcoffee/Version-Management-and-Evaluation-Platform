"""Fake OpenAI Server：脚本化的 OpenAI 兼容模型服务，所有端到端 / 性能测试都用它，**禁止**调用真实模型。

提供 ``/v1/chat/completions``（流式 / 非流式、tools / tool_calls、usage、stream_options）与
``/v1/models``。两个 pytest fixture（已在 tests/conftest.py 通过 ``pytest_plugins`` 注册）：

- ``fake_openai``：进程内运行。自动在 ``ctx`` 中注册名为 ``fake`` 的 Provider（模型 ``fake-model``，
  价格 2 元 / 6 元每百万 token，api_key 为 ``sk-fake-key``），并给 ``LLMRegistry`` 注入内存传输层，
  AsyncOpenAI 不经过网络直接访问本应用（传输层支持真正的流式，能观察到首 token 延迟、中途断开）。
- ``fake_openai_server``：在随机端口用 uvicorn 线程启动，值是 base_url 字符串（``http://127.0.0.1:PORT/v1``），
  同时带 ``.fake``（FakeOpenAI）与 ``.root`` 属性；供子进程、HTTP agent 与端到端测试使用。

用法示例::

    async def test_tool_flow(fake_openai, ctx):
        # 先发起工具调用，看到工具结果后再作答
        fake_openai.add_rule(match="天气", reply=[Call("get_weather", {"city": "北京"})])
        fake_openai.add_rule(match={"has_tool": True}, reply="北京晴，25 度")
        ...  # 通过 ctx.svc(LLMRegistry).llm 或 REST API 发起对话，Provider 名 "fake"，模型 "fake-model"
        assert fake_openai.chat_requests[-1].has_tool_message
        assert fake_openai.requests[0].headers["authorization"] == "Bearer sk-fake-key"

    # match 的写法
    fake.add_rule(match="订单", reply="…")                          # 最后一条 user 消息包含子串
    fake.add_rule(match=re.compile(r"订单\\s*\\d+"), reply="…")        # 正则
    fake.add_rule(match={"user": "你好", "model": "fake-model", "has_tool": False}, reply="…")
    fake.add_rule(match=lambda req: len(req.messages) > 5, reply="…")   # 任意条件
    fake.add_rule(reply="兜底", times=1)            # 不写 match = 全部匹配；times 限制命中次数

    # reply 的写法
    "文本"                                                          # 文本回复
    [Call("search", {"q": "x"}), ("other", {"a": 1})]               # tool_calls（Call / (name, args) / dict）
    {"content": "…", "usage": {"prompt_tokens": 7, "completion_tokens": 3}, "finish_reason": "length"}
    Reply(content="…", usage=False, ttft=0.2)       # usage=False：不返回 usage；ttft：首 token 延迟
    FakeError(429, retry_after=1)                   # 返回错误
    lambda req: f"你说的是 {req.last_user}"          # 回调（可以是 async），可返回上面任意一种

    # 顺序队列、默认回显
    # 规则都没命中时按顺序消费；队列也空了就回显 "echo: {最后一条 user 消息}"
    fake.enqueue("第一次", [Call("t", {})], "第三次")

    # 故障注入（times 为影响的请求次数，None 表示一直生效）
    fake.fail(429, times=2, retry_after=0.05)   # 前两次请求返回 429 + Retry-After
    fake.fail(500, times=1)
    fake.slow(0.3, times=1)                     # 响应前延迟 0.3 秒
    fake.slow_first_token(0.3)                  # 流式首 token 延迟
    fake.break_stream(after_chunks=2)           # 流式输出 2 个增量后断开连接
    fake.supports_stream_options = False        # 对带 stream_options 的请求返回 400（测试自动降级）
    fake.stream_usage = False                   # 流式不返回 usage（测试 tiktoken 估算）
    fake.latency = 0.2                          # 全局默认延迟（性能测试：模拟 200ms 模型）

    # 断言
    fake.requests          # 收到的全部请求（含 /v1/models），RecordedRequest 列表
    fake.chat_requests     # 只含 chat/completions
    fake.max_inflight      # 观察到的最大并发
    fake.reset()           # 清空规则、队列、故障与记录
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import itertools
import json
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import uvicorn
from evalp.runtime.llm.tokens import count_messages_tokens, count_tokens
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

DEFAULT_PROVIDER = "fake"
DEFAULT_MODEL = "fake-model"
DEFAULT_API_KEY = "sk-fake-key"


# --------------------------------------------------------------------------- 请求 / 回复数据结构


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


@dataclass
class RecordedRequest:
    """一次收到的 HTTP 请求。"""

    method: str
    path: str
    headers: dict[str, str]  # 键为小写
    body: dict[str, Any]
    ts: float = field(default_factory=time.time)

    @property
    def model(self) -> str:
        return str(self.body.get("model", ""))

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self.body.get("messages") or [])

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self.body.get("tools") or [])

    @property
    def stream(self) -> bool:
        return bool(self.body.get("stream"))

    @property
    def last_user(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "user":
                return _content_text(m.get("content"))
        return ""

    @property
    def system(self) -> str:
        return "\n".join(_content_text(m.get("content")) for m in self.messages if m.get("role") == "system")

    @property
    def has_tool_message(self) -> bool:
        return any(m.get("role") == "tool" for m in self.messages)

    @property
    def last_tool_content(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "tool":
                return _content_text(m.get("content"))
        return ""


@dataclass
class Call:
    """回复里的一次工具调用。arguments 可以是 dict 或原始字符串（用来模拟非法 JSON）。"""

    name: str
    arguments: dict[str, Any] | str = field(default_factory=dict)
    id: str | None = None


@dataclass
class Reply:
    """一次回复。字段留空表示用默认行为。"""

    content: str | None = None
    tool_calls: list[Any] = field(default_factory=list)  # Call | (name, args) | dict
    finish_reason: str | None = None  # 默认：有 tool_calls -> tool_calls，否则 stop
    usage: dict[str, int] | bool | None = None  # None：自动估算；False：不返回 usage
    model: str | None = None
    delay: float | None = None  # 响应前延迟（秒）
    ttft: float | None = None  # 流式首 token 延迟
    chunk_delay: float | None = None  # 流式相邻增量之间的延迟
    chunk_size: int | None = None  # 流式每个文本增量的字符数


@dataclass
class FakeError:
    """作为 reply 使用时返回一个 HTTP 错误。"""

    status: int = 500
    message: str = "fake server error"
    retry_after: float | None = None
    code: str | None = None


def _coerce_reply(value: Any, request: RecordedRequest) -> Reply | FakeError:
    if isinstance(value, Reply | FakeError):
        return value
    if value is None:
        return Reply(content=f"echo: {request.last_user}")
    if isinstance(value, str):
        return Reply(content=value)
    if isinstance(value, list | tuple):
        return Reply(tool_calls=list(value))
    if isinstance(value, dict):
        return Reply(**value)
    raise TypeError(f"无法识别的 reply: {value!r}")


def _norm_calls(raw: list[Any], counter: Iterator[int]) -> list[Call]:
    calls: list[Call] = []
    for item in raw:
        if isinstance(item, Call):
            c = Call(item.name, item.arguments, item.id)
        elif isinstance(item, tuple):
            c = Call(item[0], item[1] if len(item) > 1 else {})
        elif isinstance(item, dict):
            c = Call(item["name"], item.get("arguments", {}), item.get("id"))
        else:
            raise TypeError(f"无法识别的 tool_call: {item!r}")
        c.id = c.id or f"call_fake_{next(counter)}"
        calls.append(c)
    return calls


def _args_str(args: dict[str, Any] | str) -> str:
    return args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)


# --------------------------------------------------------------------------- 匹配与规则


def _text_cond(cond: Any, text: str) -> bool:
    if isinstance(cond, re.Pattern):
        return cond.search(text) is not None
    if callable(cond):
        return bool(cond(text))
    return str(cond) in text


def make_matcher(match: Any) -> Callable[[RecordedRequest], bool]:
    """把 match 参数变成判断函数，见模块 docstring。"""
    if match is None:
        return lambda req: True
    if isinstance(match, str | re.Pattern):
        return lambda req: _text_cond(match, req.last_user)
    if callable(match):
        return lambda req: bool(match(req))
    if isinstance(match, dict):
        unknown = set(match) - {"user", "model", "has_tool", "tool", "system", "stream", "has_tools"}
        if unknown:
            raise ValueError(f"match 不支持的键: {sorted(unknown)}")

        def _m(req: RecordedRequest) -> bool:
            if "user" in match and not _text_cond(match["user"], req.last_user):
                return False
            if "model" in match and not _text_cond(match["model"], req.model):
                return False
            if "system" in match and not _text_cond(match["system"], req.system):
                return False
            if "tool" in match and not _text_cond(match["tool"], req.last_tool_content):
                return False
            if "has_tool" in match and match["has_tool"] != req.has_tool_message:
                return False
            if "has_tools" in match and match["has_tools"] != bool(req.tools):
                return False
            return not ("stream" in match and match["stream"] != req.stream)

        return _m
    raise TypeError(f"无法识别的 match: {match!r}")


@dataclass
class Rule:
    matcher: Callable[[RecordedRequest], bool]
    reply: Any
    times: int | None = None  # None：不限次数
    hits: int = 0

    @property
    def exhausted(self) -> bool:
        return self.times is not None and self.hits >= self.times


@dataclass
class _Fault:
    kind: str  # status | slow | ttft | break
    times: int | None
    status: int = 500
    message: str = "injected fault"
    retry_after: float | None = None
    seconds: float = 0.0
    after_chunks: int = 0

    @property
    def active(self) -> bool:
        return self.times is None or self.times > 0

    def consume(self) -> None:
        if self.times is not None:
            self.times -= 1


class _Disconnect(Exception):
    """模拟流式中途断开（让服务端抛异常，连接被异常关闭）。"""


# --------------------------------------------------------------------------- FakeOpenAI


class FakeOpenAI:
    """脚本化的 OpenAI 兼容服务（FastAPI 应用在 ``.app``）。用法见模块 docstring。"""

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        latency: float = 0.0,
        ttft: float = 0.0,
        chunk_delay: float = 0.0,
        chunk_size: int = 4,
        required_api_key: str | None = None,
    ) -> None:
        self.models = models or [DEFAULT_MODEL]
        self.latency = latency
        self.ttft = ttft
        self.chunk_delay = chunk_delay
        self.chunk_size = chunk_size
        self.required_api_key = required_api_key
        self.supports_stream_options = True
        self.stream_usage = True
        self.requests: list[RecordedRequest] = []
        self.rules: list[Rule] = []
        self.queue: deque[Any] = deque()
        self._faults: list[_Fault] = []
        self.inflight = 0
        self.max_inflight = 0
        self._ids = itertools.count(1)
        self._call_ids = itertools.count(1)
        self.app = self._build_app()

    # -- 脚本化

    def add_rule(self, match: Any = None, reply: Any = None, *, times: int | None = None) -> Rule:
        """添加规则（先添加的先匹配）。返回 Rule，可读 ``.hits``。"""
        rule = Rule(make_matcher(match), reply, times)
        self.rules.append(rule)
        return rule

    def enqueue(self, *replies: Any) -> None:
        """追加顺序回复：规则都没命中时依次消费。"""
        self.queue.extend(replies)

    # -- 故障注入

    def fail(
        self,
        status: int = 500,
        *,
        times: int | None = 1,
        retry_after: float | None = None,
        message: str = "injected fault",
    ) -> None:
        """接下来 times 次 chat 请求直接返回 HTTP 错误（429 可带 Retry-After 秒数）。"""
        self._faults.append(_Fault("status", times, status=status, message=message, retry_after=retry_after))

    def slow(self, seconds: float, *, times: int | None = None) -> None:
        """响应前延迟 seconds 秒。"""
        self._faults.append(_Fault("slow", times, seconds=seconds))

    def slow_first_token(self, seconds: float, *, times: int | None = None) -> None:
        """流式首 token 延迟 seconds 秒。"""
        self._faults.append(_Fault("ttft", times, seconds=seconds))

    def break_stream(self, after_chunks: int = 1, *, times: int | None = 1) -> None:
        """流式输出 after_chunks 个增量后断开连接（0：只发角色块就断开，此时尚无 token）。"""
        self._faults.append(_Fault("break", times, after_chunks=after_chunks))

    def clear_faults(self) -> None:
        self._faults.clear()

    def reset(self) -> None:
        """清空规则、队列、故障与请求记录（保留开关属性）。"""
        self.rules.clear()
        self.queue.clear()
        self._faults.clear()
        self.requests.clear()
        self.max_inflight = 0

    # -- 记录

    @property
    def chat_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.path.endswith("/chat/completions")]

    # -- 内部：解析回复

    async def _resolve(self, req: RecordedRequest) -> Reply | FakeError:
        for rule in self.rules:
            if not rule.exhausted and rule.matcher(req):
                rule.hits += 1
                return await self._materialize(rule.reply, req)
        if self.queue:
            return await self._materialize(self.queue.popleft(), req)
        return Reply(content=f"echo: {req.last_user}")

    async def _materialize(self, reply: Any, req: RecordedRequest) -> Reply | FakeError:
        if callable(reply) and not isinstance(reply, Reply | FakeError):
            reply = reply(req)
            if inspect.isawaitable(reply):
                reply = await reply
        return _coerce_reply(reply, req)

    def _take(self, kind: str) -> _Fault | None:
        for f in self._faults:
            if f.kind == kind and f.active:
                f.consume()
                return f
        return None

    # -- 内部：HTTP

    def _error(self, status: int, message: str, retry_after: float | None = None, code: str | None = None):
        headers = {"Retry-After": f"{retry_after:g}"} if retry_after is not None else None
        body = {"error": {"message": message, "type": "fake_error", "code": code}}
        return JSONResponse(body, status_code=status, headers=headers)

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="fake-openai")

        @app.get("/v1/models")
        async def list_models(request: Request):
            self.requests.append(self._record(request, {}))
            data = [{"id": m, "object": "model", "created": 0, "owned_by": "fake"} for m in self.models]
            return {"object": "list", "data": data}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            try:
                body = await request.json()
            except ValueError:
                body = {}
            req = self._record(request, body if isinstance(body, dict) else {})
            self.requests.append(req)
            return await self._handle_chat(req)

        return app

    @staticmethod
    def _record(request: Request, body: dict[str, Any]) -> RecordedRequest:
        headers = {k.lower(): v for k, v in request.headers.items()}
        return RecordedRequest(request.method, request.url.path, headers, body)

    async def _handle_chat(self, req: RecordedRequest):
        if self.required_api_key is not None:
            if req.headers.get("authorization") != f"Bearer {self.required_api_key}":
                return self._error(401, "Incorrect API key provided", code="invalid_api_key")
        if "stream_options" in req.body and not self.supports_stream_options:
            return self._error(400, "Unrecognized request argument supplied: stream_options")
        fault = self._take("status")
        if fault is not None:
            return self._error(fault.status, fault.message, fault.retry_after)

        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        streaming_started = False
        try:
            reply = await self._resolve(req)
            if isinstance(reply, FakeError):
                return self._error(reply.status, reply.message, reply.retry_after, reply.code)
            delay = (reply.delay if reply.delay is not None else self.latency) + sum(
                f.seconds for f in [self._take("slow")] if f
            )
            if delay:
                await asyncio.sleep(delay)
            calls = _norm_calls(reply.tool_calls, self._call_ids)
            usage = self._usage(req, reply, calls)
            model = reply.model or req.model or DEFAULT_MODEL
            finish = reply.finish_reason or ("tool_calls" if calls else "stop")
            cid = f"chatcmpl-fake-{next(self._ids)}"
            if not req.stream:
                message: dict[str, Any] = {"role": "assistant", "content": reply.content}
                if calls:
                    message["tool_calls"] = [self._tool_call_json(c) for c in calls]
                payload: dict[str, Any] = {
                    "id": cid,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                }
                if usage is not None:
                    payload["usage"] = usage
                return JSONResponse(payload)

            include_usage = bool((req.body.get("stream_options") or {}).get("include_usage"))
            ttft_extra = self._take("ttft")
            ttft = (reply.ttft if reply.ttft is not None else self.ttft) + (
                ttft_extra.seconds if ttft_extra else 0
            )
            brk = self._take("break")
            gen = self._sse(
                cid, model, reply, calls, finish, usage if (include_usage and self.stream_usage) else None,
                ttft, brk.after_chunks if brk else None,
            )  # fmt: skip
            streaming_started = True
            return StreamingResponse(self._guard(gen), media_type="text/event-stream")
        finally:
            if not streaming_started:
                self.inflight -= 1

    async def _guard(self, gen: AsyncIterator[str]) -> AsyncIterator[str]:
        """流式响应结束（正常或异常）时扣减并发计数。"""
        try:
            async for piece in gen:
                yield piece
        finally:
            self.inflight -= 1

    @staticmethod
    def _tool_call_json(c: Call) -> dict[str, Any]:
        return {
            "id": c.id,
            "type": "function",
            "function": {"name": c.name, "arguments": _args_str(c.arguments)},
        }

    def _usage(self, req: RecordedRequest, reply: Reply, calls: list[Call]) -> dict[str, int] | None:
        if reply.usage is False:
            return None
        if isinstance(reply.usage, dict):
            u = dict(reply.usage)
            u.setdefault("total_tokens", u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
            return u
        prompt = count_messages_tokens(req.messages, req.tools or None)
        completion = count_tokens(reply.content or "") + sum(
            count_tokens(c.name) + count_tokens(_args_str(c.arguments)) for c in calls
        )
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}

    async def _sse(
        self,
        cid: str,
        model: str,
        reply: Reply,
        calls: list[Call],
        finish: str,
        usage: dict[str, int] | None,
        ttft: float,
        break_after: int | None,
    ) -> AsyncIterator[str]:
        size = reply.chunk_size or self.chunk_size
        gap = reply.chunk_delay if reply.chunk_delay is not None else self.chunk_delay
        created = int(time.time())

        def frame(delta: dict[str, Any], finish_reason: str | None = None, usage_: dict | None = None) -> str:
            obj: dict[str, Any] = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": []
                if usage_ is not None
                else [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            if usage_ is not None:
                obj["usage"] = usage_
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        # 内容增量序列：文本片段 + 工具调用片段
        deltas: list[dict[str, Any]] = []
        text = reply.content or ""
        for i in range(0, len(text), size):
            deltas.append({"content": text[i : i + size]})
        for idx, c in enumerate(calls):
            deltas.append(
                {
                    "tool_calls": [
                        {
                            "index": idx,
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": ""},
                        }
                    ]
                }
            )
            args = _args_str(c.arguments)
            step = max(size * 2, 1)
            for i in range(0, len(args), step):
                deltas.append({"tool_calls": [{"index": idx, "function": {"arguments": args[i : i + step]}}]})

        if ttft:
            await asyncio.sleep(ttft)
        yield frame({"role": "assistant", "content": ""})
        sent = 0
        for d in deltas:
            if break_after is not None and sent >= break_after:
                raise _Disconnect("injected stream break")
            if gap and sent:
                await asyncio.sleep(gap)
            yield frame(d)
            sent += 1
        if break_after is not None and sent >= break_after:
            raise _Disconnect("injected stream break")
        yield frame({}, finish)
        if usage is not None:
            yield frame({}, usage_=usage)
        yield "data: [DONE]\n\n"


# --------------------------------------------------------------------------- 内存流式传输层


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """支持真正流式（以及超时）的 ASGI 传输层。

    httpx 自带的 ``ASGITransport`` 会等应用把整个响应写完才返回，看不到首 token 延迟，
    也无法模拟中途断开；这里在后台任务里运行应用，响应体边产生边交给客户端。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        timeout = request.extensions.get("timeout") or {}
        read_timeout: float | None = timeout.get("read")
        url = request.url
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for k, v in request.headers.raw],  # ASGI 要求小写
            "scheme": url.scheme,
            "path": unquote(url.path),
            "raw_path": url.raw_path.split(b"?")[0],
            "query_string": url.query,
            "server": (url.host, url.port or (443 if url.scheme == "https" else 80)),
            "client": ("127.0.0.1", 123),
            "root_path": "",
        }
        loop = asyncio.get_running_loop()
        started: asyncio.Future[tuple[int, list[tuple[bytes, bytes]]]] = loop.create_future()
        chunks: asyncio.Queue[Any] = asyncio.Queue()
        disconnected = asyncio.Event()
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                if not started.done():
                    started.set_result((message["status"], list(message.get("headers", []))))
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    chunks.put_nowait(message["body"])
                if not message.get("more_body", False):
                    chunks.put_nowait(None)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except asyncio.CancelledError:
                raise
            except BaseException as e:  # noqa: BLE001 - 应用异常需要转交给客户端
                if not started.done():
                    started.set_exception(e)
                else:
                    chunks.put_nowait(e)
            else:
                if not started.done():
                    started.set_exception(RuntimeError("ASGI 应用没有返回响应"))
                chunks.put_nowait(None)

        task = asyncio.create_task(run())

        async def shutdown() -> None:
            disconnected.set()
            if not task.done():
                task.cancel()
            with contextlib.suppress(BaseException):
                await task

        try:
            status, raw_headers = await asyncio.wait_for(started, read_timeout)
        except TimeoutError:
            await shutdown()
            raise httpx.ReadTimeout("fake transport: 等待响应头超时", request=request) from None
        except BaseException:
            await shutdown()
            raise

        class _Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                while True:
                    try:
                        item = await asyncio.wait_for(chunks.get(), read_timeout)
                    except TimeoutError:
                        raise httpx.ReadTimeout("fake transport: 读取响应体超时", request=request) from None
                    if item is None:
                        return
                    if isinstance(item, BaseException):
                        raise httpx.RemoteProtocolError(
                            f"peer closed connection without sending complete message body ({item})",
                            request=request,
                        )
                    yield item

            async def aclose(self) -> None:
                await shutdown()

        return httpx.Response(status, headers=raw_headers, stream=_Stream(), request=request)


# --------------------------------------------------------------------------- fixtures


class FakeServerURL(str):
    """``fake_openai_server`` 的值：本身就是 base_url（含 ``/v1``），另带 ``.fake`` 与 ``.root``。"""

    fake: FakeOpenAI
    root: str

    def __new__(cls, root: str, fake: FakeOpenAI) -> FakeServerURL:
        obj = super().__new__(cls, f"{root}/v1")
        obj.fake = fake
        obj.root = root
        return obj


@pytest.fixture
async def fake_openai(ctx: Any) -> AsyncIterator[FakeOpenAI]:
    """进程内 Fake OpenAI：已在 ctx 里注册 Provider ``fake``（模型 ``fake-model``）并注入内存传输层。"""
    from evalp.runtime.llm.registry import LLMRegistry
    from evalp.schemas.provider import ModelInfo, ProviderCreate
    from evalp.services.provider import ProviderService

    fake = FakeOpenAI()
    registry = ctx.svc(LLMRegistry)
    registry.set_transport(StreamingASGITransport(fake.app))
    await ctx.svc(ProviderService).create(
        ProviderCreate(
            name=DEFAULT_PROVIDER,
            base_url="http://fake.test/v1",
            api_key=DEFAULT_API_KEY,
            models=[ModelInfo(name=DEFAULT_MODEL, context_window=8192, price_in=2.0, price_out=6.0)],
        )
    )
    yield fake


@pytest.fixture
def fake_openai_server() -> Iterator[FakeServerURL]:
    """在随机端口上以 uvicorn 线程启动 Fake OpenAI，值为 base_url（``http://127.0.0.1:PORT/v1``）。"""
    fake = FakeOpenAI()
    config = uvicorn.Config(fake.app, host="127.0.0.1", port=0, log_level="critical", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="fake-openai", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Fake OpenAI 服务启动失败")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield FakeServerURL(f"http://127.0.0.1:{port}", fake)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
