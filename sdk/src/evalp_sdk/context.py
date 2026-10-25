"""TurnContext：一轮对话的上下文，以及 ``span()`` / ``llm_call()`` / ``tool_call()`` / ``log()`` 事件辅助。

这些辅助对象同时支持 ``with`` 与 ``async with``，所以同步 handler 也能使用。事件按 ``evalp-event/1`` 生成，
嵌套关系（``parent_step_id``）通过 contextvars 自动维护，``asyncio.gather`` 并发的子任务也不会串。
"""

from __future__ import annotations

import contextvars
import itertools
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .messages import Message, to_message

EmitFn = Callable[[dict[str, Any]], None]

_current_step: contextvars.ContextVar[str | None] = contextvars.ContextVar("evalp_current_step", default=None)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _subset(match: Any, actual: Any) -> bool:
    """参数子集匹配：match 里的每个键值都要在 actual 里出现（字典递归，其他值要求相等）。"""
    if isinstance(match, dict):
        return isinstance(actual, dict) and all(
            k in actual and _subset(v, actual[k]) for k, v in match.items()
        )
    return match == actual


class TurnContext:
    """一轮对话的上下文，作为 handler 的唯一参数。

    属性：``input``（本轮用户消息）、``history``（之前各轮的消息）、``params``（Spec 的 params）、
    ``variables``、``tool_mocks``（评测用例的工具 mock，没有则为 None）、``seed``、``session_id``、
    ``turn_id``、``deadline_ms``（平台给本轮的剩余时间预算）。
    """

    def __init__(self, request: dict[str, Any], emit: EmitFn) -> None:
        self._emit = emit
        self._steps = itertools.count(1)
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self._cost = 0.0
        self.session_id: str | None = request.get("session_id")
        self.turn_id: str | None = request.get("turn_id")
        self.input: Message = to_message(request.get("input") or {"role": "user", "content": ""})
        self.history: list[Message] = [to_message(m) for m in request.get("history") or []]
        self.params: dict[str, Any] = request.get("params") or {}
        self.variables: dict[str, Any] = request.get("variables") or {}
        self.tool_mocks: dict[str, Any] | None = request.get("tool_mocks")
        self.seed: int | None = request.get("seed")
        self.deadline_ms: int | None = request.get("deadline_ms")

    # ------------------------------------------------------------ 底层

    def new_step_id(self) -> str:
        return f"s{next(self._steps)}"

    def emit(
        self,
        type: str,  # noqa: A002 - 与协议字段同名
        step_id: str | None = None,
        parent_step_id: str | None = None,
        **payload: Any,
    ) -> None:
        """发送一个原始事件（一般用 span / llm_call / tool_call / log 即可）。"""
        event: dict[str, Any] = {"type": type, "ts": _now(), "payload": payload}
        if step_id is not None:
            event["step_id"] = step_id
        if parent_step_id is not None:
            event["parent_step_id"] = parent_step_id
        self._emit(event)

    # ------------------------------------------------------------ 辅助

    def log(self, message: str, level: str = "info", **extra: Any) -> None:
        """记录一条日志（出现在 trace 里）。"""
        self.emit("log", level=level, message=str(message), **extra)

    def span(self, name: str, **attrs: Any) -> Span:
        """记录一个自定义步骤（如检索）：``with ctx.span("retrieve", query=q) as s: s.set(n=3)``。"""
        return Span(self, name, attrs)

    def llm_call(
        self,
        *,
        model: str,
        messages: list[Any] | None = None,
        provider: str | None = None,
        params: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMCall:
        """记录一次模型调用（自动生成 llm.request / llm.response）。"""
        return LLMCall(self, model, messages or [], provider, params or {}, tools)

    def tool_call(
        self, name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None
    ) -> ToolCall:
        """记录一次工具调用（自动生成 tool.call / tool.result）。"""
        return ToolCall(self, name, args or {}, call_id)

    def mock_output(self, name: str, args: dict[str, Any] | None = None) -> tuple[bool, Any]:
        """查工具 mock：返回 ``(是否命中, 输出)``。按顺序做参数子集匹配，都不匹配时用 default。"""
        for item in (self.tool_mocks or {}).get(name) or []:
            if not isinstance(item, dict):
                continue
            if "match" in item and item["match"] is not None:
                if _subset(item["match"], args or {}):
                    return True, item.get("output")
            elif "default" in item and item["default"] is not None:
                return True, item["default"]
        return False, None

    def report_usage(self, *, prompt_tokens: int = 0, completion_tokens: int = 0, cost: float = 0.0) -> None:
        """手工上报用量（没有使用 llm_call 时）；会写入最终的 turn.end。"""
        self._usage["prompt_tokens"] += int(prompt_tokens)
        self._usage["completion_tokens"] += int(completion_tokens)
        self._cost += float(cost)

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    @property
    def cost(self) -> float:
        return self._cost


class _Step:
    """span / llm_call / tool_call 的公共部分：同步与异步上下文管理器。"""

    def __init__(self, ctx: TurnContext) -> None:
        self.ctx = ctx
        self.step_id = ctx.new_step_id()
        self.parent_step_id: str | None = None
        self._token: contextvars.Token[str | None] | None = None
        self._start = 0.0

    def _open(self) -> None:
        self.parent_step_id = _current_step.get()
        self._token = _current_step.set(self.step_id)
        self._start = time.perf_counter()

    def _close(self) -> None:
        if self._token is not None:
            try:
                _current_step.reset(self._token)
            except ValueError:  # 在不同上下文中退出：忽略
                pass
            self._token = None

    def _begin(self) -> None:
        raise NotImplementedError

    def _end(self, exc: BaseException | None) -> None:
        raise NotImplementedError

    def __enter__(self):
        self._open()
        self._begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self._end(exc)
        finally:
            self._close()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)


