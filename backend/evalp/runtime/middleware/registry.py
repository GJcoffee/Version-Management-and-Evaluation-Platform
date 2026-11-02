"""首期注册固定版本的 JSON 产物处理器。"""

from evalp.errors import ValidationFailed
from evalp.schemas.middleware import JsonArtifactConfig, MiddlewareSelection


def validate_selection(selection: MiddlewareSelection) -> None:
    """拒绝未知处理器和版本，避免静默采用最新实现。"""
    if (selection.id, selection.version) != ("json_artifact", "1.0.0"):
        raise ValidationFailed("不支持的轮间中间件或版本")


def descriptors() -> list[dict]:
    """返回可用处理器及配置 Schema。"""
    return [{
        "id": "json_artifact", "version": "1.0.0", "name": "读取上一轮 JSON",
        "input_modes": ["text"], "config_schema": JsonArtifactConfig.model_json_schema(),
    }]
