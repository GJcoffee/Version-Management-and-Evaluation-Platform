"""平台维护的适配器注册表；版本和配置校验与执行实现一同发布。"""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evalp.errors import ValidationFailed
from evalp.spec.models import AdapterBinding


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
    timeout_s: float = Field(default=300, gt=0, le=3600)
    observe_base_url: str | None = None
    observation_wait_s: float = Field(default=15, ge=0, le=120)
    business_bridge: BusinessBridgeConfig | None = None

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


def list_adapters() -> list[dict[str, Any]]:
    """返回可用版本和配置定义。"""
    return [{
        "id": "hiagent",
        "name": "HiAgent",
        "version": "1.0.0",
        "recommended": True,
        "capabilities": ["streaming", "reasoning", "function_call", "mcp", "turn_usage", "trace"],
        "config_schema": HiAgentConfig.model_json_schema(),
    }]


def validate_binding(raw: dict[str, Any] | AdapterBinding) -> AdapterBinding:
    """固定具体实现版本；未知实现和不兼容字段不能进入运行。"""
    try:
        binding = AdapterBinding.model_validate(raw)
        if (binding.id, binding.version) != ("hiagent", "1.0.0"):
            raise ValueError(f"适配器不可用: {binding.id}@{binding.version}")
        config = HiAgentConfig.model_validate(binding.config)
        return binding.model_copy(update={"config": config.model_dump()})
    except ValidationError as exc:
        errors = [{
            "path": ".".join(str(segment) for segment in issue["loc"]),
            "message": issue["type"],
        } for issue in exc.errors()]
        raise ValidationFailed("适配器连接配置不合法", {"errors": errors}) from None
    except ValueError as exc:
        # 不包含 Pydantic 原始输入，防止错误回显凭据正文。
        raise ValidationFailed(str(exc)) from None
