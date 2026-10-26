"""对话消息模型（详设 6.1）。由总控定义的跨模块契约，修改需记录 DEVIATIONS。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ToolCall(BaseModel):
    """模型发起的一次工具调用。arguments 无法解析为 JSON 时保留原字符串。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] | str


class Message(BaseModel):
    """统一消息格式，与 OpenAI Chat Completions 消息一一对应。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    def text(self) -> str:
        """返回纯文本内容（list 形式时拼接其中的 text 片段）。"""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return "".join(str(p.get("text", "")) for p in self.content if isinstance(p, dict))


class TurnOutput(BaseModel):
    """一轮的输出：final 为最终回复，messages 为本轮新增的全部 assistant/tool 消息。

    轮次被取消 / 出错 / 超时时没有 final：若当时有一次 LLM 调用已经流出了部分文本（``llm.delta`` 不落库，
    也没等到 ``llm.response``），这部分文本保存在 ``partial_text``（过长时截断）。它不属于对话历史。
    """

    final: Message | None = None
    messages: list[Message] = []
    partial_text: str | None = None
