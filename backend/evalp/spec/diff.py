"""版本 Diff（详设 4.5）。

输入两个版本已固化的 Spec，递归比较：

- 字典：按键比较，add / remove / replace；
- 列表：元素有身份键时按身份键匹配（tools 按工具名；knowledge / sources 按资产名；内置工具按 builtin 名），
  否则按下标；
- 文本：多行或超过 80 个字符的字符串，额外附 unified diff（``content_diff``）；
- 资产：内容哈希相同视为未变化；不同时为 ``asset_changed``，文本类资产同时附 ``content_diff``。
"""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable
from typing import Any

from evalp.spec.refs import ASSET_KEY, is_pin

TEXT_DIFF_MIN_LEN = 80
TEXT_KINDS = ("prompt", "tool", "knowledge")

LoadText = Callable[[dict[str, Any]], Awaitable["str | None"]]


def unified_diff_text(left: str, right: str, left_label: str = "left", right_label: str = "right") -> str:
    """生成 unified diff 文本（不含 ``---`` / ``+++`` 文件头，从第一个 ``@@`` 开始）。"""
    lines = list(
        difflib.unified_diff(
            left.splitlines(), right.splitlines(), fromfile=left_label, tofile=right_label, lineterm="", n=3
        )
    )
    return "\n".join(lines[2:])


def unified_content_diff(left: str, right: str, left_label: str = "left", right_label: str = "right") -> dict:
    """``{"format": "unified", "text": …}``。"""
    return {"format": "unified", "text": unified_diff_text(left, right, left_label, right_label)}


def _needs_text_diff(left: Any, right: Any) -> bool:
    if not (isinstance(left, str) and isinstance(right, str)):
        return False
    return "\n" in left or "\n" in right or len(left) > TEXT_DIFF_MIN_LEN or len(right) > TEXT_DIFF_MIN_LEN


def _pin_summary(node: Any) -> Any:
    """固化引用 -> ``{kind,name,rev}``；其他值原样返回。"""
    if is_pin(node):
        p = node[ASSET_KEY]
        return {"kind": p["kind"], "name": p["name"], "rev": p["rev"]}
    return node


def _identity(elem: Any) -> str | None:
    if is_pin(elem):
        return str(elem[ASSET_KEY]["name"])
    if isinstance(elem, dict) and isinstance(elem.get("builtin"), str):
        return elem["builtin"]
    return None


def _keyed(items: list[Any]) -> dict[str, Any] | None:
    """所有元素都有身份键且不重复时返回 ``{key: elem}``，否则 None。"""
    out: dict[str, Any] = {}
    for el in items:
        key = _identity(el)
        if key is None or key in out:
            return None
        out[key] = el
    return out


def identity_paths(spec: Any) -> dict[str, str]:
    """固化 Spec 中每处资产引用的 ``下标路径 -> 身份键路径``（如 ``tools[0]`` -> ``tools[query-order]``）。

    身份键路径与 diff 的 ``changes[].path`` 用同一套规则（``_keyed``），``assets[].spec_path`` 对外使用它；
    库里 ``version_asset_refs.spec_path`` 仍存下标路径（校验报错的路径也是下标，指向草稿里的位置）。
    """
    out: dict[str, str] = {}

    def walk(node: Any, ipath: str, xpath: str) -> None:
        if is_pin(node):
            out[ipath] = xpath
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{ipath}.{k}" if ipath else str(k), f"{xpath}.{k}" if xpath else str(k))
        elif isinstance(node, list):
            keyed = _keyed(node)
            keys = list(keyed) if keyed is not None else None
            for i, v in enumerate(node):
                walk(v, f"{ipath}[{i}]", f"{xpath}[{keys[i] if keys is not None else i}]")

    walk(spec, "", "")
    return out