class Span(_Step):
    """自定义步骤：进入时发 span.start，退出时发 span.end（带耗时与 ``set()`` 的属性）。"""

    def __init__(self, ctx: TurnContext, name: str, attrs: dict[str, Any]) -> None:
        super().__init__(ctx)
        self.name = name
        self.attrs = dict(attrs)

    def set(self, **attrs: Any) -> None:
        """补充属性（出现在 span.end 里）。"""
        self.attrs.update(attrs)

    def _begin(self) -> None:
        self.ctx.emit("span.start", self.step_id, self.parent_step_id, name=self.name, attrs=dict(self.attrs))

    def _end(self, exc: BaseException | None) -> None:
        if exc is not None:
            self.attrs["error"] = f"{type(exc).__name__}: {exc}"
        self.ctx.emit(
            "span.end",
            self.step_id,
            self.parent_step_id,
            name=self.name,
            attrs=dict(self.attrs),
            latency_ms=_ms(self._start),
        )


def _usage_from(obj: Any) -> dict[str, int] | None:
    """从 dict / 对象里取出用量，兼容 prompt_tokens / input_tokens 两种命名。"""
    if obj is None:
        return None

    def pick(*names: str) -> int:
        for n in names:
            v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
            if isinstance(v, int | float) and not isinstance(v, bool):
                return int(v)
        return 0

    p, c = pick("prompt_tokens", "input_tokens"), pick("completion_tokens", "output_tokens")
    return {"prompt_tokens": p, "completion_tokens": c} if (p or c) else None


