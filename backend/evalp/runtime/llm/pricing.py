"""计费（详设 6.8）。

``cost = prompt_tokens × price_in + completion_tokens × price_out``，价格单位：元 / 百万 token。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PER_MILLION = 1_000_000


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def find_model(models: Iterable[Any], name: str) -> Any | None:
    """在 Provider 的 models 列表（ModelInfo 或 dict）里按名称查找。"""
    for m in models:
        if _get(m, "name") == name:
            return m
    return None


def compute_cost(model: Any | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """按模型单价计算费用（元）。模型缺失或两个价格都未配置时返回 None（表示价格未知）。"""
    if model is None:
        return None
    price_in, price_out = _get(model, "price_in"), _get(model, "price_out")
    if price_in is None and price_out is None:
        return None
    return (prompt_tokens * (price_in or 0.0) + completion_tokens * (price_out or 0.0)) / PER_MILLION
