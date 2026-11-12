"""HiAgent 绑定配置：连接地址、应用/版本与观测窗口；凭据只保存引用。"""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BusinessBridgeConfig(BaseModel):
    """已封存业务运行器的兼容入口，绑定不可变业务版本。"""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_s: float = Field(default=300, gt=0, le=3600)

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username
                or url.password or url.query or url.fragment):
            raise ValueError("业务桥接地址必须是无凭据的 HTTP(S) 地址")
        return value.rstrip("/")


class HiAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    app_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    published_version: str = Field(min_length=1)
    workspace_id: str | None = None
    timeout_s: float = Field(default=300, gt=0, le=3600)
    observe_base_url: str | None = None
    observe_account_ref: str | None = Field(None, min_length=1, description="Trace 自动登录账号引用")
    observation_wait_s: float = Field(default=15, ge=0, le=120)
    business_bridge: BusinessBridgeConfig | None = None

    @model_validator(mode="after")
    def _account_site(self):
        if self.observe_account_ref and (
            not self.observe_base_url or urlsplit(self.observe_base_url).path not in ("", "/")
        ):
            raise ValueError("选择 Trace 账号时，观测地址必须是该账号的站点根地址")
        return self

    @field_validator("base_url", "observe_base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("服务地址必须是 HTTP(S)，且不能包含凭据")
        if url.query or url.fragment:
            raise ValueError("服务地址不能包含查询参数或片段")
        return value.rstrip("/")
