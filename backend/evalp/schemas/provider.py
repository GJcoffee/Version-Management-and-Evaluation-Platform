"""Provider 的请求 / 响应 DTO。价格单位：元 / 百万 token。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ModelInfo(BaseModel):
    """Provider 下的一个模型。"""

    name: Name
    context_window: int | None = Field(None, ge=1)
    price_in: float | None = Field(None, ge=0)
    price_out: float | None = Field(None, ge=0)


def _check_url(v: str) -> str:
    v = v.strip().rstrip("/")
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("base_url 必须是 http:// 或 https:// 开头的有效地址")
    return v


def _check_models(v: list[ModelInfo] | None) -> list[ModelInfo] | None:
    if v is not None:
        names = [m.name for m in v]
        if len(names) != len(set(names)):
            raise ValueError("models 中存在重复的模型名")
    return v


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    base_url: str
    api_key: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    models: list[ModelInfo] = Field(default_factory=list)
    rpm_limit: int | None = Field(None, ge=1)
    tpm_limit: int | None = Field(None, ge=1)
    max_concurrency: int = Field(8, ge=1)

    _v_url = field_validator("base_url")(_check_url)
    _v_models = field_validator("models")(_check_models)


class ProviderUpdate(BaseModel):
    """PATCH：只更新出现在请求体中的字段。``api_key: ""`` 删除密钥；省略或 null 保持不变。
    rpm_limit / tpm_limit 显式传 null 表示改为不限。"""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    models: list[ModelInfo] | None = None
    rpm_limit: int | None = Field(None, ge=1)
    tpm_limit: int | None = Field(None, ge=1)
    max_concurrency: int | None = Field(None, ge=1)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_url(v)

    _v_models = field_validator("models")(_check_models)


class ProviderOut(BaseModel):
    """响应中永不包含密钥，只有 has_api_key。"""

    id: str
    name: str
    base_url: str
    has_api_key: bool
    headers: dict[str, str]
    models: list[ModelInfo]
    rpm_limit: int | None
    tpm_limit: int | None
    max_concurrency: int
    created_at: str
    updated_at: str


class ProviderTestResult(BaseModel):
    ok: bool
    latency_ms: int
    models: list[str] | None = None
    error: str | None = None
