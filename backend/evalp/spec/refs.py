"""引用语法的解析与格式化（详设 4.3）。

| 写法                              | 含义                              |
|-----------------------------------|-----------------------------------|
| ``asset://{kind}/{name}@latest``  | 最新修订（只能出现在草稿中）      |
| ``asset://{kind}/{name}@{rev}``   | 指定修订                          |
| ``file:relative/path``            | 工作区本地文件（只在 agent.yaml） |
| ``${env:NAME}``                   | 运行时环境变量，只识别、不解析    |

固化后的 Spec 中，资产引用以 ``{"$asset": {"kind","name","rev","hash"}}`` 表示。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ASSET_KINDS: tuple[str, ...] = ("prompt", "tool", "knowledge", "dataset", "file")
ASSET_NAME_MAX = 64
ASSET_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_ASSET_REF_RE = re.compile(r"^asset://([a-z]+)/([^/@\s]+)(?:@(\S*))?$")
ENV_REF_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_FILE_REF_RE = re.compile(r"^file:(?!//)(\S+)$")

ASSET_KEY = "$asset"


class RefError(ValueError):
    """引用语法错误。"""


def validate_asset_name(name: str) -> str:
    """校验资产名：只允许小写字母、数字、连字符，首尾必须是字母或数字，最长 64。"""
    if not isinstance(name, str) or not name:
        raise RefError("资产名不能为空")
    if len(name) > ASSET_NAME_MAX:
        raise RefError(f"资产名过长（最多 {ASSET_NAME_MAX} 个字符）: {name[:20]}…")
    if not ASSET_NAME_RE.match(name):
        raise RefError(f"资产名只允许小写字母、数字、连字符，且首尾必须是字母或数字: {name!r}")
    return name


def validate_asset_kind(kind: str) -> str:
    """校验资产类型。"""
    if kind not in ASSET_KINDS:
        raise RefError(f"未知的资产类型 {kind!r}，可选: {', '.join(ASSET_KINDS)}")
    return kind


@dataclass(frozen=True)
class AssetRef:
    """资产引用；rev 为 None 表示 ``@latest``。"""

    kind: str
    name: str
    rev: int | None = None

    @property
    def is_latest(self) -> bool:
        return self.rev is None

    def __str__(self) -> str:
        return format_asset_ref(self.kind, self.name, self.rev)


def parse_asset_ref(text: str) -> AssetRef:
    """解析 ``asset://kind/name@latest|rev``；省略 ``@…`` 时按 latest 处理。格式错误抛 RefError。"""
    m = _ASSET_REF_RE.match(text.strip()) if isinstance(text, str) else None
    if m is None:
        raise RefError(f"不是合法的资产引用（应为 asset://kind/name@latest 或 @rev）: {text!r}")
    kind, name, rev_s = m.groups()
    validate_asset_kind(kind)
    validate_asset_name(name)
    if rev_s is None or rev_s == "latest":
        return AssetRef(kind, name, None)
    if not rev_s.isdigit() or int(rev_s) < 1:
        raise RefError(f"修订号必须是正整数或 latest: {text!r}")
    return AssetRef(kind, name, int(rev_s))


def format_asset_ref(kind: str, name: str, rev: int | str | None = None) -> str:
    """格式化为 ``asset://kind/name@rev``（rev 为 None 或 "latest" 时写 ``@latest``）。"""
    tail = "latest" if rev is None or rev == "latest" else str(int(rev))
    return f"asset://{kind}/{name}@{tail}"


def is_asset_ref(text: Any) -> bool:
    """字符串整体是否是一个单行的 ``asset://`` 引用（用于区分内联文本与引用）。"""
    return isinstance(text, str) and "\n" not in text.strip() and text.strip().startswith("asset://")


def is_file_ref(text: Any) -> bool:
    """字符串整体是否是 ``file:相对路径`` 引用。"""
    return isinstance(text, str) and _FILE_REF_RE.match(text.strip()) is not None


def parse_file_ref(text: str) -> str:
    """取出 ``file:`` 引用中的路径；格式错误抛 RefError。"""
    m = _FILE_REF_RE.match(text.strip()) if isinstance(text, str) else None
    if m is None:
        raise RefError(f"不是合法的 file: 引用: {text!r}")
    return m.group(1)


def find_env_refs(text: str) -> list[str]:
    """返回字符串中出现的所有 ``${env:NAME}`` 的变量名（只识别，不解析；保持出现顺序，去重）。"""
    seen: dict[str, None] = {}
    for m in ENV_REF_RE.finditer(text):
        seen.setdefault(m.group(1), None)
    return list(seen)


def classify(text: str) -> str:
    """把字符串分类为 ``asset`` / ``file`` / ``env`` / ``inline``。"""
    if is_asset_ref(text):
        return "asset"
    if is_file_ref(text):
        return "file"
    if ENV_REF_RE.search(text):
        return "env"
    return "inline"


def is_pin(node: Any) -> bool:
    """是否是固化后的引用 ``{"$asset": {...}}``。"""
    return isinstance(node, Mapping) and set(node) == {ASSET_KEY} and isinstance(node[ASSET_KEY], Mapping)


def make_pin(kind: str, name: str, rev: int, hash_: str) -> dict[str, Any]:
    """构造固化引用。"""
    return {ASSET_KEY: {"kind": kind, "name": name, "rev": rev, "hash": hash_}}


def pin_to_ref(node: Mapping[str, Any]) -> str:
    """固化引用 -> ``asset://kind/name@rev``。"""
    p = node[ASSET_KEY]
    return format_asset_ref(p["kind"], p["name"], p["rev"])


def node_ref(node: Any) -> AssetRef | None:
    """把 Spec 中的一个节点（引用字符串或固化引用）转为 AssetRef；既不是引用则返回 None。"""
    if is_pin(node):
        p = node[ASSET_KEY]
        return AssetRef(p["kind"], p["name"], int(p["rev"]))
    if is_asset_ref(node):
        return parse_asset_ref(node)
    return None
