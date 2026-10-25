"""规范化 JSON 与哈希（详设 4.4）。

规则：键排序、紧凑分隔符、ensure_ascii=False、浮点规范化（整数值的浮点数按整数输出，
其余用 repr）。Spec 模块可以在此基础上扩展（换行符、行尾空白等）。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

_MAX_SAFE_INT = 2**53


def normalize(obj: Any) -> Any:
    """递归规范化：键转 str、浮点整数值转 int、-0.0 转 0，拒绝 NaN/Infinity。"""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str | int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"不支持 NaN/Infinity: {obj!r}")
        if obj.is_integer() and abs(obj) < _MAX_SAFE_INT:
            return int(obj)
        return obj
    if isinstance(obj, dict):
        return {str(k): normalize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [normalize(v) for v in obj]
    raise TypeError(f"无法规范化的类型: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """输出规范化 JSON 字符串（键排序、紧凑、不转义非 ASCII）。"""
    return json.dumps(
        normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    """计算 sha256 十六进制摘要；str 按 UTF-8 编码。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
