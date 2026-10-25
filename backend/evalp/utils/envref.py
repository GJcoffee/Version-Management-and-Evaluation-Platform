"""``${env:NAME}`` 引用的查找与替换（详设 5.2）。

用于工具定义中的 URL / headers 等字段：在**执行时**才读取环境变量，避免把密钥写进 Spec。
环境变量缺失时抛出 :class:`EnvRefError`（400 VALIDATION_ERROR），消息中带变量名。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from evalp.errors import ValidationFailed

ENV_REF_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


class EnvRefError(ValidationFailed):
    """引用的环境变量不存在。"""

    default_message = "环境变量未设置"

    def __init__(self, name: str) -> None:
        super().__init__(f"环境变量未设置: {name}（来自 ${{env:{name}}} 引用）", {"name": name})
        self.name = name


def find_env_refs(text: str) -> list[str]:
    """按出现顺序返回文本中引用的环境变量名（去重）。"""
    seen: dict[str, None] = {}
    for m in ENV_REF_RE.finditer(text):
        seen.setdefault(m.group(1), None)
    return list(seen)


def resolve_env(text: str, env: Mapping[str, str] | None = None) -> str:
    """把文本中的 ``${env:NAME}`` 替换为环境变量值；变量缺失抛 EnvRefError。

    ``env`` 默认为 ``os.environ``。值为空字符串视为已设置。
    """
    source = os.environ if env is None else env

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in source:
            raise EnvRefError(name)
        return source[name]

    return ENV_REF_RE.sub(_sub, text)


def resolve_env_deep(obj: Any, env: Mapping[str, str] | None = None) -> Any:
    """递归替换 dict / list / str 中的引用，返回新对象。"""
    if isinstance(obj, str):
        return resolve_env(obj, env)
    if isinstance(obj, dict):
        return {k: resolve_env_deep(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_deep(v, env) for v in obj]
    return obj
