"""轮间中间件配置与候选产物契约。"""

from __future__ import annotations

import re

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


class MiddlewareSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "json_artifact"
    version: str = "1.0.0"
    source_turn_id: str | None = None
    # 新选择时校验候选查询与提交之间的上一轮变化；失败重试显式固定来源时可省略。
    expected_previous_turn_id: str | None = None
    config: JsonArtifactConfig = Field(default_factory=JsonArtifactConfig)


class ArtifactCandidate(BaseModel):
    id: str
    url: str
    location: str


class ArtifactCandidates(BaseModel):
    source_turn_id: str
    candidates: list[ArtifactCandidate]
