"""平台账号配置；API 从不回显密码与 Cookie。"""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def normalize_origin(value: str) -> str:
    url = urlsplit(value.strip())
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("账号站点必须是无路径、无凭据的 HTTP(S) 地址")
    host = url.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    port = url.port
    suffix = f":{port}" if port and port != (443 if url.scheme == "https" else 80) else ""
    return f"{url.scheme}://{host}{suffix}"


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: str = "hiagent"
    name: str = Field(min_length=1, max_length=100)
    origin: str
    tenant_name: str = Field("", max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=4096)

    _origin = field_validator("origin")(normalize_origin)

    @field_validator("name", "username", "tenant_name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=100)
    password: SecretStr | None = Field(None, max_length=4096)
    enabled: bool | None = None


class AccountSessionImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headers: SecretStr = Field(min_length=1, max_length=32768)
