"""平台适配器注册表：一个平台一个实现，绑定不保存实现版本。

旧绑定 ``{id, version}`` 仍可读取并归一化为 ``adapter + major``；新绑定只用 ``adapter + major``。
实现由平台统一发布，更新一次所有绑定生效；只有平台 API 不兼容才出现新 major。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from evalp.errors import ValidationFailed
from evalp.runtime.adapters.binding import NormalizedBinding, normalize_binding
from evalp.runtime.adapters.platforms.hiagent.config import BusinessBridgeConfig, HiAgentConfig
from evalp.spec.models import AdapterBinding

__all__ = [
    "AdapterDefinition",
    "BusinessBridgeConfig",
    "HiAgentConfig",
    "NormalizedBinding",
    "definitions",
    "list_adapters",
    "normalize_binding",
    "resolve",
    "validate_binding",
]

PLATFORMS_ROOT = Path(__file__).parent / "platforms"


@dataclass(frozen=True)
class AdapterDefinition:
    """一个平台的一个活跃实现；``sunset`` 为空表示仍可使用。"""

    adapter: str
    major: int
    name: str
    capabilities: tuple[str, ...]
    config_model: type[BaseModel]
    adapter_cls: type
    impl_package: str
    sunset: str | None = None

    @property
    def version(self) -> str:
        """兼容旧接口的实现版本写法。"""
        return f"{self.major}.0.0"

    @property
    def impl_digest(self) -> str:
        return _impl_digest(self.impl_package)


@lru_cache(maxsize=8)
def _impl_digest(package: str) -> str:
    """适配器包内源文件的排序哈希，用于记录每轮实际使用的实现。"""
    root = PLATFORMS_ROOT / package
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def definitions() -> tuple[AdapterDefinition, ...]:
    """当前发布的平台实现；新增平台只在这里登记一次。"""
    from evalp.runtime.adapters.platforms.hiagent.adapter import HiAgentAdapter

    return (
        AdapterDefinition(
            adapter="hiagent", major=1, name="HiAgent",
            capabilities=tuple(sorted(HiAgentAdapter.capabilities)),
            config_model=HiAgentConfig, adapter_cls=HiAgentAdapter, impl_package="hiagent",
        ),
    )


def _definition(adapter: str, major: int) -> AdapterDefinition | None:
    return next((d for d in definitions() if (d.adapter, d.major) == (adapter, major)), None)


def list_adapters() -> list[dict[str, object]]:
    """返回可用平台实现；``id`` / ``version`` 为旧界面兼容字段。"""
    return [{
        "id": d.adapter,
        "adapter": d.adapter,
        "version": d.version,
        "major": d.major,
        "name": d.name,
        "capabilities": list(d.capabilities),
        "config_schema": d.config_model.model_json_schema(),
        "impl_digest": d.impl_digest,
        "sunset": d.sunset,
    } for d in definitions()]


def resolve(raw: NormalizedBinding | BaseModel | dict) -> tuple[AdapterDefinition, type]:
    """按绑定解析实现；未知平台或已下线大版本返回明确的校验错误。"""
    try:
        binding = normalize_binding(raw)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from None
    definition = _definition(binding.adapter, binding.major)
    if definition is None:
        raise ValidationFailed(f"适配器不可用: {binding.adapter}@{binding.major}")
    if definition.sunset:
        raise ValidationFailed(f"适配器 {binding.adapter}@{binding.major} 已下线（{definition.sunset}）")
    return definition, definition.adapter_cls


def validate_binding(raw: dict | AdapterBinding) -> AdapterBinding:
    """校验绑定与凭据无关的部分；实现版本由注册表解析，不再写死 id + version。"""
    try:
        binding = AdapterBinding.model_validate(raw)
        definition, _ = resolve(binding)
        config = definition.config_model.model_validate(binding.config)
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
