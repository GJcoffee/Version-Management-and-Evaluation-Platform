"""``@agent`` 装饰器与一轮执行逻辑。

handler 签名：``async def handle(ctx: TurnContext) -> str | Message``（同步函数也可以）。
返回 str 或 Message 时自动生成 ``message.assistant`` 与 ``turn.end``；抛异常时生成 ``error`` 与
``turn.end(status=error)``；被取消时生成 ``turn.end(status=cancelled)``。
"""

from __future__ import annotations

import asyncio
import inspect
import time
import traceback
from collections.abc import Callable
from typing import Any, overload

from .context import EmitFn, TurnContext, _now
from .messages import Message

PROTOCOL = "evalp-agent/1"
Handler = Callable[[TurnContext], Any]


def _to_reply(value: Any) -> Message:
    """handler 的返回值 -> assistant 消息。"""
    if isinstance(value, Message):
        msg = value
    elif isinstance(value, str):
        msg = Message.assistant(value)
    elif isinstance(value, dict) and "role" in value:
        msg = Message.from_dict(value)
    elif isinstance(value, dict) and isinstance(value.get("content"), str):
        msg = Message.assistant(value["content"])
    elif value is None:
        raise TypeError("handler 没有返回内容（需要返回 str 或 Message）")
    else:
        raise TypeError(f"handler 必须返回 str 或 Message，实际返回了 {type(value).__name__}")
    if msg.role != "assistant":
        raise TypeError(f"handler 返回的消息 role 必须是 assistant，实际是 {msg.role}")
    return msg


class Agent:
    """被 ``@agent`` 装饰后的对象：仍可当普通函数调用，并带有 ``serve_http`` / ``run_stdio``。"""

    def __init__(self, handler: Handler, name: str | None = None) -> None:
        self.handler = handler
        self.name = name or getattr(handler, "__name__", "agent")
        self.__doc__ = handler.__doc__

    def __call__(self, ctx: TurnContext) -> Any:
        return self.handler(ctx)

    # ------------------------------------------------------------ 执行一轮

    async def execute(self, request: dict[str, Any], emit: EmitFn) -> str:
        """执行一轮，通过 ``emit(event_dict)`` 输出事件；返回最终状态（completed | error | cancelled）。"""
        start = time.perf_counter()
        status = "completed"
        ctx: TurnContext | None = None
        try:
            ctx = TurnContext(request, emit)
            ctx.emit("turn.start", input=ctx.input.to_dict(), history_len=len(ctx.history))
            if inspect.iscoroutinefunction(self.handler):
                result = await self.handler(ctx)
            else:  # 同步 handler 放进线程，避免阻塞事件循环（emit 因此必须线程安全）
                result = await asyncio.to_thread(self.handler, ctx)
                if inspect.isawaitable(result):
                    result = await result
            reply = _to_reply(result)
            ctx.emit("message.assistant", message=reply.to_dict())
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001 - 任何异常都要变成协议内的 error 事件
            status = "error"
            event = {
                "type": "error",
                "ts": _now(),
                "payload": {
                    "code": "AGENT_ERROR",
                    "message": f"{type(e).__name__}: {e}",
                    "retriable": False,
                    "traceback": traceback.format_exc()[-2000:],
                },
            }
            emit(event)
        finally:
            payload: dict[str, Any] = {
                "status": status,
                "latency_ms": int((time.perf_counter() - start) * 1000),
            }
            if ctx is not None:
                if ctx.usage["prompt_tokens"] or ctx.usage["completion_tokens"]:
                    payload["usage"] = ctx.usage
                if ctx.cost:
                    payload["cost"] = ctx.cost
            emit({"type": "turn.end", "ts": _now(), "payload": payload})
        return status

    # ------------------------------------------------------------ 运行方式

    def serve_http(self, port: int = 8001, host: str = "127.0.0.1") -> None:
        """启动 HTTP 服务（阻塞）：``/evalp/run``、``/evalp/health``、``/evalp/cancel``。"""
        from .server import serve_http

        serve_http(self, port=port, host=host)

    def start_http(self, port: int = 0, host: str = "127.0.0.1"):
        """后台线程启动 HTTP 服务并返回句柄（``.port`` / ``.url`` / ``.close()``）；port=0 为随机端口。"""
        from .server import start_http

        return start_http(self, port=port, host=host)

    def run_stdio(self, stdin: Any = None, stdout: Any = None) -> int:
        """子进程模式：从 stdin 读取一个请求，向 stdout 输出 NDJSON 事件；返回进程退出码。"""
        from .stdio import run_stdio

        return run_stdio(self, stdin=stdin, stdout=stdout)


@overload
def agent(func: Handler) -> Agent: ...
@overload
def agent(func: None = None, *, name: str | None = None) -> Callable[[Handler], Agent]: ...
def agent(func: Handler | None = None, *, name: str | None = None) -> Agent | Callable[[Handler], Agent]:
    """把函数声明为 EvalP 代码型 agent 的入口：``@agent`` 或 ``@agent(name="my-agent")``。"""
    if func is not None:
        return Agent(func, name)
    return lambda f: Agent(f, name)
