"""trace 评估器：基于事件流断言 agent 的行为（详设 8.5）。

``assert`` 支持：

- ``tool_called``：这些工具（名称或名称数组）都必须被调用过；
- ``tool_not_called``：这些工具一个都不能被调用；
- ``max_steps``：LLM 调用次数（``llm.response`` 事件数）上限；代码型 agent 没有 LLM 事件时，用
  ``tool.call`` 数 + 1 近似；
- ``max_latency_ms``：各轮耗时之和上限；
- ``max_tokens``：各轮 prompt + completion token 之和上限。

得分 = 通过的断言数 / 断言总数，全部通过才算 passed。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalp.evaluators.base import CaseView, Evaluator, EvaluatorConfig, ScoreResult, Transcript
from evalp.runtime.events import AgentEvent


class TraceAssert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_called: list[str] = Field(default_factory=list)
    tool_not_called: list[str] = Field(default_factory=list)
    max_steps: int | None = Field(None, ge=0)
    max_latency_ms: int | None = Field(None, ge=0)
    max_tokens: int | None = Field(None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _single_name(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            for k in ("tool_called", "tool_not_called"):
                if isinstance(data.get(k), str):
                    data[k] = [data[k]]
        return data

    @model_validator(mode="after")
    def _non_empty(self) -> TraceAssert:
        if not (
            self.tool_called
            or self.tool_not_called
            or self.max_steps is not None
            or self.max_latency_ms is not None
            or self.max_tokens is not None
        ):
            raise ValueError("assert 至少需要一项断言")
        return self


class TraceConfig(EvaluatorConfig):
    type: Literal["trace"]
    assert_: TraceAssert = Field(alias="assert")

    model_config = EvaluatorConfig.model_config | {"populate_by_name": True}  # type: ignore[assignment]


class TraceEvaluator(Evaluator):
    type: ClassVar[str] = "trace"
    needs_events: ClassVar[bool] = True

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        a: TraceAssert = self.config.assert_
        called = [e.payload.get("name") for e in events if e.type == "tool.call"]
        if not called:  # 没有事件时退回到消息里的工具调用
            called = [c["name"] for c in transcript.tool_calls()]
        llm_calls = sum(1 for e in events if e.type == "llm.response")
        steps = llm_calls if llm_calls else len(called) + 1
        checks: list[tuple[bool, str]] = []
        for name in a.tool_called:
            ok = name in called
            checks.append((ok, f"调用了 {name}" if ok else f"没有调用 {name}"))
        for name in a.tool_not_called:
            ok = name not in called
            checks.append((ok, f"没有调用 {name}" if ok else f"不该调用 {name} 但调用了"))
        if a.max_steps is not None:
            checks.append((steps <= a.max_steps, f"步数 {steps}（上限 {a.max_steps}）"))
        if a.max_latency_ms is not None:
            lat = transcript.latency_ms
            checks.append((lat <= a.max_latency_ms, f"耗时 {lat}ms（上限 {a.max_latency_ms}ms）"))
        if a.max_tokens is not None:
            tok = transcript.tokens
            checks.append((tok <= a.max_tokens, f"token {tok}（上限 {a.max_tokens}）"))
        passed = all(ok for ok, _ in checks)
        failed = [msg for ok, msg in checks if not ok]
        score = sum(1 for ok, _ in checks if ok) / len(checks)
        reason = "全部断言通过" if passed else "；".join(failed)
        return ScoreResult(
            status="done",
            score=score,
            raw_score=score,
            passed=passed,
            reason=reason,
            detail={
                "checks": [{"ok": ok, "message": msg} for ok, msg in checks],
                "tools_called": called,
                "steps": steps,
            },
        )
