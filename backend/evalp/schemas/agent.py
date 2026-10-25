"""Agent / 草稿 / 版本 / 工作区同步的请求 / 响应 DTO。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAME_RE = re.compile(r"^[\w][\w.-]{0,63}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
ViaType = Literal["ui", "cli", "mcp", "api"]


def check_agent_name(v: str) -> str:
    """agent / 草稿名：字母（含中文）、数字、下划线、点、连字符；不含冒号和空格，最长 64。"""
    if not NAME_RE.match(v):
        raise ValueError("名称只允许字母、数字、下划线、点和连字符（不含空格、冒号），最长 64 个字符")
    return v


def check_tag(v: str) -> str:
    """标签名：不能形如 v12（会与版本号混淆），不能是 latest。"""
    if not TAG_RE.match(v):
        raise ValueError("标签只允许字母、数字、下划线、点和连字符，最长 64 个字符")
    if re.fullmatch(r"v\d+", v) or v == "latest":
        raise ValueError("标签不能是 latest，也不能形如 v12（与版本号冲突）")
    return v


# ---------------------------------------------------------------- Agent


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["config", "code"]
    description: str | None = None
    spec: dict[str, Any] | None = Field(None, description="初始草稿 Spec；省略时生成该 kind 的最小模板")

    _v_name = field_validator("name")(check_agent_name)


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else check_agent_name(v)


class VersionBrief(BaseModel):
    id: str
    seq: int
    spec_hash: str
    created_at: str


class AgentOut(BaseModel):
    id: str
    name: str
    kind: str
    description: str | None
    archived: bool
    created_at: str
    updated_at: str
    latest_version: VersionBrief | None = None
    tags: list[str] = Field(default_factory=list, description="该 agent 全部版本标签的名称（按字典序）")


# ---------------------------------------------------------------- 草稿


class DraftOut(BaseModel):
    id: str
    agent_id: str
    name: str
    spec: dict[str, Any]
    base_version_id: str | None
    revision: int
    created_at: str
    updated_at: str


class AgentCreated(BaseModel):
    agent: AgentOut
    draft: DraftOut


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    spec: dict[str, Any] | None = None
    from_version_id: str | None = Field(None, description="用该版本的 Spec 作为初始内容")

    _v_name = field_validator("name")(check_agent_name)


class DraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    expected_revision: int = Field(ge=1)


class DraftValidateOut(BaseModel):
    ok: bool
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    resolved_preview: dict[str, Any] | None


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    notes: str | None = None
    via: ViaType = "api"


# ---------------------------------------------------------------- 版本


class VersionSummary(BaseModel):
    id: str
    agent_id: str
    seq: int
    spec_hash: str
    source: dict[str, Any]
    parent_version_id: str | None
    from_draft_id: str | None
    notes: str | None
    created_via: str
    created_at: str
    tags: list[str] = Field(default_factory=list)


class VersionAssetRefOut(BaseModel):
    spec_path: str
    kind: str
    name: str
    rev: int
    asset_revision_id: str


class VersionOut(VersionSummary):
    spec: dict[str, Any]
    assets: list[VersionAssetRefOut] = Field(default_factory=list)


class PublishOut(BaseModel):
    version: VersionOut
    existed: bool


class TagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str

    _v_tag = field_validator("tag")(check_tag)


class TagOut(BaseModel):
    agent_id: str
    tag: str
    version_id: str
    updated_at: str


class ToDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_name: str = "main"

    _v_name = field_validator("draft_name")(check_agent_name)


class DraftWrapper(BaseModel):
    draft: DraftOut


# ---------------------------------------------------------------- 工作区同步


class WorkspaceFile(BaseModel):
    """客户端的一个本地文件。content 为空表示客户端认为服务端已有该 hash 的内容。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    asset: str = Field(pattern=r"^[a-z]+/[^/\s]+$", description="kind/name，如 prompt/cs-system")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="文件内容的 sha256（十六进制）")
    content: str | None = None
    encoding: Literal["utf-8", "base64"] = "utf-8"
    content_type: str | None = None
    column_map: dict[str, Any] | None = None


class PushIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    agent_kind: Literal["config", "code"] | None = None
    draft_name: str = "main"
    spec: dict[str, Any]
    files: list[WorkspaceFile] = Field(default_factory=list)
    expected_revision: int | None = Field(None, ge=1)
    force: bool = False

    _v_name = field_validator("agent_name")(check_agent_name)
    _v_draft = field_validator("draft_name")(check_agent_name)


class PushOut(BaseModel):
    agent_id: str
    agent_created: bool
    draft: DraftOut
    uploaded: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


class PullIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    draft_name: str = "main"
    asset_map: dict[str, str] = Field(
        default_factory=dict, description="资产 -> 本地路径，如 {'prompt/cs-system': 'prompts/system.md'}"
    )


class PullOut(BaseModel):
    agent_id: str
    draft: DraftOut
    local_spec: dict[str, Any]
    assets: list[dict[str, Any]]
    missing: list[str]
