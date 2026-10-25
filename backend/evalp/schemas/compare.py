"""对比与重放的请求 / 响应 DTO（契约 contract-eval 第 3 节）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class CompareGroupOut(BaseModel):
    id: str
    kind: Literal["session"] = "session"
    mode: Literal["live", "history", "replay"]
    left: CompareSideOut
    right: CompareSideOut
    title: str | None = None
    created_at: str


class ComparePairCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | Message
    variables: dict[str, Any] | None = None


class ComparePairAccepted(BaseModel):
    pair_index: int
    left_turn_id: str
    right_turn_id: str


class ReplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version_id: str
    turns: list[int] | None = Field(None, description="只重放这些原轮次的 idx；缺省为全部")
    mode: Literal["anchored", "free"] = "anchored"


class ReplayAccepted(BaseModel):
    session_id: str
    compare_id: str
