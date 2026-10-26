"""YAML 写回：在原文件基础上更新数据并保留注释与键顺序（pull 重写 agent.yaml 用，详设 10.3）。

使用 ruamel.yaml 的往返（round-trip）模式：只改动数据变化的位置，未变化的键、注释、引号风格保持原样。
原文件不是合法 YAML、顶层不是映射，或往返失败时，退化为 PyYAML 整体重写（会丢注释）。
"""

from __future__ import annotations

import io
import re
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_DASH = re.compile(r"^(?P<indent>[ ]*)- ")
_KEY = re.compile(r"^(?P<indent>[ ]*)[^\s#-][^:#]*:\s*(#.*)?$")


def plain_dump(data: Any) -> str:
    """没有原文件可参照时的输出（与 PyYAML 的旧行为一致）。"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _detect_indent(text: str) -> tuple[int, int, int]:
    """推测原文件的缩进风格 -> (mapping, sequence, offset)；找不到序列时用 ``2, 4, 2``。"""
    lines = text.splitlines()
    for i, line in enumerate(lines[1:], start=1):
        m = _DASH.match(line)
        if not m:
            continue
        for prev in reversed(lines[:i]):
            if not prev.strip() or prev.lstrip().startswith("#"):
                continue
            km = _KEY.match(prev)
            if km:
                offset = len(m["indent"]) - len(km["indent"])
                if 0 <= offset <= 4:
                    return 2, offset + 2, offset
            break
    return 2, 4, 2


def _merge(old: Any, new: Any) -> Any:
    """把 ``new`` 的数据合并进 ``old``（原地修改并返回），尽量复用旧节点以保留注释。"""
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        for key in [k for k in old if k not in new]:
            del old[key]
        for key, value in new.items():
            old[key] = _merge(old[key], value) if key in old else value
        # 键顺序跟随 new（只在顺序确实不同时重排，避免打乱注释位置）
        order = [k for k in new if k in old]
        if list(old) != order:
            for key in order:
                old.move_to_end(key)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        for i, value in enumerate(new):
            if i < len(old):
                old[i] = _merge(old[i], value)
            else:
                old.append(value)
        del old[len(new) :]
        return old
    if type(old) is type(new) or (isinstance(old, str) and isinstance(new, str)):
        return old if old == new else new
    return new


def dump_preserving(old_text: str, data: dict[str, Any]) -> str:
    """用 ``data`` 更新 ``old_text`` 并返回新文本，保留 ``old_text`` 里的注释。"""
    try:
        rt = YAML(typ="rt")
        rt.preserve_quotes = True
        rt.allow_unicode = True
        rt.width = 4096
        rt.indent(*_detect_indent(old_text))
        doc = rt.load(old_text)
        if not isinstance(doc, CommentedMap):
            return plain_dump(data)
        merged = _merge(doc, data)
        buf = io.StringIO()
        rt.dump(merged, buf)
        text = buf.getvalue()
        # 校验：往返结果必须与目标数据一致，否则退化（宁可丢注释，不能写错内容）
        if yaml.safe_load(text) != data:
            return plain_dump(data)
        return text
    except Exception:  # noqa: BLE001 - ruamel 抛出的异常类型很多，统一退化
        return plain_dump(data)
