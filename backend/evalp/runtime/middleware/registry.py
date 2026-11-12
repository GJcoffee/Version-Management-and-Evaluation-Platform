"""固定版本的轮间中间件注册表。"""

from __future__ import annotations

from pydantic import ValidationError

from evalp.errors import ValidationFailed
from evalp.schemas.middleware import (
    JsonArtifactConfig,
    MiddlewareSelection,
    OutputContractConfig,
)

# (id, version) -> (phase, 配置模型)
DEFINITIONS: dict[tuple[str, str], tuple[str, type]] = {
    ("json_artifact", "1.0.0"): ("input", JsonArtifactConfig),
    ("output_contract", "1.0.0"): ("output", OutputContractConfig),
}

NAMES = {
    "json_artifact": "读取上一轮 JSON",
    "output_contract": "校验本轮输出契约",
}


def phase_of(selection: MiddlewareSelection) -> str | None:
    """选择的执行阶段；未知处理器返回 None。"""
    spec = DEFINITIONS.get((selection.id, selection.version))
    return spec[0] if spec else None


def validate_selection(selection: MiddlewareSelection) -> None:
    """拒绝未知处理器和版本，并按处理器 Schema 复核配置。"""
    spec = DEFINITIONS.get((selection.id, selection.version))
    if spec is None:
        raise ValidationFailed("不支持的轮间中间件或版本")
    model = spec[1]
    try:
        model.model_validate(selection.config.model_dump())
    except ValidationError as exc:
        raise ValidationFailed(
            "轮间中间件配置不合法",
            {"errors": [{"path": f"config.{'.'.join(map(str, e['loc']))}", "message": e["msg"]}
                        for e in exc.errors()]},
        ) from None


def descriptors() -> list[dict]:
    """返回可用处理器及配置 Schema。"""
    return [
        {
            "id": mid, "version": version, "name": NAMES[mid], "phase": phase,
            "input_modes": ["text"], "config_schema": model.model_json_schema(),
        }
        for (mid, version), (phase, model) in DEFINITIONS.items()
    ]
