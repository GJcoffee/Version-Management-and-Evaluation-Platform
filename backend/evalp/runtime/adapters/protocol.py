"""``evalp-agent/1`` 协议的公共部分（HttpAdapter 与 SubprocessAdapter 共用，详设 6.5 / 6.6）。

请求体（POST /evalp/run 的 JSON，或子进程的 stdin）、L2 事件行的解析与校验、L1 响应的解析。
所有协议违例都抛 :class:`ProtocolError`，由 Adapter 转成带可读说明的 ``error`` 事件。
"""

from __future__ import annotations

import time
from typing import Any

from evalp.runtime.adapters.base import RunContext
from evalp.runtime.events import ALL_TYPES, PAYLOAD_MODELS, AgentEvent, ev, jsonable
from evalp.runtime.messages import Message, ToolCall
from evalp.spec.models import CodeSpec
from evalp.utils.time import now_iso

PROTOCOL = "evalp-agent/1"
PROTOCOL_ERROR = "AGENT_PROTOCOL_ERROR"
NDJSON_TYPES = ("application/x-ndjson", "application/ndjson", "application/jsonl", "application/x-jsonlines")
_MESSAGE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


class ProtocolError(Exception):
    """agent 的输出不符合协议；``details`` 会放进 error 事件，帮助定位问题。"""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


def build_request(
    ctx: RunContext, spec: CodeSpec, history: list[Message], user_input: Message
) -> dict[str, Any]:
    """构造发送给 agent 的请求体（详设 6.5）。"""
    remaining = ctx.deadline - time.monotonic()
    deadline_ms = (
        int(max(remaining, 0) * 1000)
        if remaining != float("inf")
        else int(spec.runtime.turn_timeout_s * 1000)
    )
    return {
        "protocol": PROTOCOL,
        "session_id": ctx.session_id,
        "turn_id": ctx.turn_id,
        "history": [jsonable(m) for m in history],
        "input": jsonable(user_input),
        "params": spec.params,
        "variables": ctx.variables,
        "tool_mocks": ctx.tool_mocks,
        "seed": ctx.seed,
        "deadline_ms": deadline_ms,
    }


def coerce_message(obj: Any, *, where: str = "message") -> Message:
    """把 agent 返回的消息对象转成 Message（忽略未知字段，兼容 OpenAI 风格的额外字段）。"""
    if not isinstance(obj, dict):
        raise ProtocolError(f"{where} 必须是对象，实际是 {type(obj).__name__}")
    data = {k: obj[k] for k in _MESSAGE_KEYS if k in obj}
    if "role" not in data:
        raise ProtocolError(f"{where} 缺少 role 字段", got_keys=sorted(obj)[:10])
    calls = data.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list):
            raise ProtocolError(f"{where}.tool_calls 必须是数组")
        try:
            data["tool_calls"] = [ToolCall.model_validate(c) for c in calls]
        except ValueError as e:
            raise ProtocolError(f"{where}.tool_calls 不合法: {_short(e)}") from None
    try:
        return Message.model_validate(data)
    except ValueError as e:
        raise ProtocolError(f"{where} 不合法: {_short(e)}") from None


def event_from_wire(obj: Any, *, user_input: Message | None = None) -> AgentEvent:
    """把 L2 的一行 JSON（已解析）转成 AgentEvent；seq / turn_id 由平台补齐，ts 缺省取当前时间。"""
    if not isinstance(obj, dict):
        raise ProtocolError(f"事件必须是 JSON 对象，实际是 {type(obj).__name__}")
    etype = obj.get("type")
    if not isinstance(etype, str):
        raise ProtocolError("事件缺少字符串类型的 type 字段", got_keys=sorted(obj)[:10])
    if etype not in ALL_TYPES:
        raise ProtocolError(f"未知的事件类型: {etype!r}", allowed=sorted(ALL_TYPES))
    payload = obj.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError(f"事件 {etype} 的 payload 必须是对象")
    payload = dict(jsonable(payload))
    if etype == "turn.start" and user_input is not None:
        payload.setdefault("input", jsonable(user_input))
    for key in ("step_id", "parent_step_id", "ts"):
        if obj.get(key) is not None and not isinstance(obj[key], str):
            raise ProtocolError(f"事件 {etype} 的 {key} 必须是字符串")
    if etype == "llm.delta":
        payload.setdefault("step_id", obj.get("step_id"))
    try:
        validated = PAYLOAD_MODELS[etype].model_validate(payload)
    except ValueError as e:
        raise ProtocolError(f"事件 {etype} 的 payload 不合法: {_short(e)}") from None
    data = validated.model_dump(mode="json", exclude_none=(etype == "llm.delta"))
    return AgentEvent(
        type=etype,  # type: ignore[arg-type]
        step_id=obj.get("step_id"),
        parent_step_id=obj.get("parent_step_id"),
        ts=obj.get("ts") or now_iso(),
        payload=data,
    )


def l1_events(obj: Any, ctx: RunContext) -> list[AgentEvent]:
    """L1 响应 ``{"output": Message, "usage"?, "cost"?, "messages"?}`` -> message.assistant + turn.end。

    ``messages``（可选）是本轮新增的全部 assistant / tool 消息，用于下一轮拼接历史；缺省时历史只含 final。
    """
    if not isinstance(obj, dict) or "output" not in obj:
        raise ProtocolError('L1 响应必须是 {"output": Message, ...} 对象', got=type(obj).__name__)
    final = coerce_message(obj["output"], where="output")
    if final.role != "assistant":
        raise ProtocolError(f"output.role 应为 assistant，实际是 {final.role}")
    extra = obj.get("messages")
    if extra is not None:
        if not isinstance(extra, list):
            raise ProtocolError("messages 必须是数组")
        ctx.new_messages[:] = [coerce_message(m, where=f"messages[{i}]") for i, m in enumerate(extra)]
    usage = obj.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise ProtocolError("usage 必须是对象")
    cost = obj.get("cost")
    if cost is not None and not isinstance(cost, int | float):
        raise ProtocolError("cost 必须是数字")
    return [
        ev("message.assistant", message=final),
        ev("turn.end", status="completed", usage=usage, cost=cost),
    ]


def protocol_error_event(err: ProtocolError, **extra: Any) -> AgentEvent:
    """协议违例 -> error 事件（不可重试）。"""
    reserved = {"code", "message", "retriable"}
    detail = {k: v for k, v in {**err.details, **extra}.items() if k not in reserved}
    return ev(
        "error",
        code=PROTOCOL_ERROR,
        message=f"agent 输出不符合 {PROTOCOL} 协议: {err.message}",
        retriable=False,
        **detail,
    )


def _short(exc: BaseException, limit: int = 300) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + "…"
