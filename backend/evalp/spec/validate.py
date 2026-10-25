"""Spec 业务校验（详设 4.4 第 1、2 步）。

检查内容：

- 结构（Pydantic）；
- 配置型：provider 与模型存在于 providers 表（模型名需出现在 provider.models 中；``models`` 为空则只给警告）；
  引用的资产存在且未删除；tool 资产内容能解析为 ToolDefinition，工具名不重复；prompt 模板能被 Jinja2 解析；
- 代码型：transport 配置完整、地址格式合理。

返回 ``ValidationReport``：``errors`` / ``warnings`` 为 ``[{path, message}]``；通过时附带固化后的 Spec。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from evalp.context import AppContext
from evalp.errors import NotFound, SpecInvalid, ValidationFailed
from evalp.services.asset_content import check_template, parse_tool_content
from evalp.spec.canonical import canonicalize
from evalp.spec.models import CodeSpec, ConfigSpec, format_validation_errors, parse_spec
from evalp.spec.refs import find_env_refs
from evalp.spec.resolve import RefUse, resolve_spec
from evalp.store.models import Provider
from jinja2 import TemplateSyntaxError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

TEMPLATE_BUILTINS = frozenset({"variables", "knowledge", "now"})


@dataclass
class ValidationReport:
    """校验报告。stage: ``schema``（结构错误）/ ``semantic``（业务错误）/ ``ok``。"""

    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    stage: str = "ok"
    spec: ConfigSpec | CodeSpec | None = None
    resolved: dict[str, Any] | None = None
    refs: list[RefUse] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str) -> None:
        self.errors.append({"path": path, "message": message})

    def warn(self, path: str, message: str) -> None:
        self.warnings.append({"path": path, "message": message})

    def to_dict(self) -> dict[str, Any]:
        """API 响应：``{ok, errors, warnings, resolved_preview}``（预览即发布时会存储的规范化 Spec）。"""
        preview = canonicalize(self.resolved).data if self.ok and self.resolved is not None else None
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "resolved_preview": preview}

    def raise_for_errors(self) -> None:
        """有错误时抛异常：结构错误 -> VALIDATION_ERROR；业务错误 -> SPEC_INVALID。"""
        if self.ok:
            return
        details = {"errors": self.errors, "warnings": self.warnings}
        if self.stage == "schema":
            raise ValidationFailed("Spec 结构校验失败", details)
        raise SpecInvalid(
            "Spec 校验失败：" + "；".join(f"{e['path']}: {e['message']}" for e in self.errors[:3]), details
        )


async def validate_spec(
    ctx: AppContext, s: AsyncSession, raw: Any, *, agent_kind: str | None = None
) -> ValidationReport:
    """校验草稿 Spec（原始 dict）。"""
    report = ValidationReport()
    if not isinstance(raw, dict):
        report.error("(root)", "Spec 必须是对象")
        report.stage = "schema"
        return report
    try:
        spec = parse_spec(raw)
    except ValidationError as e:
        report.errors = format_validation_errors(e)
        report.stage = "schema"
        return report
    if agent_kind is not None and spec.kind != agent_kind:
        report.error("kind", f"Spec 的 kind={spec.kind} 与 agent 的类型 {agent_kind} 不一致")
        report.stage = "schema"
        return report
    report.spec = spec

    result = await resolve_spec(s, spec)
    report.resolved, report.refs = result.resolved, result.refs
    report.errors.extend(result.errors)
    if isinstance(spec, ConfigSpec):
        await _check_config(ctx, s, spec, report)
    else:
        _check_code(spec, report)
    report.stage = "semantic" if report.errors else "ok"
    return report


async def _read_ref_text(ctx: AppContext, use: RefUse, report: ValidationReport) -> str | None:
    try:
        return (await ctx.blobs.get_bytes(use.hash)).decode("utf-8")
    except NotFound:
        report.error(use.spec_path, f"资产内容缺失: {use.kind}/{use.name}@{use.rev}")
    except UnicodeDecodeError:
        report.error(use.spec_path, f"资产不是 UTF-8 文本: {use.kind}/{use.name}@{use.rev}")
    return None


async def _check_config(ctx: AppContext, s: AsyncSession, spec: ConfigSpec, report: ValidationReport) -> None:
    # provider / 模型
    provider = (
        await s.execute(select(Provider).where(Provider.name == spec.model.provider))
    ).scalar_one_or_none()
    if provider is None:
        report.error("model.provider", f"Provider 不存在: {spec.model.provider}")
    else:
        names = [m["name"] for m in (provider.models or [])]
        if not names:
            report.warn(
                "model.name", f"Provider {provider.name} 没有登记模型列表，无法校验模型名 {spec.model.name}"
            )
        elif spec.model.name not in names:
            report.error(
                "model.name",
                f"模型 {spec.model.name} 不在 Provider {provider.name} 的模型列表中"
                f"（可选: {', '.join(names)}）",
            )

    by_path = {u.spec_path: u for u in report.refs}

    # prompt
    system_use = by_path.get("prompts.system")
    system_text: str | None
    if system_use is not None:
        system_text = await _read_ref_text(ctx, system_use, report)
    else:
        raw = spec.prompts.system
        system_text = raw if isinstance(raw, str) and not _is_unresolved_ref(raw) else None
    if system_text is not None:
        if not system_text.strip():
            report.warn("prompts.system", "system prompt 为空")
        else:
            try:
                used = check_template(system_text)
            except TemplateSyntaxError as e:
                report.error("prompts.system", f"模板语法错误（第 {e.lineno} 行）: {e.message}")
            else:
                known = TEMPLATE_BUILTINS | set(spec.prompts.variables)
                missing = [v for v in used if v not in known]
                if missing:
                    report.warn(
                        "prompts.system",
                        f"模板使用了未定义的变量: {', '.join(missing)}"
                        "（请在 prompts.variables 中定义，或运行时传入）",
                    )

    # 工具
    seen: dict[str, str] = {}
    for i, tool in enumerate(spec.tools):
        path = f"tools[{i}]"
        if isinstance(tool, str) or not hasattr(tool, "builtin"):
            use = by_path.get(path)
            if use is None:
                continue  # 引用本身已报错
            text = await _read_ref_text(ctx, use, report)
            if text is None:
                continue
            try:
                name = parse_tool_content(text).name
            except ValidationFailed as e:
                for issue in e.details.get("errors", []):
                    report.error(
                        path, f"工具 {use.name}@{use.rev} 定义不合法（{issue['path']}）: {issue['message']}"
                    )
                continue
        else:
            name = tool.builtin
            if not tool.with_.sources:
                report.warn(f"{path}.with.sources", "knowledge_search 没有配置 sources，检索不到任何内容")
        if name in seen:
            report.error(path, f"工具名重复: {name}（与 {seen[name]} 相同）")
        else:
            seen[name] = path


def _is_unresolved_ref(value: str) -> bool:
    return value.strip().startswith("asset://") and "\n" not in value.strip()


def _walk_strings(obj: Any, path: str):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def _check_code(spec: CodeSpec, report: ValidationReport) -> None:
    if spec.transport == "http" and spec.http is not None:
        url = spec.http.base_url.strip()
        if not (url.startswith(("http://", "https://")) or url.startswith("${env:")):
            report.error("http.base_url", "base_url 必须以 http:// 或 https:// 开头")
    if spec.transport == "subprocess" and spec.subprocess is not None:
        if not spec.subprocess.command[0].strip():
            report.error("subprocess.command", "command 的第一项（可执行文件）不能为空")
    data = spec.model_dump(by_alias=True, mode="json", include={"http", "subprocess", "params"})
    missing: dict[str, str] = {}
    for path, text in _walk_strings(data, ""):
        for name in find_env_refs(text):
            if name not in os.environ:
                missing.setdefault(name, path)
    for name, path in missing.items():
        report.warn(path, f"引用的环境变量 {name} 在服务进程中未设置（运行时由平台进程读取）")
