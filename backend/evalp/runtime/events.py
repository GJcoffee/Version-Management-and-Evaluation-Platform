"""事件协议 ``evalp-event/1``（详设 6.2）。

用法::

    from evalp.runtime.events import ev

    ev("turn.start", input=user_msg, history_len=3)
    ev("llm.request", step_id="s1", provider="fake", model="m", params={}, messages=msgs, tools=None)
    ev("tool.call", step_id="s2", parent_step_id="s1", call_id="c1", name="query", arguments={"a": 1})

``ev()`` 会把 pydantic 对象（Message、ToolCall 等）转成 JSON 友好的 dict，并按 type 对应的 payload
模型校验必填字段（payload 模型允许额外字段，便于外部 agent 携带自定义信息）。
``turn_id`` 与 ``seq`` 通常由 Runner / EventSink 补上：``event.with_turn("trn_x").with_seq(5)``。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from evalp.errors import ValidationFailed
from evalp.utils.time import now_iso

EventType = Literal[
    "turn.start",
    "llm.request",
    "llm.delta",
    "llm.response",
    "tool.call",
    "tool.result",
    "span.start",
    "span.end",
    "log",
    "message.assistant",
    "error",
    "turn.end",
]

ALL_TYPES: frozenset[str] = frozenset(get_args(EventType))
# llm.delta 只推送、不落库
PERSISTED_TYPES: frozenset[str] = ALL_TYPES - {"llm.delta"}

TurnStatus = Literal["completed", "error", "timeout", "cancelled"]


# --------------------------------------------------------------------------- payload 模型


class _Payload(BaseModel):
    """payload 基类：允许额外字段。"""

    model_config = ConfigDict(extra="allow")


class TurnStartPayload(_Payload):
    input: dict[str, Any]
    history_len: int = 0


class LlmRequestPayload(_Payload):
    provider: str | None = None
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None


class LlmDeltaPayload(_Payload):
    step_id: str | None = None
    text: str | None = None
    tool_call_delta: dict[str, Any] | None = None


class UsagePayload(_Payload):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False


class LlmResponsePayload(_Payload):
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: UsagePayload | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    cost: float | None = None


class ToolCallPayload(_Payload):
    call_id: str
    name: str
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class ToolResultPayload(_Payload):
    call_id: str
    name: str
    output: Any = None
    is_error: bool = False
    latency_ms: int | None = None
    mocked: bool = False


class SpanStartPayload(_Payload):
    name: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class SpanEndPayload(_Payload):
    name: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None


class LogPayload(_Payload):
    level: str = "info"
    message: str


class MessageAssistantPayload(_Payload):
    message: dict[str, Any]


class ErrorPayload(_Payload):
    code: str
    message: str
    retriable: bool = False


class TurnEndPayload(_Payload):
    status: TurnStatus
    output: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    cost: float | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None


PAYLOAD_MODELS: dict[str, type[_Payload]] = {
    "turn.start": TurnStartPayload,
    "llm.request": LlmRequestPayload,
    "llm.delta": LlmDeltaPayload,
    "llm.response": LlmResponsePayload,
    "tool.call": ToolCallPayload,
    "tool.result": ToolResultPayload,
    "span.start": SpanStartPayload,
    "span.end": SpanEndPayload,
    "log": LogPayload,
    "message.assistant": MessageAssistantPayload,
    "error": ErrorPayload,
    "turn.end": TurnEndPayload,
}


# --------------------------------------------------------------------------- 事件


class AgentEvent(BaseModel):
    """事件外层结构。seq 由平台（EventSink）分配；llm.delta 没有 seq。"""

    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    turn_id: str | None = None
    ts: str = Field(default_factory=now_iso)
    type: EventType
    step_id: str | None = None
    parent_step_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def with_seq(self, seq: int | None) -> AgentEvent:
        """返回带新 seq 的副本。"""
        return self.model_copy(update={"seq": seq})

    def with_turn(self, turn_id: str) -> AgentEvent:
        """返回带 turn_id 的副本。"""
        return self.model_copy(update={"turn_id": turn_id})

    @property
    def persisted(self) -> bool:
        """是否需要落库（llm.delta 不落库）。"""
        return self.type in PERSISTED_TYPES

    def to_wire(self) -> dict[str, Any]:
        """外层结构的 dict（SSE / 入库 / NDJSON 用）。"""
        return self.model_dump(mode="json")


def jsonable(obj: Any) -> Any:
    """递归把 pydantic 对象、元组等转为 JSON 友好的结构（BaseModel 去掉 None 字段）。"""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [jsonable(v) for v in obj]
    return obj


def ev(
    type: EventType,  # noqa: A002 - 与协议字段同名
    *,
    step_id: str | None = None,
    parent_step_id: str | None = None,
    turn_id: str | None = None,
    ts: str | None = None,
    **payload: Any,
) -> AgentEvent:
    """构造事件。payload 按 type 校验；llm.delta 的 payload 里会带上 step_id（6.2 节）。"""
    if type not in PAYLOAD_MODELS:
        raise ValidationFailed(f"未知事件类型: {type}", {"type": type})
    model = PAYLOAD_MODELS[type]
    if type == "llm.delta":
        payload.setdefault("step_id", step_id)
    try:
        validated = model.model_validate(jsonable(payload))
    except ValueError as e:
        raise ValidationFailed(f"事件 {type} 的 payload 不合法: {e}", {"type": type}) from e
    data = validated.model_dump(mode="json", exclude_none=(type == "llm.delta"))
    kwargs: dict[str, Any] = {
        "type": type,
        "step_id": step_id,
        "parent_step_id": parent_step_id,
        "turn_id": turn_id,
        "payload": data,
    }
    if ts is not None:
        kwargs["ts"] = ts
    return AgentEvent(**kwargs)


def step_signature(event: AgentEvent | Mapping[str, Any]) -> str | None:
    """步骤签名（用于对比时对齐，详设 6.2 / 9.2）；不参与对齐的事件返回 None。

    ``llm.response`` -> ``"llm"``，``tool.call`` -> ``"tool:{name}"``，
    ``span.start`` -> ``"span:{name}"``，``message.assistant`` -> ``"final"``。
    """
    if isinstance(event, Mapping):
        etype, payload = event.get("type"), event.get("payload") or {}
    else:
        etype, payload = event.type, event.payload
    if etype == "llm.response":
        return "llm"
    if etype == "tool.call":
        return f"tool:{payload.get('name', '')}"
    if etype == "span.start":
        return f"span:{payload.get('name', '')}"
    if etype == "message.assistant":
        return "final"
    return None


# --------------------------------------------------------------------------- 脱敏（16.1）

REDACTED = "***"

# 规范化（小写，- 换成 _）后的敏感键：完整匹配，或以这些词结尾
_SENSITIVE_EXACT = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "cookie",
        "set_cookie",
        "private_key",
    }
)
# 以 _token / _secret / _api_key / _password 结尾（max_tokens、prompt_tokens 以 tokens 结尾，不会误伤）
_SENSITIVE_SUFFIX = re.compile(r"_(token|secret|api_key|apikey|password|passwd|private_key)$")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")


def is_sensitive_key(key: str) -> bool:
    """键名是否属于需要脱敏的字段（Authorization、*-Token、api_key 等）。"""
    k = key.strip().lower().replace("-", "_")
    return k in _SENSITIVE_EXACT or bool(_SENSITIVE_SUFFIX.search(k))


def _redact_str(s: str) -> str:
    s = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", s)
    return _SK_RE.sub(REDACTED, s)


def redact(obj: Any) -> Any:
    """返回脱敏后的深拷贝：敏感键的值换成 ``***``，字符串里的 Bearer 凭据 / sk- 密钥也会被抹掉。"""
    if isinstance(obj, BaseModel):
        return redact(obj.model_dump(mode="json"))
    if isinstance(obj, Mapping):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_sensitive_key(k) and v is not None:
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list | tuple):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _redact_str(obj)
    return obj
