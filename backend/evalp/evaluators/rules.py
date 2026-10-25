"""规则类评估器：exact_match、contains、regex、json_schema（详设 8.5）。"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar, Literal

import jsonschema
from pydantic import Field, field_validator

from evalp.evaluators.base import (
    CaseView,
    Evaluator,
    EvaluatorConfig,
    ScoreResult,
    Transcript,
    stringify,
)
from evalp.runtime.events import AgentEvent

NormalizeOp = Literal["strip", "lower", "nfkc", "punct"]
_REGEX_FLAGS = {"i": re.I, "m": re.M, "s": re.S, "x": re.X, "a": re.A}


def normalize_text(text: str, ops: list[str]) -> str:
    """按固定顺序（nfkc -> lower -> punct -> strip）应用规范化，与 ops 的书写顺序无关。"""
    if "nfkc" in ops:
        text = unicodedata.normalize("NFKC", text)
    if "lower" in ops:
        text = text.lower()
    if "punct" in ops:
        text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    if "strip" in ops:
        text = text.strip()
    return text


def compile_flags(flags: list[str] | str) -> int:
    value = 0
    for f in flags:
        value |= _REGEX_FLAGS[f]
    return value


def _check_pattern(pattern: str, flags: list[str] | str = ()) -> None:
    try:
        re.compile(pattern, compile_flags(list(flags)))
    except re.error as e:
        raise ValueError(f"正则不合法: {e}") from None


def expected_list(expected: Any) -> list[str]:
    """expected -> 候选答案列表（字符串或数字为单个候选；数组为多个候选）。"""
    if expected is None:
        return []
    if isinstance(expected, list):
        return [stringify(x) for x in expected]
    return [stringify(expected)]


def pick_field(text: str, field: str) -> tuple[str | None, str | None]:
    """按 ``field`` 取待比较的文本：``final`` 为完整回复；``regex:模式`` 为第一个匹配（有分组取第 1 组）。"""
    if field == "final":
        return text, None
    pattern = field[len("regex:") :]
    m = re.search(pattern, text, re.S)
    if m is None:
        return None, f"回复中没有匹配 {pattern!r} 的内容"
    return (m.group(1) if m.re.groups else m.group(0)), None


# ---------------------------------------------------------------- exact_match


class ExactMatchConfig(EvaluatorConfig):
    type: Literal["exact_match"]
    field: str = "final"
    normalize: list[NormalizeOp] = Field(default_factory=lambda: ["strip"])

    @field_validator("field")
    @classmethod
    def _field(cls, v: str) -> str:
        if v == "final":
            return v
        if not v.startswith("regex:") or len(v) <= len("regex:"):
            raise ValueError("field 只能是 'final' 或 'regex:<模式>'")
        _check_pattern(v[len("regex:") :])
        return v


class ExactMatchEvaluator(Evaluator):
    type: ClassVar[str] = "exact_match"

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: ExactMatchConfig = self.config
        candidates = expected_list(case.expected)
        if not candidates:
            return ScoreResult.error("用例没有 expected，无法做精确匹配")
        got, err = pick_field(transcript.final, cfg.field)
        if got is None:
            return ScoreResult.of(False, err or "没有可比较的内容")
        got_n = normalize_text(got, cfg.normalize)
        for cand in candidates:
            if got_n == normalize_text(cand, cfg.normalize):
                return ScoreResult.of(True, "与期望答案一致")
        shown = got_n if len(got_n) <= 80 else got_n[:80] + "…"
        return ScoreResult.of(False, f"与期望答案不一致（回复：{shown!r}）")


# ---------------------------------------------------------------- contains


class ContainsConfig(EvaluatorConfig):
    type: Literal["contains"]
    values: list[str] = Field(default_factory=list)  # 为空时使用 expected
    mode: Literal["all", "any"] = "all"
    negate: bool = False
    case_sensitive: bool = True


class ContainsEvaluator(Evaluator):
    type: ClassVar[str] = "contains"

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: ContainsConfig = self.config
        values = cfg.values or expected_list(case.expected)
        if not values:
            return ScoreResult.error("没有配置 values，用例也没有 expected")
        text = transcript.final
        if not cfg.case_sensitive:
            text = text.lower()
        hits = [(v.lower() if not cfg.case_sensitive else v) in text for v in values]
        matched = sum(hits)
        base = all(hits) if cfg.mode == "all" else any(hits)
        passed = base != cfg.negate
        missing = [v for v, h in zip(values, hits, strict=True) if not h]
        found = [v for v, h in zip(values, hits, strict=True) if h]
        if cfg.negate:
            reason = "未出现禁止内容" if passed else f"出现了不该出现的内容: {', '.join(map(repr, found))}"
        elif passed:
            reason = "包含全部期望内容" if cfg.mode == "all" else f"包含 {found[0]!r}"
        else:
            reason = f"缺少: {', '.join(map(repr, missing))}"
        partial = matched / len(values) if cfg.mode == "all" and not cfg.negate else 0.0
        return ScoreResult.of(passed, reason, score=partial, matched=matched, total=len(values))


# ---------------------------------------------------------------- regex


class RegexConfig(EvaluatorConfig):
    type: Literal["regex"]
    pattern: str = Field(min_length=1)
    flags: list[Literal["i", "m", "s", "x", "a"]] = Field(default_factory=list)

    @field_validator("pattern")
    @classmethod
    def _compile(cls, v: str) -> str:
        _check_pattern(v)
        return v


class RegexEvaluator(Evaluator):
    type: ClassVar[str] = "regex"

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: RegexConfig = self.config
        m = re.search(cfg.pattern, transcript.final, compile_flags(cfg.flags))
        if m is None:
            return ScoreResult.of(False, f"回复中没有匹配 {cfg.pattern!r}")
        return ScoreResult.of(True, f"匹配到 {m.group(0)[:60]!r}")


# ---------------------------------------------------------------- json_schema

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)


def extract_json(text: str, mode: str = "auto") -> tuple[Any, str | None]:
    """从回复中提取 JSON。

    ``codeblock``：只看 Markdown 代码块；``auto``：先整体解析，再试代码块，最后取第一个括号配平的 ``{…}`` /
    ``[…]``。
    返回 ``(值, 错误信息)``。
    """
    candidates: list[str] = []
    if mode == "auto":
        candidates.append(text.strip())
    candidates.extend(m.group(1).strip() for m in _FENCE_RE.finditer(text))
    for c in candidates:
        try:
            return json.loads(c), None
        except ValueError:
            continue
    if mode == "auto":
        dec = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch in "{[":
                try:
                    value, _ = dec.raw_decode(text[i:])
                except ValueError:
                    continue
                return value, None
        return None, "回复中没有可解析的 JSON"
    return None, "回复中没有 JSON 代码块"


class JsonSchemaConfig(EvaluatorConfig):
    type: Literal["json_schema"]
    schema_: dict[str, Any] = Field(alias="schema")
    extract: Literal["auto", "codeblock"] = "auto"

    model_config = EvaluatorConfig.model_config | {"populate_by_name": True}  # type: ignore[assignment]

    @field_validator("schema_")
    @classmethod
    def _check_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            jsonschema.Draft202012Validator.check_schema(v)
        except jsonschema.SchemaError as e:
            raise ValueError(f"JSON Schema 不合法: {e.message}") from None
        return v


class JsonSchemaEvaluator(Evaluator):
    type: ClassVar[str] = "json_schema"

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: JsonSchemaConfig = self.config
        value, err = extract_json(transcript.final, cfg.extract)
        if err is not None:
            return ScoreResult.of(False, err)
        errors = sorted(
            jsonschema.Draft202012Validator(cfg.schema_).iter_errors(value), key=lambda e: list(e.path)
        )
        if not errors:
            return ScoreResult.of(True, "符合 JSON Schema")
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(根)"
        return ScoreResult.of(False, f"{where}: {first.message}"[:300], errors=len(errors))
