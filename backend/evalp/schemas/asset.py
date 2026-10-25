"""资产与数据集的请求 / 响应 DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalp.spec.refs import ASSET_KINDS, RefError, validate_asset_name

AssetKind = Literal["prompt", "tool", "knowledge", "dataset", "file"]
DatasetFormat = Literal["jsonl", "csv"]


def _check_name(v: str) -> str:
    try:
        return validate_asset_name(v)
    except RefError as e:
        raise ValueError(str(e)) from None


class _ContentIn(BaseModel):
    """内容二选一：content（文本）或 blob_upload_id（先用 POST /assets/upload 上传）。"""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    blob_upload_id: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    content_type: str | None = None
    message: str | None = None
    column_map: dict[str, Any] | None = Field(None, description="数据集 CSV 的列映射 {字段名: 列名}")

    @model_validator(mode="after")
    def _one_of(self) -> _ContentIn:
        if (self.content is None) == (self.blob_upload_id is None):
            raise ValueError("content 与 blob_upload_id 必须且只能提供一个")
        return self


class AssetCreate(_ContentIn):
    kind: AssetKind
    name: str
    description: str | None = None

    _v_name = field_validator("name")(_check_name)


class RevisionCreate(_ContentIn):
    pass


class AssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None


class RevisionOut(BaseModel):
    id: str
    asset_id: str
    rev: int
    blob_hash: str
    content_type: str
    size: int
    meta: dict[str, Any]
    message: str | None
    created_at: str


class RevisionDetail(RevisionOut):
    """带内容的修订。非 UTF-8 文本或过大（>5MB）时 content 为 null，用 /raw 下载。"""

    kind: str
    name: str
    content: str | None
    content_available: bool


class AssetOut(BaseModel):
    id: str
    kind: str
    name: str
    description: str | None
    latest_rev: int
    deleted_at: str | None
    created_at: str
    latest: RevisionOut | None = None


class AssetWriteOut(BaseModel):
    asset: AssetOut
    revision: RevisionOut
    existed: bool = Field(description="内容与已有修订相同，未新建修订")
    created: bool = Field(description="本次新建了资产")


class UploadOut(BaseModel):
    blob_upload_id: str
    size: int


class DatasetImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    content: str
    format: DatasetFormat = "jsonl"
    column_map: dict[str, Any] | None = None
    description: str | None = None
    message: str | None = None

    _v_name = field_validator("name")(_check_name)


class DatasetImportOut(BaseModel):
    asset: AssetOut
    revision: RevisionOut
    case_count: int
    warnings: list[dict[str, Any]]
    existed: bool
    created: bool


class CaseOut(BaseModel):
    id: str
    dataset_rev_id: str
    case_key: str
    idx: int
    input: dict[str, Any]
    expected: Any = None
    rubric: Any = None
    tags: list[str]
    metadata: dict[str, Any]
    case_hash: str


class CaseFilter(BaseModel):
    """用例过滤：case_keys 与 tags 同时给出时取交集；tags 为"含任一"。"""

    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(default_factory=list)
    case_keys: list[str] = Field(default_factory=list)


__all__ = [
    "ASSET_KINDS",
    "AssetCreate",
    "AssetKind",
    "AssetOut",
    "AssetUpdate",
    "AssetWriteOut",
    "CaseFilter",
    "CaseOut",
    "DatasetImport",
    "DatasetImportOut",
    "RevisionCreate",
    "RevisionDetail",
    "RevisionOut",
    "UploadOut",
]
