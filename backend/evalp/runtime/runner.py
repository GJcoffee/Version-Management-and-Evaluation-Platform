"""Runner：Adapter 之外的通用执行逻辑（详设 6.3）。

职责：

- 校验事件顺序：第一个必须是 ``turn.start``、最后必须是 ``turn.end``，缺失时自动补齐；
  Adapter 自己产出的 ``turn.end`` 只作为"提示"（status / output / usage / cost）被吸收，最终的 ``turn.end``
  始终由 Runner 汇总后产出；``turn.end`` 之后的事件被丢弃；
- 用 ``asyncio.timeout`` 实现超时（``ctx.deadline``）、监听 ``ctx.cancel`` 实现取消，
  超时 / 取消时关闭 Adapter 的事件流（Adapter 在 ``finally`` 里清理连接、子进程等）；
- 捕获 Adapter 抛出的异常，转成 ``error`` 事件；
- 从 ``llm.response`` 汇总 usage / cost / llm_calls / tool_calls，计算 ttft（第一个 ``llm.delta`` 或
  ``llm.response`` 的时间，相对本轮开始）与 latency；
- 组装 :class:`TurnOutput`：``final`` 来自 ``message.assistant``（或 Adapter 的 ``turn.end.output``），
  ``messages`` 为 ``ctx.new_messages``（Adapter 没填时为 ``[final]``）。

最终状态 ``status`` 取值同 ``turns.status``：``ok | error | cancelled | timeout``；``turn.end`` 事件里的
状态用事件协议的 ``completed | error | cancelled | timeout``。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from evalp.errors import EvalpError
from evalp.logging import get_logger
from evalp.runtime.adapters.base import AgentAdapter, RunContext
from evalp.runtime.events import AgentEvent, ev, jsonable
from evalp.runtime.messages import Message, TurnOutput

log = get_logger(__name__)

EmitFn = Callable[[AgentEvent], Awaitable[None]]
BeforeEndFn = Callable[["TurnResult"], Awaitable[None]]

_END_TO_STATUS = {"completed": "ok", "error": "error", "cancelled": "cancelled", "timeout": "timeout"}
_STATUS_TO_END = {v: k for k, v in _END_TO_STATUS.items()}


@dataclass
class TurnResult:
    """一轮的执行结果。"""

    status: str  # ok | error | cancelled | timeout
    output: TurnOutput | None
    usage: dict[str, Any]
    cost: float
    latency_ms: int
    ttft_ms: int | None
    error: dict[str, Any] | None = None  # {code, message, retriable}

    @property
    def end_status(self) -> str:
        """``turn.end`` 事件里的状态（completed | error | cancelled | timeout）。"""
        return _STATUS_TO_END[self.status]


def error_from_exception(exc: BaseException) -> dict[str, Any]:
    """异常 -> ``{code, message, retriable}``。"""
    if isinstance(exc, EvalpError):
        return {
            "code": exc.code,
            "message": exc.message,
            "retriable": bool(exc.details.get("retriable", False)),
        }
    log.warning("runner.adapter_exception", error=f"{type(exc).__name__}: {exc}", exc_info=exc)
    return {"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}", "retriable": False}


@dataclass
class _State:
    t0: float
    history_len: int
    started: bool = False
    agent_end: dict[str, Any] | None = None
    final: Message | None = None
    error: dict[str, Any] | None = None
    error_emitted: bool = False
    outcome: str = "done"  # done | timeout | cancelled | failed
    llm_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False
    cost: float = 0.0
    ttft_ms: int | None = None
    dropped: int = 0
    notes: list[str] = field(default_factory=list)

    def mark_first_output(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = int((time.monotonic() - self.t0) * 1000)


class Runner:
    """执行一轮：驱动 Adapter 的事件流，并保证事件协议的完整性。"""

    async def run(
        self,
        adapter: AgentAdapter,
        ctx: RunContext,
        history: list[Message],
        user_input: Message,
        emit: EmitFn,
        *,
        before_end: BeforeEndFn | None = None,
    ) -> TurnResult:
        """执行一轮并通过 ``emit`` 输出全部事件（含最后的 ``turn.end``）。

        ``before_end`` 在产出 ``turn.end`` 之前被调用（会话服务借此先把轮次结果写库，
        再让订阅者看到 ``turn.end``）。
        本方法不会因为 agent 的失败而抛异常，失败体现在 :class:`TurnResult` 中。
        """
        state = _State(t0=time.monotonic(), history_len=len(history))
        budget = ctx.deadline - state.t0
        consumer = asyncio.create_task(self._consume(adapter, ctx, history, user_input, emit, state))
        waiter = asyncio.create_task(ctx.cancel.wait())
        try:
            await asyncio.wait({consumer, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not consumer.done():  # 被取消
                state.outcome = "cancelled"
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        except asyncio.CancelledError:  # Runner 所在任务被外部取消：先收尾，再继续向上抛
            state.outcome = "cancelled"
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await asyncio.shield(self._finish(ctx, user_input, emit, state, budget, before_end))
            raise
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        return await self._finish(ctx, user_input, emit, state, budget, before_end)

    # ------------------------------------------------------------ 消费事件流

    async def _consume(
        self,
        adapter: AgentAdapter,
        ctx: RunContext,
        history: list[Message],
        user_input: Message,
        emit: EmitFn,
        state: _State,
    ) -> None:
        remaining = ctx.deadline - time.monotonic()
        limit = None if remaining == float("inf") else max(remaining, 0.0)
        timer = asyncio.timeout(limit)
        agen = adapter.run_turn(ctx, history, user_input)
        try:
            async with timer:
                async for raw in agen:
                    if not isinstance(raw, AgentEvent):
                        raise TypeError(f"Adapter 产出了非 AgentEvent 对象: {type(raw).__name__}")
                    if await self._handle(raw, ctx, user_input, emit, state):
                        break
        except TimeoutError as e:
            if timer.expired():
                state.outcome = "timeout"
            else:
                state.outcome, state.error = "failed", error_from_exception(e)
        except Exception as e:  # noqa: BLE001 - Adapter 的任何异常都转成 error 事件
            state.outcome, state.error = "failed", error_from_exception(e)
        finally:
            # 取消（CancelledError）与超时都会走到这里：关闭事件流，让 Adapter 的 finally 清理资源
            with contextlib.suppress(Exception):
                await agen.aclose()  # type: ignore[attr-defined]

    async def _ensure_started(
        self, ctx: RunContext, user_input: Message, emit: EmitFn, state: _State
    ) -> None:
        if not state.started:
            state.started = True
            await emit(ev("turn.start", input=user_input, history_len=state.history_len, turn_id=ctx.turn_id))

    async def _handle(
        self, raw: AgentEvent, ctx: RunContext, user_input: Message, emit: EmitFn, state: _State
    ) -> bool:
        """处理 Adapter 的一个事件，返回 True 表示事件流应当结束（遇到 turn.end）。"""
        event = raw.with_turn(ctx.turn_id)
        p = event.payload
        if event.type == "turn.start":
            if state.started:
                state.dropped += 1
                return False
            state.started = True
            payload = {**p, "input": jsonable(user_input), "history_len": state.history_len}
            await emit(event.model_copy(update={"payload": payload}))
            return False
        await self._ensure_started(ctx, user_input, emit, state)
        if event.type == "turn.end":
            state.agent_end = p
            return True
        if event.type == "error":
            if state.error is None:
                state.error = {
                    "code": p.get("code", "AGENT_ERROR"),
                    "message": p.get("message", ""),
                    "retriable": bool(p.get("retriable", False)),
                }
            state.error_emitted = True
        elif event.type == "llm.delta":
            state.mark_first_output()
        elif event.type == "llm.response":
            state.mark_first_output()
            state.llm_calls += 1
            usage = p.get("usage") or {}
            state.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            state.completion_tokens += int(usage.get("completion_tokens") or 0)
            state.estimated = state.estimated or bool(usage.get("estimated"))
            state.cost += float(p.get("cost") or 0.0)
        elif event.type == "tool.call":
            state.tool_calls += 1
        elif event.type == "message.assistant":
            try:
                state.final = Message.model_validate(p.get("message"))
            except ValueError as e:
                state.final = None
                if state.error is None:
                    state.error = {
                        "code": "AGENT_PROTOCOL_ERROR",
                        "message": f"message.assistant 的 message 不合法: {' '.join(str(e).split())[:300]}",
                        "retriable": False,
                    }
        await emit(event)
        return False

    # ------------------------------------------------------------ 收尾

    async def _finish(
        self,
        ctx: RunContext,
        user_input: Message,
        emit: EmitFn,
        state: _State,
        budget: float,
        before_end: BeforeEndFn | None,
    ) -> TurnResult:
        await self._ensure_started(ctx, user_input, emit, state)
        end = state.agent_end or {}

        # Adapter 只在 turn.end 里给了 output 时，补一个 message.assistant
        if state.final is None and state.outcome == "done" and state.error is None:
            out = end.get("output")
            cand = out.get("final") if isinstance(out, dict) and "final" in out else out
            if isinstance(cand, dict):
                try:
                    state.final = Message.model_validate(cand)
                    await emit(ev("message.assistant", message=state.final, turn_id=ctx.turn_id))
                except ValueError:
                    pass

        status = "ok"
        if state.outcome == "timeout":
            status = "timeout"
            state.error = {
                "code": "TIMEOUT",
                "message": f"本轮执行超过 {budget:g}s（turn_timeout_s）被终止",
                "retriable": False,
            }
        elif state.outcome == "cancelled":
            status = "cancelled"
        elif state.error is not None:
            status = "error"
        else:
            reported = _END_TO_STATUS.get(str(end.get("status", "completed")), "ok")
            if reported != "ok":
                status = reported
                if status == "error":
                    state.error = {
                        "code": "AGENT_ERROR",
                        "message": "agent 报告本轮失败（turn.end.status=error）但没有给出 error 事件",
                        "retriable": False,
                    }
            elif state.final is None:
                status = "error"
                state.error = {
                    "code": "AGENT_PROTOCOL_ERROR",
                    "message": "agent 没有返回最终回复（缺少 message.assistant 事件）",
                    "retriable": False,
                }
        if state.error is not None and not state.error_emitted and status in ("error", "timeout"):
            await emit(ev("error", turn_id=ctx.turn_id, **state.error))
            state.error_emitted = True

        output = None
        if status == "ok" and state.final is not None:
            output = TurnOutput(final=state.final, messages=list(ctx.new_messages) or [state.final])

        usage: dict[str, Any] = {
            "prompt_tokens": state.prompt_tokens,
            "completion_tokens": state.completion_tokens,
            "llm_calls": state.llm_calls,
            "tool_calls": state.tool_calls,
        }
        cost = state.cost
        if state.llm_calls == 0:  # 没有 llm.response 事件时采用 agent 自报的用量
            reported_usage = end.get("usage")
            if isinstance(reported_usage, dict):
                for k, v in reported_usage.items():
                    if isinstance(v, int | float) and not isinstance(v, bool):
                        usage[k] = v
                usage["tool_calls"] = max(usage.get("tool_calls", 0), state.tool_calls)
            if isinstance(end.get("cost"), int | float):
                cost = float(end["cost"])
        if state.estimated:
            usage["estimated"] = True
        result = TurnResult(
            status=status,
            output=output,
            usage=usage,
            cost=cost,
            latency_ms=int((time.monotonic() - state.t0) * 1000),
            ttft_ms=state.ttft_ms,
            error=state.error if status in ("error", "timeout") else None,
        )
        if before_end is not None:
            try:
                await before_end(result)
            except Exception as e:  # noqa: BLE001 - 持久化失败不应阻止 turn.end 的发布
                log.exception("runner.before_end_failed", error=f"{type(e).__name__}: {e}")
        extra: dict[str, Any] = {"error": result.error} if result.error else {}
        await emit(
            ev(
                "turn.end",
                turn_id=ctx.turn_id,
                status=result.end_status,
                output=output.model_dump(mode="json", exclude_none=True) if output else None,
                usage=usage,
                cost=cost,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                **extra,
            )
        )
        return result
