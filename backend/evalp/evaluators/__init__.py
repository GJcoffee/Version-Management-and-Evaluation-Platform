"""评估器（详设 8.5）：8 种类型、配置校验、evaluator_hash、构造。

- :func:`parse_evaluators`：校验配置数组（Pydantic），补齐 key / gate，非法配置抛 ``VALIDATION_ERROR``；
- :func:`resolve_evaluators`：在 parse 的基础上做需要查库的校验（llm_judge 的 provider、评审 prompt
  资产与变量），
  并把评审 prompt 引用固化为具体修订（``asset://prompt/x@3``），保证重新评分可复现；
- :func:`evaluator_hash`：单个评估器配置的哈希（不含 key 与 gate），用于"仅重新评分"时判断哪些评估器变了；
- :func:`build_evaluators`：构造评估器实例。

每个评估器：``async evaluate(case, transcript, events) -> ScoreResult``，见 :mod:`evalp.evaluators.base`。
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from evalp.context import AppContext
from evalp.errors import EvalpError, NotFound, SpecInvalid, ValidationFailed
from evalp.evaluators.base import (
    CaseView,
    Evaluator,
    EvaluatorConfig,
    ScoreResult,
    Transcript,
    TurnView,
)
from evalp.evaluators.human import HumanConfig, HumanEvaluator
from evalp.evaluators.llm_judge import LLMJudgeConfig, LLMJudgeEvaluator
from evalp.evaluators.python_eval import PythonConfig, PythonEvaluator
from evalp.evaluators.rules import (
    ContainsConfig,
    ContainsEvaluator,
    ExactMatchConfig,
    ExactMatchEvaluator,
    JsonSchemaConfig,
    JsonSchemaEvaluator,
    RegexConfig,
    RegexEvaluator,
)
from evalp.evaluators.trace import TraceConfig, TraceEvaluator
from evalp.services.judge import DEFAULT_PROMPT_NAME, JudgeService, validate_judge_template
from evalp.spec.models import format_validation_errors
from evalp.utils.canonical import canonical_json, sha256_hex

_CONFIGS: dict[str, type[EvaluatorConfig]] = {
    "exact_match": ExactMatchConfig,
    "contains": ContainsConfig,
    "regex": RegexConfig,
    "json_schema": JsonSchemaConfig,
    "trace": TraceConfig,
    "llm_judge": LLMJudgeConfig,
    "python": PythonConfig,
    "human": HumanConfig,
}
AnyConfig = Annotated[
    ExactMatchConfig
    | ContainsConfig
    | RegexConfig
    | JsonSchemaConfig
    | TraceConfig
    | LLMJudgeConfig
    | PythonConfig
    | HumanConfig,
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnyConfig)

EVALUATOR_TYPES: dict[str, type[Evaluator]] = {
    cls.type: cls
    for cls in (
        ExactMatchEvaluator,
        ContainsEvaluator,
        RegexEvaluator,
        JsonSchemaEvaluator,
        TraceEvaluator,
        LLMJudgeEvaluator,
        PythonEvaluator,
        HumanEvaluator,
    )
}
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

__all__ = [
    "EVALUATOR_TYPES",
    "CaseView",
    "Evaluator",
    "EvaluatorConfig",
    "ScoreResult",
    "Transcript",
    "TurnView",
    "build_evaluators",
    "config_to_dict",
    "evaluator_hash",
    "evaluators_hash",
    "parse_evaluators",
    "resolve_evaluators",
]


def _invalid(errors: list[dict[str, str]], message: str = "评估器配置不合法") -> ValidationFailed:
    return ValidationFailed(message, {"errors": errors})


def config_to_dict(cfg: EvaluatorConfig) -> dict[str, Any]:
    """配置 -> 用于存储的 dict（使用别名、补齐默认值与 gate）。"""
    data = cfg.model_dump(mode="json", by_alias=True)
    data["gate"] = cfg.gated
    return data


def evaluator_hash(cfg: EvaluatorConfig | dict[str, Any]) -> str:
    """单个评估器配置的哈希：不含 ``key`` 与 ``gate``（它们不影响评分结果）。"""
    data = config_to_dict(cfg) if isinstance(cfg, EvaluatorConfig) else dict(cfg)
    data.pop("key", None)
    data.pop("gate", None)
    return sha256_hex(canonical_json(data))


def evaluators_hash(configs: list[dict[str, Any]]) -> str:
    """整个评估器数组的哈希（含 key 与 gate）。"""
    return sha256_hex(canonical_json(configs))


def parse_evaluators(raw: Any) -> list[EvaluatorConfig]:
    """校验评估器配置数组；补齐 ``key``（缺省按类型生成，重复时加序号）。非法配置抛 ValidationFailed。"""
    if not isinstance(raw, list) or not raw:
        raise _invalid([{"path": "evaluators", "message": "至少需要一个评估器"}])
    configs: list[EvaluatorConfig] = []
    errors: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        kind = item.get("type") if isinstance(item, dict) else None
        if kind not in _CONFIGS:
            msg = f"type 必须是 {' / '.join(EVALUATOR_TYPES)} 之一"
            errors.append({"path": f"evaluators[{i}].type", "message": msg})
            continue
        try:
            configs.append(_CONFIGS[kind].model_validate(item))
        except ValidationError as e:
            for err in format_validation_errors(e):
                path = err["path"] if err["path"] != "(root)" else "(root)"
                errors.append(
                    {"path": f"evaluators[{i}].{path}".replace(".(root)", ""), "message": err["message"]}
                )
    if errors:
        raise _invalid(errors)
    used = {c.key for c in configs if c.key}
    counts: dict[str, int] = {}
    for c in configs:
        counts[c.type] = counts.get(c.type, 0) + 1
    seen_type: dict[str, int] = {}
    for i, c in enumerate(configs):
        if c.key:
            if not KEY_RE.match(c.key):
                msg = "key 只允许字母、数字、_ . -，最长 64"
                errors.append({"path": f"evaluators[{i}].key", "message": msg})
            continue
        seen_type[c.type] = seen_type.get(c.type, 0) + 1
        n = seen_type[c.type]
        key = c.type if counts[c.type] == 1 and c.type not in used else f"{c.type}_{n}"
        while key in used:
            n += 1
            key = f"{c.type}_{n}"
        c.key = key
        used.add(key)
    first: dict[str | None, int] = {}
    for i, c in enumerate(configs):
        if c.key in first:
            errors.append({"path": f"evaluators[{i}].key", "message": f"评估器 key 重复: {c.key}"})
        first.setdefault(c.key, i)
    if errors:
        raise _invalid(errors)
    return configs


async def resolve_evaluators(ctx: AppContext, raw: Any) -> list[EvaluatorConfig]:
    """parse + 需要查库的校验；llm_judge 的评审 prompt 引用被固化为具体修订。"""
    configs = parse_evaluators(raw)
    judge = ctx.svc(JudgeService)
    for i, c in enumerate(configs):
        if not isinstance(c, LLMJudgeConfig):
            continue
        path = f"evaluators[{i}]"
        try:
            if c.prompt.startswith(f"asset://prompt/{DEFAULT_PROMPT_NAME}@"):
                await judge.ensure_default_prompt()
            pinned, text = await judge.prompt_text(c.prompt)
        except (SpecInvalid, NotFound) as e:
            raise _invalid([{"path": f"{path}.prompt", "message": e.message}]) from None
        issues = validate_judge_template(text)
        if issues:
            raise _invalid([{"path": f"{path}.prompt", "message": m} for m in issues])
        c.prompt = pinned
        try:  # 顺便确认 provider / 模型存在：直接发布评审版本（相同配置复用同一版本）
            await judge.version_for(provider=c.provider, model=c.model, prompt_ref=pinned, params=c.params)
        except EvalpError as e:
            detail = e.details.get("errors") or [{"path": "provider", "message": e.message}]
            raise _invalid(
                [
                    {"path": f"{path}.{d.get('path', '')}".rstrip("."), "message": d.get("message", "")}
                    for d in detail
                ],
                f"评审配置不合法: {e.message}",
            ) from None
    return configs


def build_evaluators(ctx: AppContext | None, configs: list[dict[str, Any]]) -> list[Evaluator]:
    """由存储的配置 dict 构造评估器实例（key 取自配置）。"""
    out: list[Evaluator] = []
    for raw in configs:
        cfg = _ADAPTER.validate_python(raw)
        out.append(EVALUATOR_TYPES[cfg.type](cfg.key or cfg.type, cfg, ctx))
    return out
