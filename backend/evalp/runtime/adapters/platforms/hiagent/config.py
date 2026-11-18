"""HiAgent 绑定配置：连接地址、应用/版本与观测窗口；凭据只保存引用。"""

from typing import Literal
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
    observe_key_ref: str | None = Field(None, min_length=1, description="执行链路密钥连接引用")
    observe_transport: Literal["web", "hicli"] = "web"
    observe_hicli_base_url: str | None = Field(
        None, description="hicli 观测查询使用的 TOP 根地址；凭据由 hicli auth 管理",
    )
    observation_wait_s: float = Field(default=15, ge=0, le=120)
    business_bridge: BusinessBridgeConfig | None = None

    @model_validator(mode="after")
    def _account_site(self):
        if self.observe_key_ref and self.observe_transport != "hicli":
            raise ValueError("密钥连接需要使用 hicli 观测方式")
        if self.observe_transport == "hicli":
            if not self.observe_base_url or not self.observe_hicli_base_url or not self.workspace_id:
                raise ValueError("hicli 观测需要站点地址、TOP 根地址和工作空间编号")
            if urlsplit(self.observe_hicli_base_url).path not in ("", "/"):
                raise ValueError("hicli 观测的 TOP 地址必须是站点根地址")
            if self.observe_account_ref:
                raise ValueError("hicli 观测使用账号密钥，不能同时选择网页登录账号")
        if self.observe_account_ref and (
            not self.observe_base_url or urlsplit(self.observe_base_url).path not in ("", "/")
        ):
            raise ValueError("选择 Trace 账号时，观测地址必须是该账号的站点根地址")
        return self

    @field_validator("base_url", "observe_base_url", "observe_hicli_base_url")
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
