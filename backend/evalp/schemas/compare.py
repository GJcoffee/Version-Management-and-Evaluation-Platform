"""对比与重放的请求 / 响应 DTO（契约 contract-eval 第 3 节）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evalp.runtime.messages import Message


class CompareSide(BaseModel):
    """创建对比时的一侧：live 用 version_id（可写 agent:v12 / agent:标签），history 用 session_id。"""

    model_config = ConfigDict(extra="forbid")

    version_id: str | None = None
    session_id: str | None = None


class CompareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["session"] = "session"
    mode: Literal["live", "history"]
    left: CompareSide
    right: CompareSide
    title: str | None = None


class CompareSideOut(BaseModel):
    session_id: str
    version_id: str
    version_seq: int | None = None
    agent_name: str | None = None


ReplayStatus = Literal["running", "completed", "failed"]
ReplayModeName = Literal["anchored", "free"]
COMPARE_TITLE_MAX = 120
COMPARE_NOTES_MAX = 2000


class CompareGroupOut(BaseModel):
    id: str
    kind: Literal["session"] = "session"
    mode: Literal["live", "history", "replay"]
    left: CompareSideOut
    right: CompareSideOut
    title: str | None = None
    notes: str | None = None
    created_at: str
    # 以下仅 mode=replay 有值（其余为 null）
    replay_mode: ReplayModeName | None = None
    replay_status: ReplayStatus | None = None
    replay_turns: list[int] | None = None
    replay_error: str | None = None


class CompareUpdate(BaseModel):
    """``PATCH /compare/{id}``：修改标题 / 备注。只改请求里出现的字段；传 null 或空白字符串表示清空。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > COMPARE_TITLE_MAX:
            raise ValueError(f"标题最长 {COMPARE_TITLE_MAX} 个字符")
        return v or None

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > COMPARE_NOTES_MAX:
            raise ValueError(f"备注最长 {COMPARE_NOTES_MAX} 个字符")
        return v or None


class ComparePairCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | Message
    variables: dict[str, Any] | None = None
    side: Literal["both", "left", "right"] = Field(
        "both", description="发给哪一侧；left / right 为单侧消息（分叉），pair_index 为空，不参与轮次对齐"
    )


class ComparePairAccepted(BaseModel):
    pair_index: int | None = Field(None, description="单侧消息为 null")
    left_turn_id: str | None = Field(None, description="没有发给左侧时为 null")
    right_turn_id: str | None = Field(None, description="没有发给右侧时为 null")
    side: Literal["both", "left", "right"] = "both"


class ReplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version_id: str
    turns: list[int] | None = Field(None, description="只重放这些原轮次的 idx；缺省为全部")
    mode: Literal["anchored", "free"] = "anchored"


class ReplayAccepted(BaseModel):
    session_id: str
    compare_id: str
