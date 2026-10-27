"""各类资产内容的校验（详设 5.1）。纯函数，不访问数据库。

| kind      | 校验                                                          |
|-----------|---------------------------------------------------------------|
| prompt    | UTF-8 文本；Jinja2 模板语法可解析；meta.variables 列出用到的变量 |
| tool      | YAML/JSON 解析为 ToolDefinition；parameters 是合法的 JSON Schema |
| knowledge | Markdown / TXT / JSONL（{id,text,meta}）；大小 <= 20MB          |
| kb_config / skill / mcp / app_config / database | UTF-8 文本（JSON / Markdown / YAML），按文本存储与 diff |
| dataset   | JSONL / CSV 逐行校验；警告写入 meta.schema_warnings              |
| file      | 任意；大小 <= 100MB                                            |
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import yaml
from jinja2 import TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from evalp.errors import ValidationFailed
from evalp.runtime.tools.definition import ToolDefinition
from evalp.services.dataset_parse import ParsedCase, parse_dataset
from evalp.spec.models import format_validation_errors

MB = 1024 * 1024
MAX_SIZE: dict[str, int] = {
    "prompt": 5 * MB,
    "tool": 5 * MB,
    "knowledge": 20 * MB,
    "kb_config": 5 * MB,
    "skill": 5 * MB,
    "mcp": 5 * MB,
    "app_config": 5 * MB,
    "database": 20 * MB,
    "dataset": 100 * MB,
    "file": 100 * MB,
}

# 只做文本存储与 diff 的类型（agent 快照里的导出配置，不参与运行）
TEXT_CONFIG_KINDS = ("kb_config", "skill", "mcp", "app_config", "database")

_JINJA = SandboxedEnvironment()


@dataclass
class ValidatedContent:
    """内容校验结果：content_type、写入 asset_revisions.meta 的内容、数据集用例与警告。"""

    content_type: str
    meta: dict[str, Any] = field(default_factory=dict)
    cases: list[ParsedCase] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


def default_content_type(kind: str, data: bytes) -> str:
    """未指定 content_type 时的默认值。"""
    if kind == "tool":
        return "application/json" if data.lstrip()[:1] in (b"{", b"[") else "application/x-yaml"
    if kind in TEXT_CONFIG_KINDS:
        return "application/json" if data.lstrip()[:1] in (b"{", b"[") else "text/markdown"
    return {
        "prompt": "text/markdown",
        "knowledge": "text/markdown",
        "dataset": "application/jsonl",
        "file": "application/octet-stream",
    }[kind]


def normalize_content_type(value: str | None) -> str | None:
    """去掉参数（``; charset=…``）并转小写。"""
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def decode_text(data: bytes, what: str = "内容") -> str:
    """严格按 UTF-8 解码。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationFailed(
            f"{what}不是合法的 UTF-8 文本",
            {"errors": [{"path": "content", "message": "不是合法的 UTF-8 文本"}]},
        ) from None


def check_template(text: str) -> list[str]:
    """校验 Jinja2 模板语法并返回用到的（未在模板内定义的）变量名；语法错误抛 TemplateSyntaxError。"""
    ast = _JINJA.parse(text)
    return sorted(meta.find_undeclared_variables(ast))


def template_issue(exc: TemplateSyntaxError) -> dict[str, Any]:
    """TemplateSyntaxError -> 错误项。"""
    return {
        "path": "content",
        "message": f"模板语法错误（第 {exc.lineno} 行）: {exc.message}",
        "line": exc.lineno,
    }


def parse_tool_content(text: str) -> ToolDefinition:
    """把 tool 资产内容（YAML/JSON）解析为 ToolDefinition；失败抛 ValidationFailed。"""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValidationFailed(
            "工具定义不是合法的 YAML/JSON",
            {"errors": [{"path": "content", "message": str(e).splitlines()[0]}]},
        ) from None
    if not isinstance(raw, dict):
        raise ValidationFailed(
            "工具定义必须是一个对象", {"errors": [{"path": "content", "message": "顶层必须是对象"}]}
        )
    try:
        tool = ToolDefinition.model_validate(raw)
    except ValidationError as e:
        errors = format_validation_errors(e)
        for item in errors:
            for tag in ("http", "python", "mock"):
                item["path"] = item["path"].replace(f"impl.{tag}.", "impl.").replace(f"impl.{tag}", "impl")
        raise ValidationFailed("工具定义校验失败", {"errors": errors}) from None
    try:
        Draft202012Validator.check_schema(tool.parameters)
    except SchemaError as e:
        raise ValidationFailed(
            "工具定义校验失败",
            {"errors": [{"path": "parameters", "message": f"不是合法的 JSON Schema: {e.message}"}]},
        ) from None
    return tool


def _validate_knowledge_jsonl(text: str) -> int:
    errors: list[dict[str, Any]] = []
    count = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append({"path": f"line {lineno}", "line": lineno, "message": f"不是合法的 JSON: {e.msg}"})
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
            errors.append(
                {
                    "path": f"line {lineno}",
                    "line": lineno,
                    "message": "每行应为 {id,text,meta} 对象，且 text 为字符串",
                }
            )
            continue
        count += 1
    if errors:
        raise ValidationFailed(
            f"知识库 JSONL 校验失败（共 {len(errors)} 处）",
            {"errors": errors[:100], "lines": [e["line"] for e in errors[:100]]},
        )
    return count


def validate_content(
    kind: str,
    data: bytes,
    content_type: str | None = None,
    *,
    column_map: dict[str, Any] | None = None,
) -> ValidatedContent:
    """校验资产内容并产出 meta；不合法抛 ValidationFailed（数据集抛 DatasetParseError）。"""
    ct = normalize_content_type(content_type) or default_content_type(kind, data)
    limit = MAX_SIZE[kind]
    if len(data) > limit:
        raise ValidationFailed(
            f"{kind} 资产不能超过 {limit // MB}MB（当前 {len(data) / MB:.1f}MB）",
            {"errors": [{"path": "content", "message": "内容过大"}], "limit": limit, "size": len(data)},
        )
    if kind == "file":
        return ValidatedContent(ct)
    text = decode_text(data)
    if kind in TEXT_CONFIG_KINDS:
        return ValidatedContent(ct)
    if kind == "prompt":
        try:
            variables = check_template(text)
        except TemplateSyntaxError as e:
            raise ValidationFailed("prompt 模板语法错误", {"errors": [template_issue(e)]}) from None
        return ValidatedContent(ct, {"variables": variables})
    if kind == "tool":
        tool = parse_tool_content(text)
        return ValidatedContent(ct, {"tool_name": tool.name, "impl_type": tool.impl.type})
    if kind == "knowledge":
        if ct in ("application/jsonl", "application/x-ndjson"):
            return ValidatedContent(ct, {"format": "jsonl", "doc_count": _validate_knowledge_jsonl(text)})
        return ValidatedContent(ct, {"format": "text"})
    # dataset
    fmt = "csv" if "csv" in ct else "jsonl"
    result = parse_dataset(data, fmt, column_map)
    meta_: dict[str, Any] = {
        "case_count": len(result.cases),
        "schema_warnings": result.warnings,
        "format": fmt,
    }
    if fmt == "csv" and column_map:
        meta_["column_map"] = column_map
    return ValidatedContent(ct, meta_, cases=result.cases, warnings=result.warnings)