class LLMCall(_Step):
    """一次模型调用：进入时发 llm.request，退出时发 llm.response；``record()`` 登记结果。"""

    def __init__(
        self,
        ctx: TurnContext,
        model: str,
        messages: list[Any],
        provider: str | None,
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None,
    ) -> None:
        super().__init__(ctx)
        self.model, self.provider, self.params, self.tools = model, provider, params, tools
        self.messages = [m.to_dict() if isinstance(m, Message) else m for m in messages]
        self._content: str | None = None
        self._tool_calls: list[dict[str, Any]] = []
        self._finish_reason: str | None = None
        self._usage: dict[str, int] | None = None
        self._cost: float | None = None
        self._recorded = False
        self._first_token: float | None = None

    def _begin(self) -> None:
        self.ctx.emit(
            "llm.request",
            self.step_id,
            self.parent_step_id,
            provider=self.provider,
            model=self.model,
            params=self.params,
            messages=self.messages,
            tools=self.tools,
        )

    def delta(self, text: str | None = None, tool_call_delta: dict[str, Any] | None = None) -> None:
        """流式输出的增量（可选）；第一次调用的时间用于计算 ttft。"""
        if self._first_token is None:
            self._first_token = time.perf_counter()
        self.ctx.emit(
            "llm.delta", self.step_id, self.parent_step_id, text=text, tool_call_delta=tool_call_delta
        )

    def record(
        self,
        resp: Any = None,
        *,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: Any = None,
        finish_reason: str | None = None,
        cost: float | None = None,
    ) -> None:
        """登记模型返回。``resp`` 可以是字符串、dict（content / text / tool_calls / usage /
        finish_reason / cost），或带 ``text`` / ``content`` / ``usage`` 属性的对象；关键字参数优先。"""
        self._recorded = True
        if resp is not None:
            get = (lambda k: resp.get(k)) if isinstance(resp, dict) else (lambda k: getattr(resp, k, None))
            if isinstance(resp, str):
                content = content if content is not None else resp
            else:
                if content is None:
                    for k in ("content", "text"):
                        if isinstance(get(k), str):
                            content = get(k)
                            break
                tool_calls = tool_calls if tool_calls is not None else get("tool_calls")
                usage = usage if usage is not None else get("usage")
                finish_reason = finish_reason or get("finish_reason")
                cost = cost if cost is not None else get("cost")
        self._content = content
        self._tool_calls = [
            c if isinstance(c, dict) else getattr(c, "__dict__", {}) for c in (tool_calls or [])
        ]
        self._finish_reason = finish_reason
        self._usage = _usage_from(usage)
        self._cost = float(cost) if isinstance(cost, int | float) else None

    def _end(self, exc: BaseException | None) -> None:
        extra: dict[str, Any] = {}
        finish = self._finish_reason
        if exc is not None:
            extra["error"] = f"{type(exc).__name__}: {exc}"
            finish = finish or "error"
        elif not self._recorded:
            self.ctx.log(f"llm_call({self.model}) 结束时没有调用 record() 登记结果", level="warn")
        if self._usage:
            self.ctx.report_usage(
                prompt_tokens=self._usage["prompt_tokens"],
                completion_tokens=self._usage["completion_tokens"],
                cost=self._cost or 0.0,
            )
        elif self._cost:
            self.ctx.report_usage(cost=self._cost)
        ttft = int((self._first_token - self._start) * 1000) if self._first_token else None
        self.ctx.emit(
            "llm.response",
            self.step_id,
            self.parent_step_id,
            content=self._content,
            tool_calls=self._tool_calls,
            finish_reason=finish,
            usage=self._usage,
            latency_ms=_ms(self._start),
            ttft_ms=ttft,
            cost=self._cost,
            **extra,
        )


class ToolCall(_Step):
    """一次工具调用：进入时发 tool.call，退出时发 tool.result；用 ``result()`` / ``error()`` 登记结果。

    评测用例带了工具 mock 时，``has_mock`` 为 True、``mock_output`` 是命中的输出；
    ``result(t.mock_output)`` 会自动标记 ``mocked=true``。
    """

    def __init__(self, ctx: TurnContext, name: str, args: dict[str, Any], call_id: str | None) -> None:
        super().__init__(ctx)
        self.name, self.args = name, args
        self.call_id = call_id or f"call_{self.step_id}"
        self.has_mock, self.mock_output = ctx.mock_output(name, args)
        self._output: Any = None
        self._is_error = False
        self._mocked = False
        self._done = False

    def _begin(self) -> None:
        self.ctx.emit(
            "tool.call",
            self.step_id,
            self.parent_step_id,
            call_id=self.call_id,
            name=self.name,
            arguments=self.args,
        )

    def result(self, output: Any, *, is_error: bool = False) -> None:
        """登记工具输出。"""
        self._output, self._is_error, self._done = output, is_error, True
        self._mocked = self.has_mock and output is self.mock_output

    def error(self, message: str) -> None:
        """登记工具失败。"""
        self.result(message, is_error=True)

    def _end(self, exc: BaseException | None) -> None:
        if exc is not None and not self._done:
            self._output, self._is_error = f"{type(exc).__name__}: {exc}", True
        self.ctx.emit(
            "tool.result",
            self.step_id,
            self.parent_step_id,
            call_id=self.call_id,
            name=self.name,
            output=self._output,
            is_error=self._is_error,
            latency_ms=_ms(self._start),
            mocked=self._mocked,
        )
