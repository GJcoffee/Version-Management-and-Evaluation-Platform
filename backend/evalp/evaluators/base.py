"""评估器的公共类型（详设 8.5）。

统一接口::

    class Evaluator:
        async def evaluate(
            self, case: CaseView, transcript: Transcript, events: list[AgentEvent]
        ) -> ScoreResult

所有评估器的输出都归一化为 ``score ∈ [0,1]``、``passed``、``reason``。评估器不应抛出异常：配置合法但
当次无法评估（例如用例缺少 expected、评审输出无法解析）时返回 ``status="error"`` 的结果，这样它会被
计入错误率、但不参与通过判定。唯一的例外是 :class:`evalp.runtime.scheduler.Retriable`：评审模型暂时不可用时，
评估器抛出它，让调度器稍后重试整个评分任务。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from evalp.runtime.events import AgentEvent

if TYPE_CHECKING:
    from evalp.context import AppContext


class ScoreResult(BaseModel):
    """一个评估器对一个 Item 的评分结果。"""

    status: Literal["done", "error", "pending_human"] = "done"
    score: float | None = None  # 归一化到 [0,1]
    raw_score: float | None = None  # 评估器自己的量纲（如 1–5 分）
    passed: bool | None = None
    reason: str | None = None
    detail: dict[str, Any] | None = None
    judge_session_id: str | None = None

    @classmethod
    def of(cls, passed: bool, reason: str, *, score: float | None = None, **detail: Any) -> ScoreResult:
        """二值评估器的便捷构造：通过得 1 分，否则得 ``score``（默认 0 分）。"""
        s = 1.0 if passed else (0.0 if score is None else score)
        return cls(status="done", score=s, raw_score=s, passed=passed, reason=reason, detail=detail or None)

    @classmethod
    def error(cls, reason: str, **detail: Any) -> ScoreResult:
        """无法评估：不计入通过判定，但计入错误率。"""
        return cls(status="error", reason=reason, detail=detail or None)

    @classmethod
    def pending_human(cls, **detail: Any) -> ScoreResult:
        """等待人工打分。"""
        return cls(status="pending_human", detail=detail or None)


@dataclass(frozen=True)
class CaseView:
    """评估器看到的用例（不含数据库细节）。"""

    case_key: str
    input: dict[str, Any]  # {"turns": [...]}
    expected: Any = None
    rubric: Any = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def turns(self) -> list[str]:
        turns = self.input.get("turns", [])
        return [t if isinstance(t, str) else json.dumps(t, ensure_ascii=False) for t in turns]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_key": self.case_key,
            "input": self.input,
            "expected": self.expected,
            "rubric": self.rubric,
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class TurnView:
    """一轮对话的摘要。"""

    idx: int
    input: str
    output: str
    status: str = "ok"
    messages: list[dict[str, Any]] = field(default_factory=list)  # 本轮新增的 assistant / tool 消息
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    cost: float = 0.0
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "messages": self.messages,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
            "error": self.error,
        }


@dataclass
class Transcript:
    """一个 Item 的完整对话（可能有多轮）。"""

    turns: list[TurnView] = field(default_factory=list)

    @property
    def final(self) -> str:
        """最后一轮的最终回复文本。"""
        return self.turns[-1].output if self.turns else ""

    @property
    def input_text(self) -> str:
        """用户输入：单轮为原文，多轮用换行拼接。"""
        return "\n".join(t.input for t in self.turns)

    @property
    def latency_ms(self) -> int:
        return sum(t.latency_ms or 0 for t in self.turns)

    @property
    def tokens(self) -> int:
        return sum(
            int(t.usage.get("prompt_tokens", 0) or 0) + int(t.usage.get("completion_tokens", 0) or 0)
            for t in self.turns
        )

    def tool_calls(self) -> list[dict[str, Any]]:
        """从消息中还原工具调用：``[{name, arguments, output}]``（按调用顺序）。"""
        calls: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        for t in self.turns:
            for m in t.messages:
                for c in m.get("tool_calls") or []:
                    item = {"name": c.get("name"), "arguments": c.get("arguments"), "output": None}
                    calls.append(item)
                    by_id[c.get("id")] = item
                if m.get("role") == "tool" and m.get("tool_call_id") in by_id:
                    by_id[m["tool_call_id"]]["output"] = m.get("content")
        return calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "final": self.final,
            "input": self.input_text,
            "latency_ms": self.latency_ms,
            "tokens": self.tokens,
        }


class EvaluatorConfig(BaseModel):
    """评估器配置的公共字段。具体类型在子类里用 ``type: Literal[...]`` 区分。"""

    model_config = ConfigDict(extra="forbid")

    key: str | None = None  # 同一次评测内唯一；缺省时按类型自动生成
    gate: bool | None = None  # 是否参与 Item 的通过判定；缺省：human 为 false，其他为 true

    default_gate: ClassVar[bool] = True

    @property
    def gated(self) -> bool:
        return self.default_gate if self.gate is None else self.gate


class Evaluator(ABC):
    """评估器基类。"""

    type: ClassVar[str]
    needs_events: ClassVar[bool] = False  # 为 True 时评分前会加载会话事件

    def __init__(self, key: str, config: Any, app: AppContext | None = None) -> None:
        self.key = key
        self.config = config
        self.app = app

    @abstractmethod
    async def evaluate(self, case: CaseView, transcript: Transcript, events: list[AgentEvent]) -> ScoreResult:
        """评估一个 Item。"""


def stringify(value: Any) -> str:
    """期望值 / 评分标准等转成文本（非字符串用 JSON）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
