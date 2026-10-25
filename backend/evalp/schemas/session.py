"""会话 / 轮次 / 事件的请求与响应 DTO（详设 3.3、7.1、11.3）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalp.runtime.events import AgentEvent
from evalp.runtime.messages import Message, TurnOutput

SessionMode = Literal["chat", "eval", "replay", "judge"]
ReplayMode = Literal["anchored", "free"]
TurnStatus = Literal["running", "ok", "error", "cancelled", "timeout"]


class TurnError(BaseModel):
    """轮次错误：序列化为 JSON 字符串存入 ``turns.error``。"""

    code: str
    message: str
    retriable: bool = False


class SessionOut(BaseModel):
    id: str
    agent_id: str
    version_id: str
    mode: SessionMode
    title: str | None = None
    source_session_id: str | None = None
    replay_mode: ReplayMode | None = None
    status: Literal["idle", "running", "error"]
    last_seq: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    agent_name: str | None = None
    version_seq: int | None = None


class TurnOut(BaseModel):
    id: str
    session_id: str
    idx: int
    pair_index: int | None = None
    status: TurnStatus
    input: Message
    output: TurnOutput | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost: float = 0.0
    latency_ms: int | None = None
    ttft_ms: int | None = None
    error: TurnError | None = None
    anchored_from_turn_id: str | None = None
    started_at: str
    finished_at: str | None = None


class SessionDetail(BaseModel):
    """``GET /sessions/{id}`` 的响应：会话 + 轮次 + 事件（后两者按 include 返回）。"""

    session: SessionOut
    turns: list[TurnOut] = Field(default_factory=list)
    events: list[AgentEvent] | None = None


# ---------------------------------------------------------------- 请求


class SessionCreate(BaseModel):
    """创建会话：``version_id``（版本 id，也接受 ``agent:tag`` 形式）或 ``agent`` + ``tag`` 二选一。"""

    model_config = ConfigDict(extra="forbid")

    version_id: str | None = None
    agent: str | None = Field(None, description="agent 的 id 或名称，与 tag 配合使用")
    tag: str | None = Field(None, description="标签、v12 或 latest；缺省为 latest")
    title: str | None = None
    variables: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> SessionCreate:
        if bool(self.version_id) == bool(self.agent):
            raise ValueError("必须且只能提供 version_id，或 agent（可加 tag）")
        if self.tag and not self.agent:
            raise ValueError("tag 需要和 agent 一起使用")
        return self


SESSION_TITLE_MAX = 120


class SessionUpdate(BaseModel):
    """``PATCH /sessions/{id}``：目前只能改标题（去掉首尾空白后 1 到 120 个字符，不能清空）。"""

    model_config = ConfigDict(extra="forbid")

    title: str

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > SESSION_TITLE_MAX:
            raise ValueError(f"标题最长 {SESSION_TITLE_MAX} 个字符")
        return v


class TurnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | Message
    variables: dict[str, Any] | None = None
    tool_mocks: dict[str, Any] | None = None
    pair_index: int | None = None
    wait: bool = False


class TurnAccepted(BaseModel):
    """``POST /sessions/{id}/turns`` 的响应。"""

    turn_id: str
    stream_url: str
    turn: TurnOut
