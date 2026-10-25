"""human 评估器：生成 ``pending_human`` 状态的评分，等人工在 UI 中打分（详设 8.5）。

人工打分提交（``POST /evals/{id}/human-scores``）后，评分变为 ``done``，才计入通过判定与汇总。
``scale`` 是打分量纲（默认 1–5）；``pass_threshold`` 是通过线（量纲内的原始分，缺省为量纲的 60% 位置）。
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, ValidationInfo, field_validator

from evalp.evaluators.base import CaseView, Evaluator, EvaluatorConfig, ScoreResult, Transcript
from evalp.runtime.events import AgentEvent


class ScaleMixin:
    """量纲相关的公共计算（human 与 llm_judge 共用）。"""

    scale: list[float]
    pass_threshold: float | None

    @property
    def lo(self) -> float:
        return self.scale[0]

    @property
    def hi(self) -> float:
        return self.scale[1]

    @property
    def threshold(self) -> float:
        """通过线（原始分）。"""
        if self.pass_threshold is not None:
            return self.pass_threshold
        return self.lo + 0.6 * (self.hi - self.lo)

    def normalize(self, raw: float) -> float:
        return (raw - self.lo) / (self.hi - self.lo)


def check_scale(scale: list[float], threshold: float | None) -> None:
    if len(scale) != 2 or not scale[0] < scale[1]:
        raise ValueError("scale 必须是 [最小值, 最大值]，且最小值小于最大值")
    if threshold is not None and not scale[0] <= threshold <= scale[1]:
        raise ValueError("pass_threshold 必须落在 scale 范围内")


class HumanConfig(ScaleMixin, EvaluatorConfig):
    type: Literal["human"]
    scale: list[float] = Field(default_factory=lambda: [1, 5])
    guideline: str | None = None
    pass_threshold: float | None = None

    default_gate: ClassVar[bool] = False

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


class HumanEvaluator(Evaluator):
    type: ClassVar[str] = "human"

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: HumanConfig = self.config
        return ScoreResult.pending_human(
            scale=cfg.scale, guideline=cfg.guideline, pass_threshold=cfg.threshold
        )
