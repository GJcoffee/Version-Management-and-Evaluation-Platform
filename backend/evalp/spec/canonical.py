"""Spec 规范化与哈希（详设 4.4）。

规范化规则：

1. 用 Pydantic 模型补齐所有默认值后再导出（``exclude_none=False``）；
2. 资产引用已替换为 ``{"$asset": {"kind","name","rev","hash"}}``（哈希中包含内容哈希）；
3. JSON 键排序，``separators=(",",":")``，``ensure_ascii=False``；
4. 浮点数规范化（``1.0`` 与 ``1`` 等价，其余用 ``repr``）；
5. 内联文本（``prompts`` 下的全部字符串）统一换行符为 ``\\n``，并去掉每行行尾空白；
6. ``notes`` / ``name`` / ``description`` 不参与哈希；
7. 代码型的 ``snapshot`` 为空时从结果中去掉，``snapshot.map`` 为空时同样去掉（保持旧版本哈希稳定）。

``spec_hash = sha256("evalp/v1\\n" + canonical_json)``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evalp.spec.models import SCHEMA_VERSION, CodeSpec, ConfigSpec, dump_spec, parse_spec
from evalp.utils.canonical import canonical_json, sha256_hex

HASH_PREFIX = f"{SCHEMA_VERSION}\n"
EXCLUDED_KEYS: tuple[str, ...] = ("notes", "name", "description")


def normalize_text(s: str) -> str:
    """换行统一为 ``\\n``，每行去掉行尾空白（保留末尾换行）。"""
    return "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def _normalize_strings(obj: Any) -> Any:
    if isinstance(obj, str):
        return normalize_text(obj)
    if isinstance(obj, dict):
        return {k: _normalize_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_strings(v) for v in obj]
    return obj


@dataclass(frozen=True)
class CanonicalSpec:
    """规范化结果：``data`` 是规范化后的 dict（版本中存储的内容），``text`` 是参与哈希的 JSON 文本。"""

    data: dict[str, Any]
    text: str
    hash: str


def hash_text(canonical_text: str) -> str:
    """``sha256("evalp/v1\\n" + canonical)`` 的十六进制摘要。"""
    return sha256_hex(HASH_PREFIX + canonical_text)


def canonicalize(spec: ConfigSpec | CodeSpec | dict[str, Any]) -> CanonicalSpec:
    """规范化 Spec 并计算哈希。输入可以是 dict（草稿或固化后的 Spec）或已解析的模型。"""
    model = parse_spec(spec)
    data = dump_spec(model)
    for key in EXCLUDED_KEYS:
        data.pop(key, None)
    if data.get("snapshot") is None:
        data.pop("snapshot", None)  # 未声明快照时不出现，保持没有该字段的旧版本哈希不变
    elif not data["snapshot"]["map"]:
        del data["snapshot"]["map"]  # 没写入库规则时不出现，保持旧快照版本的哈希不变
    if "prompts" in data:
        data["prompts"] = _normalize_strings(data["prompts"])
    text = canonical_json(data)
    return CanonicalSpec(data=json.loads(text), text=text, hash=hash_text(text))


def spec_hash(spec: ConfigSpec | CodeSpec | dict[str, Any]) -> str:
    """便捷函数：只返回哈希。"""
    return canonicalize(spec).hash
