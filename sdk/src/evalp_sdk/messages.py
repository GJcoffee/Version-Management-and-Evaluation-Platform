"""消息模型：与平台的 ``Message`` / ``ToolCall``（详设 6.1）字段一一对应的轻量版本。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


@dataclass
class Message:
    """一条对话消息。``content`` 可以是字符串，或（多模态）片段列表；``tool_calls`` 是
    ``[{"id", "name", "arguments"}]``。"""

    role: str
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str | None) -> Message:
        return cls(role="assistant", content=content)

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """从请求里的 dict 构造（忽略未知字段）。"""
        if not isinstance(data, dict) or "role" not in data:
            raise ValueError(f"不是合法的消息对象: {data!r}"[:200])
        return cls(**{k: data[k] for k in _KEYS if k in data})

    def to_dict(self) -> dict[str, Any]:
        """导出为 JSON 友好的 dict（去掉 None 字段）。"""
        return {k: getattr(self, k) for k in _KEYS if getattr(self, k) is not None}

    def text(self) -> str:
        """纯文本内容（多模态片段只取其中的 text）。"""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return "".join(str(p.get("text", "")) for p in self.content if isinstance(p, dict))

    def __str__(self) -> str:
        return self.text()


def to_message(value: Any) -> Message:
    """str / dict / Message -> Message（用于 llm_call 的 messages 等宽松入参）。"""
    if isinstance(value, Message):
        return value
    if isinstance(value, str):
        return Message.user(value)
    if isinstance(value, dict):
        return Message.from_dict(value)
    raise TypeError(f"无法转换为消息: {type(value).__name__}")
