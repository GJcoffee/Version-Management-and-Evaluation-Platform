"""绑定归一化：兼容旧的 ``id@version`` 与新的 ``adapter + major``，实现版本不再进入绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class NormalizedBinding:
    """运行期使用的接入配置；``major`` 是平台适配器大版本，不是 agent 版本。"""

    adapter: str
    major: int
    config: dict[str, Any]
    credential_ref: str

    @property
    def id(self) -> str:
        """兼容旧调用方（如健康检查详情）的字段名。"""
        return self.adapter

    @property
    def version(self) -> str:
        """兼容旧调用方的实现版本写法：``1`` -> ``1.0.0``。"""
        return f"{self.major}.0.0"


def _major_from_version(version: Any) -> int:
    text = str(version or "1.0.0").strip()
    try:
        return int(text.split(".")[0])
    except (ValueError, IndexError):
        raise ValueError(f"适配器版本不合法: {text}") from None


def normalize_binding(raw: NormalizedBinding | BaseModel | dict[str, Any]) -> NormalizedBinding:
    """把旧 ``{id, version}`` / 新 ``{adapter, major}`` 统一成运行期绑定。"""
    if isinstance(raw, NormalizedBinding):
        return raw
    data = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw)
    adapter = data.get("adapter") or data.get("id")
    if not isinstance(adapter, str) or not adapter:
        raise ValueError("绑定缺少 adapter / id")
    credential_ref = data.get("credential_ref")
    if not isinstance(credential_ref, str) or not credential_ref:
        raise ValueError("绑定缺少 credential_ref")
    major = data.get("major")
    if major is None:
        major = _major_from_version(data.get("version"))
    if isinstance(major, bool) or not isinstance(major, int) or major < 1:
        raise ValueError("适配器 major 必须是正整数")
    config = data.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("适配器 config 必须是对象")
    return NormalizedBinding(adapter=adapter, major=major, config=dict(config),
                             credential_ref=credential_ref)
