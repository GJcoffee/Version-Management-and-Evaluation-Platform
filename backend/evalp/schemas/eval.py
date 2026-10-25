"""评测接口的请求 / 响应 DTO（详设 8、11.3）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evalp.schemas.asset import CaseFilter

RunStatus = Literal["queued", "running", "paused", "completed", "completed_with_errors", "cancelled"]
ItemStatus = Literal["pending", "running", "scoring", "done", "failed", "cancelled"]
TERMINAL_RUN_STATUSES = ("completed", "completed_with_errors", "cancelled")


class Budget(BaseModel):
    """预算：``max_cost``（元）与 ``max_tokens`` 都是"本次评测新增消耗"的上限（缓存命中的不计）。"""

    model_config = ConfigDict(extra="forbid")

    max_cost: float | None = Field(None, ge=0)
    max_tokens: int | None = Field(None, ge=0)


class EvalCreate(BaseModel):
    """``POST /evals``。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    version_ids: list[str] = Field(min_length=1, description="版本 id 或 agent:v12 / agent:标签 引用")
    dataset: str = Field(description="asset://dataset/x@latest|rev，或数据集修订 id（arv_…）")
    case_filter: CaseFilter = Field(default_factory=CaseFilter)
    evaluators: list[dict[str, Any]] = Field(description="评估器配置数组，见详设 8.5")
    repeats: int = Field(3, ge=1, le=50)
    concurrency: int | None = Field(None, ge=1, le=512, description="每个 Run 的并发上限；缺省取设置")
    seed: int | None = None
    cache_policy: Literal["reuse", "refresh"] = "reuse"
    budget: Budget = Field(default_factory=Budget)
    dry_run: bool = False
    via: Literal["ui", "cli", "mcp", "api"] = "api"


class RunRef(BaseModel):
    id: str
    version_id: str


class EvalCreated(BaseModel):
    """创建评测的响应；``dry_run`` 时只有 ``estimate``。"""

    dry_run: bool = False
    batch_id: str | None = None
    runs: list[RunRef] = Field(default_factory=list)
    estimate: dict[str, Any]


class DatasetBrief(BaseModel):
    revision_id: str
    asset_id: str | None = None
    name: str | None = None
    rev: int | None = None


class EvalRunOut(BaseModel):
    id: str
    batch_id: str
    batch_name: str | None = None
    agent_id: str
    agent_name: str | None = None
    version_id: str
    version_seq: int | None = None
    dataset: DatasetBrief
    case_filter: dict[str, Any]
    evaluators: list[dict[str, Any]]
    evaluators_hash: str
    repeats: int
    concurrency: int
    seed: int | None = None
    cache_policy: str
    budget: dict[str, Any]
    status: RunStatus
    pause_reason: str | None = None
    progress: dict[str, Any]
    stats: dict[str, Any] | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class EvalDetail(EvalRunOut):
    """``GET /evals/{id}``：在列表字段之外带同批次的其他 Run。"""

    batch_runs: list[dict[str, Any]] = Field(default_factory=list)


class ScoreOut(BaseModel):
    evaluator_key: str
    evaluator_type: str | None = None
    gate: bool | None = None
    status: Literal["done", "error", "pending_human"]
    score: float | None = None
    raw_score: float | None = None
    passed: bool | None = None
    reason: str | None = None
    detail: Any = None
    judge_session_id: str | None = None
    annotator: str | None = None
    stale: bool = False  # 评估器配置已变化、尚未重新评分


class CaseBrief(BaseModel):
    id: str
    case_key: str
    idx: int
    tags: list[str]
    input: dict[str, Any]


class ItemOut(BaseModel):
    id: str
    run_id: str
    case: CaseBrief
    repeat_idx: int
    status: ItemStatus
    passed: bool | None = None
    score: float | None = None
    cached: bool = False
    reused_from_item_id: str | None = None
    attempts: int = 0
    error: dict[str, Any] | str | None = None
    latency_ms: int | None = None
    cost: float | None = None
    usage: dict[str, Any] | None = None
    session_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    scores: list[ScoreOut] = Field(default_factory=list)


class ItemDetail(ItemOut):
    """``GET /evals/{id}/items/{item_id}``：带完整用例、各轮输出与评估器分数。"""

    case_detail: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    turns: list[dict[str, Any]] = Field(default_factory=list)


class HumanScoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    evaluator_key: str
    raw_score: float
    reason: str | None = None
    annotator: str | None = None


class RescoreIn(BaseModel):
    """``POST /evals/{id}/rescore``。

    ``evaluators``：新的评估器配置（省略则沿用现有配置，只重算出错 / 过期的评分）；
    ``dataset``：把评测"换绑"到数据集的另一个修订（``asset://dataset/x@latest`` 或修订 id），
    要求每个用例的输入没变
    （``case_hash`` 相同，即只改了 expected / rubric / tags）；换绑后全部评分按新的 expected / rubric 重算，
    不会调用被测 agent。
    """

    model_config = ConfigDict(extra="forbid")

    evaluators: list[dict[str, Any]] | None = None
    dataset: str | None = None


class ResumeIn(BaseModel):
    """``POST /evals/{id}/resume``：因预算暂停的评测可在继续时调高预算。"""

    model_config = ConfigDict(extra="forbid")

    budget: Budget | None = None