class _Differ:
    def __init__(self, load_text: LoadText | None) -> None:
        self.load_text = load_text
        self.changes: list[dict[str, Any]] = []
        self._cache: dict[str, str | None] = {}

    async def _text_of(self, node: Any) -> str | None:
        """节点对应的文本：内联字符串本身，或文本类资产的内容。"""
        if isinstance(node, str):
            return node
        if is_pin(node) and self.load_text is not None:
            pin = node[ASSET_KEY]
            if pin["kind"] not in TEXT_KINDS:
                return None
            if pin["hash"] not in self._cache:
                self._cache[pin["hash"]] = await self.load_text(pin)
            return self._cache[pin["hash"]]
        return None

    def _label(self, node: Any, fallback: str) -> str:
        if is_pin(node):
            p = node[ASSET_KEY]
            return f"{p['name']}@{p['rev']}"
        return fallback

    def _add(self, path: str, op: str, **kw: Any) -> None:
        self.changes.append({"path": path, "op": op, **kw})

    async def _replace(self, path: str, left: Any, right: Any) -> None:
        change: dict[str, Any] = {
            "path": path,
            "op": "replace",
            "left": _pin_summary(left),
            "right": _pin_summary(right),
        }
        if is_pin(left) or is_pin(right):
            lt, rt = await self._text_of(left), await self._text_of(right)
            if lt is not None and rt is not None:
                change["content_diff"] = unified_content_diff(
                    lt, rt, self._label(left, "left"), self._label(right, "right")
                )
        elif _needs_text_diff(left, right):
            change["content_diff"] = unified_content_diff(left, right)
        self.changes.append(change)

    async def _pin(self, path: str, left: Any, right: Any) -> None:
        if is_pin(left) and is_pin(right):
            pl, pr = left[ASSET_KEY], right[ASSET_KEY]
            if pl["kind"] == pr["kind"] and pl["name"] == pr["name"]:
                if pl["hash"] == pr["hash"]:
                    return
                change: dict[str, Any] = {
                    "path": path,
                    "op": "asset_changed",
                    "left": {"rev": pl["rev"]},
                    "right": {"rev": pr["rev"]},
                }
                lt, rt = await self._text_of(left), await self._text_of(right)
                if lt is not None and rt is not None:
                    change["content_diff"] = unified_content_diff(
                        lt, rt, self._label(left, "left"), self._label(right, "right")
                    )
                self.changes.append(change)
                return
        await self._replace(path, left, right)

    async def walk(self, left: Any, right: Any, path: str) -> None:
        if is_pin(left) or is_pin(right):
            await self._pin(path, left, right)
        elif isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                sub = f"{path}.{key}" if path else str(key)
                if key not in left:
                    self._add(sub, "add", right=_pin_summary(right[key]))
                elif key not in right:
                    self._add(sub, "remove", left=_pin_summary(left[key]))
                else:
                    await self.walk(left[key], right[key], sub)
        elif isinstance(left, list) and isinstance(right, list):
            lk, rk = _keyed(left), _keyed(right)
            if lk is not None and rk is not None:
                for key in [*lk, *[k for k in rk if k not in lk]]:
                    sub = f"{path}[{key}]"
                    if key not in rk:
                        self._add(sub, "remove", left=_pin_summary(lk[key]))
                    elif key not in lk:
                        self._add(sub, "add", right=_pin_summary(rk[key]))
                    else:
                        await self.walk(lk[key], rk[key], sub)
            else:
                for i in range(max(len(left), len(right))):
                    sub = f"{path}[{i}]"
                    if i >= len(left):
                        self._add(sub, "add", right=_pin_summary(right[i]))
                    elif i >= len(right):
                        self._add(sub, "remove", left=_pin_summary(left[i]))
                    else:
                        await self.walk(left[i], right[i], sub)
        elif left != right or isinstance(left, bool) != isinstance(right, bool):
            await self._replace(path, left, right)


async def diff_specs(
    left: dict[str, Any], right: dict[str, Any], load_text: LoadText | None = None
) -> dict[str, Any]:
    """比较两个固化后的 Spec，返回 ``{"changes": [...], "summary": {...}}``。

    load_text(pin) 用于读取文本类资产内容（给出 ``{kind,name,rev,hash}``），返回 None 表示不可读 / 非文本。
    """
    differ = _Differ(load_text)
    await differ.walk(left, right, "")
    summary = {"add": 0, "remove": 0, "replace": 0, "asset_changed": 0}
    for c in differ.changes:
        summary[c["op"]] += 1
    return {"changes": differ.changes, "summary": summary}
