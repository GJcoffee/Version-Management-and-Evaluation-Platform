"""观测密钥连接；密钥只允许写入，不作为响应字段。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from evalp.schemas.account import normalize_origin


class KeyConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    origin: str
    top_url: str
    workspace_id: str = Field(min_length=1, max_length=200)
    credential_source: Literal["key", "hicli"] = "key"
    access_key_id: SecretStr | None = Field(None, min_length=1, max_length=4096)
    access_key_secret: SecretStr | None = Field(None, min_length=1, max_length=4096)

    _origins = field_validator("origin", "top_url")(normalize_origin)

    @field_validator("name", "workspace_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("连接名称和工作空间不能为空")
        return value.strip()

    @model_validator(mode="after")
    def _keys(self):
        if self.credential_source == "key":
            if not all(v and v.get_secret_value().strip()
                       for v in (self.access_key_id, self.access_key_secret)):
                raise ValueError("请填写完整的账号密钥")
        elif self.access_key_id is not None or self.access_key_secret is not None:
            raise ValueError("复用本机认证时无需填写密钥")
        return self


class KeyConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=100)
    enabled: bool | None = None
    access_key_id: SecretStr | None = Field(None, min_length=1, max_length=4096)
    access_key_secret: SecretStr | None = Field(None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _keys(self):
        if (self.access_key_id is None) != (self.access_key_secret is None):
            raise ValueError("更换密钥时必须同时填写标识和密钥正文")
        if self.name is not None and not self.name.strip():
            raise ValueError("连接名称不能为空")
        if any(v is not None and not v.get_secret_value().strip()
               for v in (self.access_key_id, self.access_key_secret)):
            raise ValueError("密钥不能为空白")
        return self
