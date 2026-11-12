"""轮间中间件配置与候选产物契约。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JsonArtifactConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url_pointer: str | None = None
    selected_candidate_id: str | None = None
    content_pointer: str | None = None

    @field_validator("url_pointer", "content_pointer")
    @classmethod
    def check_pointer(cls, value: str | None) -> str | None:
        if value is not None and (
            (value != "" and not value.startswith("/")) or re.search(r"~(?![01])", value)
        ):
            raise ValueError("字段路径须为 JSON Pointer，例如 /story/scenes")
        return value


OUTPUT_TYPES = ("confirm", "confirm_artifact", "examine", "finish")


class OutputStageRule(BaseModel):
    """单个阶段（按本轮 idx+1）的输出契约。"""

    model_config = ConfigDict(extra="forbid")

    output_type: Literal["confirm", "confirm_artifact", "examine", "finish"]
    artifact_type: int | None = None
    artifact_types: list[int] | None = None
    approval_code: str | None = None
    examine_status: list[int] = Field(default_factory=lambda: [2, 3])
    terminal: bool = False


class OutputContractConfig(BaseModel):
    """轮次结束后的输出 Envelope 契约，阶段号从 1 开始（Turn.idx + 1）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None
    required_keys: list[str] = Field(default_factory=lambda: [
        "summary", "outputType", "agentInfo", "confirmInfo", "artifactInfo", "agentInvocationChain",
    ])
    examine_key: str = "examineInfo"
    stages: dict[str, OutputStageRule] = Field(default_factory=dict)

    @field_validator("stages")
    @classmethod
    def check_stages(cls, value: dict[str, OutputStageRule]) -> dict[str, OutputStageRule]:
        for key in value:
            if not key.isdigit() or int(key) < 1:
                raise ValueError("阶段号须为从 1 开始的正整数，例如 \"1\"")
        return value


MiddlewareConfig = JsonArtifactConfig | OutputContractConfig


class MiddlewareSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "json_artifact"
    version: str = "1.0.0"
    source_turn_id: str | None = None
    # 新选择时校验候选查询与提交之间的上一轮变化；失败重试显式固定来源时可省略。
    expected_previous_turn_id: str | None = None
    config: MiddlewareConfig = Field(default_factory=JsonArtifactConfig)


class ArtifactCandidate(BaseModel):
    id: str
    url: str
    location: str


class ArtifactCandidates(BaseModel):
    source_turn_id: str
    candidates: list[ArtifactCandidate]
