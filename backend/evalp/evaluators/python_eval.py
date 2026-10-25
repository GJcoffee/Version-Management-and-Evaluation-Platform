"""python 评估器：通过 toolhost 子进程执行用户函数（详设 8.5）。

``entry`` 为 ``module:function``，函数只有一个参数::

    def func(payload):   # payload = {"case": {...}, "transcript": {...}, "events": [...]}
        return {"score": 0.8, "passed": True, "reason": "…"}

``score`` 取值 [0,1]；缺省 ``passed`` 时按 ``score >= 0.5`` 判定；缺省 ``score`` 时按 ``passed`` 记 1 / 0；
返回布尔值等价于 ``{"passed": 值}``。函数在独立进程中执行（有超时），拥有本机权限。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator

from evalp.evaluators.base import CaseView, Evaluator, EvaluatorConfig, ScoreResult, Transcript
from evalp.runtime.events import AgentEvent
from evalp.runtime.tools.python_tool import run_toolhost

STDERR_TAIL = 2000


class PythonConfig(EvaluatorConfig):
    type: Literal["python"]
    entry: str
    timeout_s: float = Field(30.0, gt=0, le=3600)
    python: str | None = None  # 指定解释器；缺省使用平台自己的
    workdir: str | None = None  # 加入 sys.path 的目录

    @field_validator("entry")
    @classmethod
    def _entry(cls, v: str) -> str:
        mod, sep, fn = v.partition(":")
        if not sep or not mod.strip() or not fn.strip():
            raise ValueError("entry 必须是 'module:function' 格式")
        return v.strip()


def coerce_result(value: Any) -> ScoreResult:
    """把函数返回值转换为 ScoreResult。"""
    if isinstance(value, bool):
        value = {"passed": value}
    if not isinstance(value, dict):
        got = type(value).__name__
        return ScoreResult.error(f"函数必须返回 {{score, passed, reason}} 字典，实际是 {got}")
    score, passed, reason = value.get("score"), value.get("passed"), value.get("reason")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, int | float):
            return ScoreResult.error(f"score 必须是数字: {score!r}")
        if not 0.0 <= float(score) <= 1.0:
            return ScoreResult.error(f"score 必须在 [0,1] 内: {score!r}")
        score = float(score)
    if passed is not None and not isinstance(passed, bool):
        return ScoreResult.error(f"passed 必须是布尔值: {passed!r}")
    if score is None and passed is None:
        return ScoreResult.error("返回值里至少要有 score 或 passed")
    if score is None:
        score = 1.0 if passed else 0.0
    if passed is None:
        passed = score >= 0.5
    return ScoreResult(
        status="done",
        score=score,
        raw_score=score,
        passed=passed,
        reason=None if reason is None else str(reason),
    )


class PythonEvaluator(Evaluator):
    type: ClassVar[str] = "python"
    needs_events: ClassVar[bool] = True

    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        cfg: PythonConfig = self.config
        payload = {
            "case": case.to_dict(),
            "transcript": transcript.to_dict(),
            "events": [e.to_wire() for e in events],
        }
        resp, stderr, failure = await run_toolhost(
            cfg.entry, payload, python=cfg.python, workdir=cfg.workdir, timeout=cfg.timeout_s
        )
        tail = stderr[-STDERR_TAIL:] if stderr else None
        if failure == "timeout":
            return ScoreResult.error(f"Python 评估器超时（{cfg.timeout_s:g}s），进程已终止")
        if resp is None:
            return ScoreResult.error(f"Python 评估器执行失败（{failure}）", stderr=tail)
        if not resp.get("ok"):
            err = resp.get("error") or {}
            return ScoreResult.error(
                f"{err.get('type', 'Error')}: {err.get('message', '')}"[:300], traceback=err.get("traceback")
            )
        result = coerce_result(resp.get("result"))
        if tail:
            result.detail = {**(result.detail or {}), "stderr": tail}
        return result
