"""数据集解析（详设 5.3）：JSONL / CSV -> 用例。

- ``input`` 可以是字符串（单轮）或字符串数组（多轮），统一为 ``{"turns": [...]}``；
- 缺少 ``case_key`` 时用 ``sha256(input)[:12]`` 自动生成，并给出警告；
- ``case_hash = sha256(canonical({"input", "variables", "tool_mocks"}))``（详设 8.4），
  ``expected`` / ``rubric`` 不参与；
- 解析失败抛 ``DatasetParseError``，``details.lines`` 列出出错的行号。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from evalp.errors import DatasetParseError
from evalp.utils.canonical import canonical_json, sha256_hex

MAX_REPORTED_ERRORS = 100
MAX_WARNING_LINES = 20
CASE_KEY_MAX = 200
KNOWN_FIELDS = ("case_key", "input", "expected", "rubric", "tags", "metadata")
CSV_EXTRA_FIELDS = ("variables", "tool_mocks")
_PROMOTED = ("variables", "tool_mocks")  # 顶层写法等价于写在 metadata 里


@dataclass
class ParsedCase:
    """解析出的一条用例（line 为来源行号，从 1 开始）。"""

    line: int
    case_key: str
    input: dict[str, Any]
    expected: Any = None
    rubric: Any = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    case_hash: str = ""


@dataclass
class ParseResult:
    cases: list[ParsedCase]
    warnings: list[dict[str, Any]]


def compute_case_hash(input_: dict[str, Any], variables: Any = None, tool_mocks: Any = None) -> str:
    """``sha256(canonical({input, variables, tool_mocks}))``；缺省的 variables / tool_mocks 视为 ``{}``。"""
    return sha256_hex(
        canonical_json({"input": input_, "variables": variables or {}, "tool_mocks": tool_mocks or {}})
    )


def auto_case_key(turns: list[str]) -> str:
    """缺少 case_key 时的自动生成规则：``sha256(input)[:12]``（单轮取原文，多轮取数组的规范化 JSON）。"""
    basis = turns[0] if len(turns) == 1 else canonical_json(turns)
    return sha256_hex(basis)[:12]


class _Errors:
    """收集错误：行号 + 消息。"""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, line: int, message: str) -> None:
        self.items.append({"line": line, "message": message})

    def raise_if_any(self) -> None:
        if not self.items:
            return
        shown = self.items[:MAX_REPORTED_ERRORS]
        lines = sorted({e["line"] for e in self.items if e["line"] > 0})[:MAX_REPORTED_ERRORS]
        first = self.items[0]
        loc = f"第 {first['line']} 行：" if first["line"] > 0 else ""
        raise DatasetParseError(
            f"数据集解析失败（共 {len(self.items)} 处错误）：{loc}{first['message']}",
            {"lines": lines, "errors": shown, "total_errors": len(self.items)},
        )


class _Warnings:
    """聚合警告：同一类警告合并为一条，附出现次数和前若干行号。"""

    def __init__(self) -> None:
        self._by_code: dict[str, dict[str, Any]] = {}

    def add(self, code: str, message: str, line: int) -> None:
        w = self._by_code.setdefault(code, {"code": code, "message": message, "count": 0, "lines": []})
        w["count"] += 1
        if len(w["lines"]) < MAX_WARNING_LINES:
            w["lines"].append(line)

    def result(self) -> list[dict[str, Any]]:
        return list(self._by_code.values())


def _parse_turns(value: Any) -> list[str]:
    """把 input 的各种写法转为轮次列表；不合法抛 ValueError。"""
    if isinstance(value, dict):
        if set(value) != {"turns"}:
            raise ValueError('input 为对象时只能是 {"turns": [...]}')
        value = value["turns"]
        if not isinstance(value, list):
            raise ValueError("input.turns 必须是数组")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError("input 必须是字符串或非空的字符串数组")
    turns: list[str] = []
    for i, t in enumerate(value):
        if isinstance(t, dict) and t.get("role", "user") == "user" and isinstance(t.get("content"), str):
            t = t["content"]
        if not isinstance(t, str) or not t.strip():
            raise ValueError(f"input 第 {i + 1} 轮必须是非空字符串")
        turns.append(t)
    return turns


def _build_case(obj: Any, line: int, errors: _Errors, warnings: _Warnings) -> ParsedCase | None:
    """把一条原始记录（dict）校验并转为 ParsedCase；有错误时记录并返回 None。"""
    if not isinstance(obj, dict):
        errors.add(line, "每一行必须是 JSON 对象")
        return None
    n_before = len(errors.items)

    if "input" not in obj:
        errors.add(line, "缺少必填字段 input")
        turns: list[str] = []
    else:
        try:
            turns = _parse_turns(obj["input"])
        except ValueError as e:
            errors.add(line, str(e))
            turns = []

    key_raw = obj.get("case_key")
    case_key: str | None = None
    if key_raw is not None and key_raw != "":
        if isinstance(key_raw, bool) or not isinstance(key_raw, str | int):
            errors.add(line, "case_key 必须是字符串")
        else:
            case_key = str(key_raw).strip()
            if not case_key or len(case_key) > CASE_KEY_MAX:
                errors.add(line, f"case_key 不能为空且不超过 {CASE_KEY_MAX} 个字符")
                case_key = None

    tags_raw = obj.get("tags")
    tags: list[str] = []
    if tags_raw is not None:
        if isinstance(tags_raw, str):
            tags_raw = [tags_raw]
        if not isinstance(tags_raw, list) or not all(isinstance(t, str) and t for t in tags_raw):
            errors.add(line, "tags 必须是字符串数组")
        else:
            tags = list(dict.fromkeys(tags_raw))

    metadata_raw = obj.get("metadata")
    metadata: dict[str, Any] = {}
    if metadata_raw is not None:
        if not isinstance(metadata_raw, dict):
            errors.add(line, "metadata 必须是对象")
        else:
            metadata = dict(metadata_raw)
    for name in _PROMOTED:
        if name in obj and name not in metadata:
            metadata[name] = obj[name]
    for name in _PROMOTED:
        if metadata.get(name) is not None and not isinstance(metadata[name], dict):
            errors.add(line, f"{name} 必须是对象")

    for name in obj:
        if name not in KNOWN_FIELDS and name not in _PROMOTED:
            warnings.add("UNKNOWN_FIELD", "存在未知字段，已放入 metadata", line)
            metadata.setdefault(name, obj[name])

    if len(errors.items) > n_before:
        return None
    if case_key is None:
        case_key = auto_case_key(turns)
        warnings.add(
            "MISSING_CASE_KEY",
            "缺少 case_key，已用 sha256(input)[:12] 自动生成；输入文本变化后跨修订对比可能对不上",
            line,
        )
    input_ = {"turns": turns}
    return ParsedCase(
        line=line,
        case_key=case_key,
        input=input_,
        expected=obj.get("expected"),
        rubric=obj.get("rubric"),
        tags=tags,
        metadata=metadata,
        case_hash=compute_case_hash(input_, metadata.get("variables"), metadata.get("tool_mocks")),
    )


def _finish(cases: list[ParsedCase], errors: _Errors, warnings: _Warnings) -> ParseResult:
    seen: dict[str, int] = {}
    for c in cases:
        if c.case_key in seen:
            errors.add(c.line, f"case_key 重复: {c.case_key}（首次出现在第 {seen[c.case_key]} 行）")
        else:
            seen[c.case_key] = c.line
    errors.raise_if_any()
    if not cases:
        raise DatasetParseError("数据集没有任何用例", {"lines": [], "errors": []})
    return ParseResult(cases=cases, warnings=warnings.result())


def _decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data.lstrip("﻿")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise DatasetParseError("数据集不是合法的 UTF-8 文本", {"lines": [], "errors": []}) from e


def parse_jsonl(data: bytes | str) -> ParseResult:
    """解析 JSONL：空行忽略，逐行校验。"""
    text = _decode(data)
    errors, warnings = _Errors(), _Warnings()
    cases: list[ParsedCase] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.add(lineno, f"不是合法的 JSON（{e.msg}，第 {e.colno} 列）")
            continue
        case = _build_case(obj, lineno, errors, warnings)
        if case is not None:
            cases.append(case)
    return _finish(cases, errors, warnings)


def _split_tags(cell: str) -> list[str]:
    return [t.strip() for t in cell.replace("；", ";").split(";") if t.strip()]


def _normalize_column_map(column_map: dict[str, Any] | None, header: list[str]) -> dict[str, Any]:
    """column_map: ``{字段名: 列名}``（input 可以是列名数组，表示多轮）；缺省时按同名列。"""
    allowed = (*KNOWN_FIELDS, *CSV_EXTRA_FIELDS)
    if column_map:
        unknown = [k for k in column_map if k not in allowed]
        if unknown:
            raise DatasetParseError(
                f"column_map 含有未知字段: {', '.join(unknown)}（可用: {', '.join(allowed)}）",
                {"lines": [], "errors": []},
            )
        mapping = dict(column_map)
    else:
        mapping = {f: f for f in allowed if f in header}
    if "input" not in mapping:
        raise DatasetParseError(
            'CSV 需要通过 column_map 指定 input 列，例如 {"input": "问题"}',
            {"lines": [1], "errors": [{"line": 1, "message": "找不到 input 列"}]},
        )
    return mapping


def parse_csv(data: bytes | str, column_map: dict[str, Any] | None = None) -> ParseResult:
    """解析 CSV（首行为表头）；column_map 为 ``{字段名: 列名}``，如 ``{"input": "问题"}``。"""
    text = _decode(data)
    errors, warnings = _Errors(), _Warnings()
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise DatasetParseError("CSV 内容为空", {"lines": [], "errors": []}) from None
    mapping = _normalize_column_map(column_map, header)
    col_index: dict[str, int] = {h: i for i, h in enumerate(header)}
    missing: list[str] = []
    resolved: dict[str, int | list[int]] = {}
    for field_name, col in mapping.items():
        cols = col if isinstance(col, list) else [col]
        idxs: list[int] = []
        for c in cols:
            if not isinstance(c, str) or c.strip() not in col_index:
                missing.append(f"{field_name} -> {c}")
            else:
                idxs.append(col_index[c.strip()])
        if field_name == "input" and isinstance(col, list):
            resolved[field_name] = idxs
        elif idxs:
            resolved[field_name] = idxs[0]
    if missing:
        raise DatasetParseError(
            f"CSV 表头中找不到这些列: {', '.join(missing)}",
            {"lines": [1], "errors": [{"line": 1, "message": f"找不到列 {m}"} for m in missing]},
        )

    cases: list[ParsedCase] = []
    prev_line = reader.line_num
    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as e:
            errors.add(reader.line_num, f"CSV 格式错误: {e}")
            break
        start = prev_line + 1
        prev_line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue

        def cell(i: int) -> str:
            return row[i].strip() if i < len(row) else ""  # noqa: B023

        obj: dict[str, Any] = {}
        n_before = len(errors.items)
        for field_name, idx in resolved.items():
            if field_name == "input":
                if isinstance(idx, list):
                    obj["input"] = [c for c in (cell(i) for i in idx) if c]
                else:
                    raw = cell(idx)
                    obj["input"] = raw
                    if raw.startswith("["):
                        try:
                            parsed = json.loads(raw)
                            if (
                                isinstance(parsed, list)
                                and parsed
                                and all(isinstance(x, str) for x in parsed)
                            ):
                                obj["input"] = parsed
                        except json.JSONDecodeError:
                            pass
            elif field_name == "tags":
                obj["tags"] = _split_tags(cell(idx))  # type: ignore[arg-type]
            elif field_name in ("metadata", "variables", "tool_mocks"):
                raw = cell(idx)  # type: ignore[arg-type]
                if raw:
                    try:
                        obj[field_name] = json.loads(raw)
                    except json.JSONDecodeError:
                        errors.add(start, f"{field_name} 列不是合法的 JSON")
            else:
                raw = cell(idx)  # type: ignore[arg-type]
                if raw:
                    obj[field_name] = raw
        if len(errors.items) > n_before:
            continue
        case = _build_case(obj, start, errors, warnings)
        if case is not None:
            cases.append(case)
    return _finish(cases, errors, warnings)


def parse_dataset(
    data: bytes | str,
    fmt: Literal["jsonl", "csv"] = "jsonl",
    column_map: dict[str, Any] | None = None,
) -> ParseResult:
    """按格式解析数据集。"""
    if fmt == "csv":
        return parse_csv(data, column_map)
    return parse_jsonl(data)
