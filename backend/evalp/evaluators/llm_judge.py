"""llm_judge 评估器：用另一个模型按评审 prompt 打分（详设 8.5）。

流程（每次评审调用）：把评审配置发布为隐藏 agent（``evalp-judge``）的配置型版本（相同配置复用同一个版本），
新建 ``Session(mode=judge)``，用普通的 ``run_turn`` 执行，因此评审的 trace 可以回看（``judge_session_id``）。

- 评审 prompt 变量：``input``、``output``、``expected``、``rubric``、``transcript``（工具调用摘要）、
  ``scale_min``、``scale_max``；要求模型以 JSON 输出 ``{"score": 数字, "reason":
  "…"}``（``response_format=json_object``）；
- 输出解析失败（不是 JSON、缺少 score、分数超出量纲）时重试 1 次（再新建一个评审会话）；
  仍失败则这次评分记为 ``error``
  （``detail.parse_failed=true``），不计入通过判定，但计入错误率；
- ``samples > 1`` 时调用多次，取成功样本的平均分；全部失败才记为 error；
- 评审模型暂时不可用（429、超时等可重试错误）时抛 ``Retriable``，由调度器稍后重试整个评分任务；
  不可重试的模型错误记为 error。
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal

from pydantic import Field, ValidationInfo, field_validator

from evalp.evaluators.base import (
    CaseView,
    Evaluator,
    EvaluatorConfig,
    ScoreResult,
    Transcript,
    stringify,
)
from evalp.evaluators.human import ScaleMixin, check_scale
from evalp.evaluators.rules import extract_json
from evalp.runtime.events import AgentEvent
from evalp.runtime.llm.ratelimit import PRIORITY_EVAL
from evalp.runtime.scheduler import Retriable
from evalp.services.judge import DEFAULT_PROMPT_REF, JUDGE_USER_MESSAGE, JudgeService
from evalp.services.session import SessionService

PARSE_ATTEMPTS = 2  # 首次 + 重试 1 次
OUTPUT_PREVIEW = 300
REQUEST_PREVIEW = 200
TRANSCRIPT_LIMIT = 4000
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


class LLMJudgeConfig(ScaleMixin, EvaluatorConfig):
    type: Literal["llm_judge"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = DEFAULT_PROMPT_REF  # 资产引用；创建评测时会固化为具体修订
    scale: list[float] = Field(default_factory=lambda: [1, 5])
    pass_threshold: float | None = None
    samples: int = Field(1, ge=1, le=10)
    params: dict[str, Any] = Field(default_factory=dict)  # 额外的模型参数（temperature 缺省为 0）

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        if not v.startswith("asset://prompt/"):
            raise ValueError("prompt 必须是 asset://prompt/<名称>@<修订|latest> 引用")
        return v

    @field_validator("scale")
    @classmethod
    def _scale(cls, v: list[float]) -> list[float]:
        check_scale(v, None)
        return v

    @field_validator("pass_threshold")
    @classmethod
    def _threshold(cls, v: float | None, info: ValidationInfo) -> float | None:
        scale = info.data.get("scale")
        if v is not None and scale is not None:
            check_scale(scale, v)
        return v


class JudgeParseError(ValueError):
    """评审输出无法解析。"""


def parse_judge_output(text: str, lo: float, hi: float) -> tuple[float, str]:
    """解析评审输出 ``{"score": 数字, "reason": "…"}``，返回 ``(原始分, 理由)``；失败抛 JudgeParseError。"""
    value, err = extract_json(text or "", "auto")
    if err is not None:
        raise JudgeParseError(f"输出不是合法的 JSON: {err}")
    if not isinstance(value, dict) or "score" not in value:
        raise JudgeParseError('JSON 里缺少 "score" 字段')
    raw = value["score"]
    if isinstance(raw, str) and _NUM_RE.match(raw.strip()):
        raw = float(raw)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise JudgeParseError(f'"score" 必须是数字: {raw!r}')
    raw = float(raw)
    if not lo <= raw <= hi:
        raise JudgeParseError(f'"score"={raw:g} 超出评分范围 [{lo:g}, {hi:g}]')
    return raw, str(value.get("reason", "") or "")


def transcript_summary(transcript: Transcript, events: list[AgentEvent]) -> str:
    """工具调用摘要（给评审看）：优先用事件，没有事件时用消息。"""
    lines: list[str] = []
    results = {e.payload.get("call_id"): e for e in events if e.type == "tool.result"}
    for e in events:
        if e.type != "tool.call":
            continue
        res = results.get(e.payload.get("call_id"))
        out = "" if res is None else stringify(res.payload.get("output"))
        args = json.dumps(e.payload.get("arguments"), ensure_ascii=False)
        lines.append(f"- {e.payload.get('name')}({args}) -> {out[:300]}")
    if not lines:
        for c in transcript.tool_calls():
            args = json.dumps(c["arguments"], ensure_ascii=False)
            lines.append(f"- {c['name']}({args}) -> {stringify(c['output'])[:300]}")
    text = "\n".join(lines)
    return text if len(text) <= TRANSCRIPT_LIMIT else text[:TRANSCRIPT_LIMIT] + "\n…（已截断）"


class LLMJudgeEvaluator(Evaluator):
    type: ClassVar[str] = "llm_judge"
    needs_events: ClassVar[bool] = True

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: LLMJudgeConfig = self.config
        assert self.app is not None, "llm_judge 需要 AppContext"
        judge = self.app.svc(JudgeService)
        prompt_ref, _ = await judge.prompt_text(cfg.prompt)
        version_id = await judge.version_for(
            provider=cfg.provider, model=cfg.model, prompt_ref=prompt_ref, params=cfg.params
        )
        variables = {
            "input": transcript.input_text,
            "output": transcript.final,
            "expected": stringify(case.expected),
            "rubric": stringify(case.rubric),
            "transcript": transcript_summary(transcript, events),
            "scale_min": f"{cfg.lo:g}",
            "scale_max": f"{cfg.hi:g}",
        }
        samples: list[dict[str, Any]] = []
        raws: list[float] = []
        sessions: list[str] = []
        for i in range(cfg.samples):
            sample = await self._sample(version_id, case, variables, cfg, i)
            samples.append(sample)
            sessions.extend(sample["sessions"])
            if sample.get("raw") is not None:
                raws.append(sample["raw"])
        detail: dict[str, Any] = {
            "samples": samples,
            "judge_sessions": sessions,
            "scale": cfg.scale,
            "pass_threshold": cfg.threshold,
            "parse_failed": any(s.get("parse_failed") for s in samples),
        }
        first_session = sessions[0] if sessions else None
        if not raws:
            last = samples[-1]
            return ScoreResult(
                status="error",
                reason=last.get("error") or "评审没有产出有效评分",
                detail=detail,
                judge_session_id=first_session,
            )
        raw = sum(raws) / len(raws)
        reasons = [s["reason"] for s in samples if s.get("raw") is not None and s.get("reason")]
        return ScoreResult(
            status="done",
            score=cfg.normalize(raw),
            raw_score=raw,
            passed=raw >= cfg.threshold,
            reason=reasons[0] if len(reasons) == 1 else " | ".join(reasons)[:500] or None,
            detail=detail,
            judge_session_id=first_session,
        )

    async def _sample(
        self, version_id: str, case: CaseView, variables: dict[str, str], cfg: LLMJudgeConfig, idx: int
    ) -> dict[str, Any]:
        """一次评审样本：解析失败时重试 1 次（每次调用一个新的 judge 会话）。"""
        assert self.app is not None
        sessions_svc = self.app.svc(SessionService)
        out: dict[str, Any] = {"sessions": [], "attempts": 0}
        for attempt in range(PARSE_ATTEMPTS):
            sess = await sessions_svc.create(
                version_id=version_id,
                mode="judge",
                title=f"judge:{self.key}:{case.case_key}",
                variables=variables,
            )
            out["sessions"].append(sess.id)
            out["attempts"] = attempt + 1
            turn = await sessions_svc.run_turn(sess.id, JUDGE_USER_MESSAGE, priority=PRIORITY_EVAL, wait=True)
            if turn.status != "ok":
                err = turn.error
                msg = f"评审调用失败: {err.code}: {err.message}" if err else f"评审调用失败（{turn.status}）"
                if err is not None and err.retriable:
                    raise Retriable(msg, code=err.code)
                out["error"] = msg
                return out
            text = turn.output.final.text() if turn.output and turn.output.final else ""
            out["request_preview"] = {k: v[:REQUEST_PREVIEW] for k, v in variables.items() if v}
            out["reply_preview"] = text[:OUTPUT_PREVIEW]
            try:
                raw, reason = parse_judge_output(text, cfg.lo, cfg.hi)
            except JudgeParseError as e:
                out["error"] = f"评审输出无法解析: {e}"
                out["parse_failed"] = True
                out["parse_retried"] = attempt + 1 < PARSE_ATTEMPTS
                out["output_preview"] = text[:OUTPUT_PREVIEW]
                continue
            out.pop("error", None)
            out.pop("parse_failed", None)
            out.pop("output_preview", None)
            out.update(raw=raw, reason=reason)
            return out
        return out
