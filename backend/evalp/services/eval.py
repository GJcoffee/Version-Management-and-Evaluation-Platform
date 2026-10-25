"""评测服务（详设 8）：创建、状态机、执行单元、缓存、预算、评分、人工打分、对比与门禁。

给调度器 / 重放 / CLI / MCP 的要点
==================================

- 构造 ``EvalService`` 时会向调度器注册 ``eval_item``（执行并评分）与 ``rescore``（仅重新评分）两类任务；
  启动调度器请用 :func:`start_eval_runtime`（注册 + 内置评审 prompt + ``scheduler.start()``）。
- 一个 Item 对应一个 job：``jobs.ref_id = eval_items.id``、``jobs.group_id = eval_runs.id``，并发上限取
  ``eval_runs.concurrency``，
  Run 处于 ``paused`` / ``cancelled`` 时不会被领取。
- 执行 = ``SessionService.create(mode="eval")`` + 依次 ``run_turn(wait=True, priority=PRIORITY_EVAL)``；
  agent 失败不抛异常，
  看 ``TurnOut.status`` / ``error.retriable``：可重试的错误抛 ``Retriable`` 交给调度器按指数退避重试，
  用尽后 Item 记为 ``failed``。
- **计费规则**：``eval_items.cost`` 只记最终成功的那次执行；重试中失败的尝试记在
  ``usage.failed_attempts``（含各自的成本），
  累计到 ``usage.wasted_cost``，**不计入预算**，只在汇总里体现为 ``cost_wasted``。预算按"本 Run 新增成本"
  （``cost_new``：非缓存命中、已成功执行的 Item 的成本之和）实时从库里累加，因此重启 / 重试不会重复计费。
- 缓存命中的 Item 沿用来源 Item 的会话、用量和成本（名义成本，计入 ``stats.cost`` 但不计入 ``cost_new``
  与预算）。
- 公开方法（均可被 CLI / MCP 直接调用）：``create`` / ``get`` / ``list`` / ``pause`` / ``resume`` /
  ``cancel`` /
  ``retry_failed`` / ``rescore`` / ``list_items`` / ``get_item`` / ``human_scores`` / ``compare`` / ``gate`` /
  ``wait_finished`` / ``stream``。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Text, and_, case, cast, delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.compare.stats import (
    DEFAULT_THRESHOLD,
    classify_cases,
    mean,
    summarize_deltas,
)
from evalp.context import AppContext
from evalp.errors import EvalpError, InvalidState, NotFound, SpecInvalid, ValidationFailed
from evalp.evaluators import (
    build_evaluators,
    config_to_dict,
    evaluator_hash,
    evaluators_hash,
    resolve_evaluators,
)
from evalp.evaluators.base import CaseView, ScoreResult, Transcript, TurnView
from evalp.ids import _ulid_str, new_id
from evalp.logging import get_logger
from evalp.runtime.llm.pricing import compute_cost, find_model
from evalp.runtime.llm.ratelimit import PRIORITY_EVAL
from evalp.runtime.llm.tokens import count_tokens
from evalp.runtime.scheduler import GroupInfo, Job, Retriable, Scheduler
from evalp.schemas.eval import (
    TERMINAL_RUN_STATUSES,
    Budget,
    CaseBrief,
    DatasetBrief,
    EvalCreate,
    EvalCreated,
    EvalDetail,
    EvalRunOut,
    HumanScoreIn,
    ItemDetail,
    ItemOut,
    RescoreIn,
    RunRef,
    ScoreOut,
)
from evalp.services.agent import AgentService
from evalp.services.dataset import DatasetService
from evalp.services.eval_stats import (
    ItemRow,
    ScoreRow,
    case_scores,
    compute_passed,
    compute_stats,
)
from evalp.services.judge import JudgeService
from evalp.services.provider import ProviderService
from evalp.services.session import SessionService
from evalp.services.version import ResolvedVersion, VersionService
from evalp.spec.models import ConfigSpec
from evalp.store.models import (
    Agent,
    Asset,
    AssetRevision,
    EvalBatch,
    EvalCase,
    EvalItem,
    EvalRun,
    EvalScore,
    Version,
)
from evalp.store.models import Job as JobRow
from evalp.utils.canonical import sha256_hex
from evalp.utils.time import now_iso

log = get_logger(__name__)

KIND_ITEM = "eval_item"
KIND_RESCORE = "rescore"
ACTIVE_ITEM = ("pending", "running", "scoring")
PROGRESS_INTERVAL = 0.25  # eval.progress 最多每秒 4 条
CACHE_CHUNK = 400
HISTORY_LIMIT = 2000
DEFAULT_MAX_TOKENS = 4096
CLASS_ORDER = {"regressed": 0, "improved": 1, "unchanged": 2, "new": 3, "missing": 4, "unscored": 5}
ITEM_CLASSES = ("passed", "failed", "error", "flaky", "pending_human")


def eval_topic(run_id: str) -> str:
    """评测事件的 EventBus 主题。"""
    return f"eval:{run_id}"


def _score_id() -> str:
    return f"evs_{_ulid_str()}"


def cache_key_of(spec_hash: str, case_hash: str, repeat_idx: int, seed: int | None) -> str:
    """缓存键（详设 8.4）：``sha256(spec_hash|case_hash|repeat_idx|seed)``。"""
    return sha256_hex(f"{spec_hash}|{case_hash}|{repeat_idx}|{seed}")


def error_text(code: str, message: str, retriable: bool = False) -> str:
    return json.dumps({"code": code, "message": message, "retriable": retriable}, ensure_ascii=False)


def _parse_error(text: str | None) -> dict[str, Any] | str | None:
    if not text:
        return None
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else text
    except ValueError:
        return text


@dataclass
class EvalEvent:
    """推送给 ``eval:{run_id}`` 主题的事件：``eval.progress`` / ``eval.item_done`` / ``eval.status`` /
    ``eval.budget_exceeded``。"""

    event: str
    data: dict[str, Any]


@dataclass
class _Throttle:
    last: float = 0.0
    timer: asyncio.TimerHandle | None = None


# ---------------------------------------------------------------- 启动


async def start_eval_runtime(ctx: AppContext, *, seed_builtin: bool = True) -> Scheduler:
    """注册评测 handler、创建内置评审 prompt 并启动调度器（应用启动与测试共用）。

    ``seed_builtin=False`` 时不预先创建 ``prompt/judge-default``（首次有评估器引用它时才懒创建），
    供注入 ctx 的测试应用使用，避免影响对资产列表的断言。
    """
    ctx.svc(EvalService)
    if seed_builtin:
        await ctx.svc(JudgeService).ensure_default_prompt()
    sched = ctx.svc(Scheduler)
    await sched.start()
    return sched


class EvalService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._throttles: dict[str, _Throttle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._started_runs: set[str] = set()
        sched = ctx.svc(Scheduler)
        sched.register(
            KIND_ITEM,
            self._handle_item,
            group_loader=self._group_info,
            on_retry=self._on_retry,
            on_fail=self._on_fail,
            on_cancel=self._on_cancel,
            on_reset=self._on_reset,
        )
        sched.register(
            KIND_RESCORE,
            self._handle_rescore,
            group_loader=self._group_info,
            on_fail=self._rescore_revert,
            on_cancel=self._rescore_revert_job,
        )
        sched.add_startup_hook(self.reconcile)

    @property
    def scheduler(self) -> Scheduler:
        return self.ctx.svc(Scheduler)

    async def aclose(self) -> None:
        for th in self._throttles.values():
            if th.timer is not None:
                th.timer.cancel()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ================================================================ 创建

    async def create(self, body: EvalCreate, *, dry_run: bool | None = None) -> EvalCreated:
        """发起评测（详设 8.1）：每个版本一个 Run，同属一个批次；``dry_run=True`` 只返回预估。"""
        dry = body.dry_run if dry_run is None else dry_run
        versions = await self._resolve_versions(body.version_ids)
        rev_id, asset_id = await self._resolve_dataset(body.dataset)
        cases = await self.ctx.svc(DatasetService).list_cases(rev_id, body.case_filter)
        if not cases:
            raise ValidationFailed(
                "数据集中没有符合 case_filter 的用例",
                {"errors": [{"path": "case_filter", "message": "没有匹配的用例"}]},
            )
        configs = await resolve_evaluators(self.ctx, body.evaluators)
        evaluators = [config_to_dict(c) for c in configs]
        settings = await self._settings()
        concurrency = body.concurrency or settings["scheduler.default_run_concurrency"]
        budget = body.budget.model_dump(exclude_none=True)

        # 每个版本的缓存命中
        plans: list[dict[str, Any]] = []
        for rv in versions:
            keys = {
                (c.id, r): cache_key_of(rv.spec_hash, c.case_hash, r, body.seed)
                for c in cases
                for r in range(body.repeats)
            }
            sources = await self._cache_sources(list(keys.values())) if body.cache_policy == "reuse" else {}
            plans.append({"version": rv, "keys": keys, "sources": sources})
        estimate = await self._estimate(versions, cases, body, plans, asset_id, budget)
        if dry:
            return EvalCreated(dry_run=True, estimate=estimate)

        now = now_iso()
        batch_id = new_id("evb")
        runs: list[RunRef] = []
        job_pairs: list[tuple[str, str, str]] = []  # (run_id, kind, item_id)，按执行顺序
        async with self.ctx.db.write() as s:
            s.add(EvalBatch(id=batch_id, name=body.name, created_via=body.via, created_at=now))
            await s.flush()
            for plan in plans:
                rv: ResolvedVersion = plan["version"]
                run_id = new_id("evr")
                run = EvalRun(
                    id=run_id,
                    batch_id=batch_id,
                    agent_id=rv.agent_id,
                    version_id=rv.id,
                    dataset_rev_id=rev_id,
                    case_filter=body.case_filter.model_dump(),
                    evaluators=evaluators,
                    evaluators_hash=evaluators_hash(evaluators),
                    repeats=body.repeats,
                    concurrency=concurrency,
                    seed=body.seed,
                    cache_policy=body.cache_policy,
                    budget=budget,
                    status="queued",
                    progress={},
                    created_at=now,
                )
                s.add(run)
                await s.flush()
                rows: list[dict[str, Any]] = []
                by_repeat: dict[int, list[str]] = {r: [] for r in range(body.repeats)}
                for c in cases:
                    for r in range(body.repeats):
                        key = plan["keys"][(c.id, r)]
                        src = plan["sources"].get(key)
                        item_id = new_id("evi")
                        rows.append(
                            {
                                "id": item_id,
                                "run_id": run_id,
                                "case_id": c.id,
                                "repeat_idx": r,
                                "cache_key": key,
                                "status": "scoring" if src else "pending",
                                "session_id": src["session_id"] if src else None,
                                "reused_from_item_id": src["id"] if src else None,
                                "attempts": 0,
                                "latency_ms": src["latency_ms"] if src else None,
                                "usage": _clean_usage(src["usage"]) if src else None,
                                "cost": src["cost"] if src else None,
                            }
                        )
                        by_repeat[r].append(item_id)
                await s.execute(insert(EvalItem), rows)
                # 任务按"第 1 次重复的所有用例、第 2 次……"的顺序排队，中途暂停 / 预算耗尽时每个用例都已有结果
                for r in range(body.repeats):
                    for item_id in by_repeat[r]:
                        job_pairs.append((run_id, KIND_ITEM, item_id))
                runs.append(RunRef(id=run_id, version_id=rv.id))
            for run_id in [r.id for r in runs]:
                rows_j = Scheduler.job_rows(
                    [(k, i) for rid, k, i in job_pairs if rid == run_id], group_id=run_id, priority=0
                )
                await s.execute(insert(JobRow), rows_j)
        self.scheduler.wake()
        for r in runs:
            await self._publish_status(r.id)
        log.info("eval.created", batch_id=batch_id, runs=[r.id for r in runs], items=estimate["items"])
        return EvalCreated(batch_id=batch_id, runs=runs, estimate=estimate)

    async def _settings(self) -> dict[str, int]:
        from evalp.services.settings import SettingsService

        return await self.ctx.svc(SettingsService).get_all()

    async def _resolve_versions(self, refs: list[str]) -> list[ResolvedVersion]:
        versions = self.ctx.svc(VersionService)
        out: list[ResolvedVersion] = []
        for ref in refs:
            vid = await versions.resolve_ref(ref)
            if any(v.id == vid for v in out):
                raise ValidationFailed(
                    f"version_ids 中有重复的版本: {ref}",
                    {"errors": [{"path": "version_ids", "message": "版本重复"}]},
                )
            out.append(await versions.get_resolved(vid))
        return out

    async def _resolve_dataset(self, ref: str) -> tuple[str, str]:
        """数据集引用 -> (修订 id, 资产 id)。接受 asset://dataset/x@latest|rev 或修订 id（arv_…）。"""
        datasets = self.ctx.svc(DatasetService)
        if ref.startswith("arv_"):
            async with self.ctx.db.read() as s:
                row = (
                    await s.execute(
                        select(AssetRevision.id, AssetRevision.asset_id, Asset.kind)
                        .join(Asset, Asset.id == AssetRevision.asset_id)
                        .where(AssetRevision.id == ref)
                    )
                ).first()
            if row is None or row.kind != "dataset":
                raise NotFound(f"数据集修订不存在: {ref}", {"id": ref})
            return row.id, row.asset_id
        try:
            resolved = await datasets.resolve_dataset(ref)
        except SpecInvalid as e:
            raise NotFound(f"数据集不存在: {ref}（{e.message}）", {"ref": ref}) from None
        return resolved.revision_id, resolved.asset_id

    async def _cache_sources(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """按 cache_key 找可复用的已完成 Item（优先取原始 Item，而不是复用别人的）。"""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), CACHE_CHUNK):
            chunk = keys[i : i + CACHE_CHUNK]
            async with self.ctx.db.read() as s:
                rows = (
                    await s.execute(
                        select(
                            EvalItem.cache_key,
                            EvalItem.id,
                            EvalItem.session_id,
                            EvalItem.latency_ms,
                            EvalItem.usage,
                            EvalItem.cost,
                            EvalItem.reused_from_item_id,
                        )
                        .where(
                            EvalItem.cache_key.in_(chunk),
                            EvalItem.status == "done",
                            EvalItem.session_id.is_not(None),
                        )
                        .order_by(EvalItem.id)
                    )
                ).all()
            for r in rows:
                cur = out.get(r.cache_key)
                if cur is None or (cur["reused"] is not None and r.reused_from_item_id is None):
                    out[r.cache_key] = {
                        "id": r.id,
                        "session_id": r.session_id,
                        "latency_ms": r.latency_ms,
                        "usage": r.usage,
                        "cost": r.cost,
                        "reused": r.reused_from_item_id,
                    }
        return out

    # ---------------------------------------------------------------- 成本预估

    async def _estimate(
        self,
        versions: list[ResolvedVersion],
        cases: list[Any],
        body: EvalCreate,
        plans: list[dict[str, Any]],
        asset_id: str,
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        """成本预估（详设 8.1）：有历史评测时用历史的平均每条 token / 成本；否则 ``input×3 +
        max_tokens×0.5``。

        ``tokens`` / ``cost`` 是扣除缓存命中后预计新增的消耗；``gross_*`` 是不考虑缓存的总量。
        """
        n_cases = len(cases)
        input_tokens = [
            sum(count_tokens(t) for t in c.input.get("turns", []) if isinstance(t, str)) for c in cases
        ]
        avg_in = sum(input_tokens) / n_cases
        per_version: list[dict[str, Any]] = []
        cost_known = True
        for plan in plans:
            rv: ResolvedVersion = plan["version"]
            items = n_cases * body.repeats
            cached = sum(1 for k in plan["keys"].values() if k in plan["sources"])
            hist = await self._history_avg(rv.agent_id, asset_id)
            if hist is not None:
                tok_item, cost_item, basis, known = hist["tokens"], hist["cost"], "history", True
            else:
                prompt_est = avg_in * 3
                max_tokens, price = await self._model_price(rv)
                completion_est = max_tokens * 0.5
                tok_item = prompt_est + completion_est
                basis = "heuristic"
                c = compute_cost(price, int(prompt_est), int(completion_est)) if price is not None else None
                cost_item, known = (c if c is not None else 0.0), c is not None
            cost_known = cost_known and known
            new_items = items - cached
            per_version.append(
                {
                    "version_id": rv.id,
                    "items": items,
                    "cached_items": cached,
                    "tokens": round(tok_item * new_items),
                    "cost": round(cost_item * new_items, 6),
                    "gross_tokens": round(tok_item * items),
                    "gross_cost": round(cost_item * items, 6),
                    "basis": basis,
                }
            )
        bases = {p["basis"] for p in per_version}
        total_cost = round(sum(p["cost"] for p in per_version), 6)
        total_tokens = sum(p["tokens"] for p in per_version)
        exceeds = None
        if budget.get("max_cost") is not None:
            exceeds = total_cost > budget["max_cost"]
        if budget.get("max_tokens") is not None:
            exceeds = bool(exceeds) or total_tokens > budget["max_tokens"]
        return {
            "items": sum(p["items"] for p in per_version),
            "cached_items": sum(p["cached_items"] for p in per_version),
            "tokens": total_tokens,
            "cost": total_cost,
            "gross_tokens": sum(p["gross_tokens"] for p in per_version),
            "gross_cost": round(sum(p["gross_cost"] for p in per_version), 6),
            "cost_known": cost_known,
            "basis": bases.pop() if len(bases) == 1 else "mixed",
            "exceeds_budget": exceeds,
            "per_version": per_version,
        }

    async def _history_avg(self, agent_id: str, asset_id: str) -> dict[str, float] | None:
        async with self.ctx.db.read() as s:
            rows = (
                await s.execute(
                    select(EvalItem.usage, EvalItem.cost)
                    .join(EvalRun, EvalRun.id == EvalItem.run_id)
                    .join(AssetRevision, AssetRevision.id == EvalRun.dataset_rev_id)
                    .where(
                        EvalRun.agent_id == agent_id,
                        AssetRevision.asset_id == asset_id,
                        EvalItem.status == "done",
                        EvalItem.reused_from_item_id.is_(None),
                        EvalItem.usage.is_not(None),
                    )
                    .order_by(EvalItem.id.desc())
                    .limit(HISTORY_LIMIT)
                )
            ).all()
        if not rows:
            return None
        toks = [
            int((u or {}).get("prompt_tokens", 0) or 0) + int((u or {}).get("completion_tokens", 0) or 0)
            for u, _ in rows
        ]
        return {"tokens": sum(toks) / len(rows), "cost": sum(float(c or 0.0) for _, c in rows) / len(rows)}

    async def _model_price(self, rv: ResolvedVersion) -> tuple[int, Any | None]:
        """(max_tokens, 模型价格信息)；代码型 agent 没有模型信息，取默认值。"""
        try:
            spec = rv.spec_model()
        except Exception:  # noqa: BLE001
            return DEFAULT_MAX_TOKENS, None
        if not isinstance(spec, ConfigSpec):
            return 1024, None
        max_tokens = spec.model.params.max_tokens or DEFAULT_MAX_TOKENS
        try:
            provider = await self.ctx.svc(ProviderService).get_by_name(spec.model.provider)
        except EvalpError:
            return max_tokens, None
        return max_tokens, find_model(provider.models, spec.model.name)

    # ================================================================ 查询

    async def _run_row(self, s: AsyncSession, run_id: str) -> EvalRun:
        run = await s.get(EvalRun, run_id)
        if run is None:
            raise NotFound(f"评测不存在: {run_id}", {"id": run_id})
        return run

    async def _progress_map(self, s: AsyncSession, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        out = {
            rid: {
                "total": 0,
                "pending": 0,
                "running": 0,
                "scoring": 0,
                "done": 0,
                "failed": 0,
                "cancelled": 0,
                "cached": 0,
                "cost_new": 0.0,
            }
            for rid in run_ids
        }
        if not run_ids:
            return out
        rows = (
            await s.execute(
                select(
                    EvalItem.run_id,
                    EvalItem.status,
                    func.count(),
                    func.sum(case((EvalItem.reused_from_item_id.is_not(None), 1), else_=0)),
                )
                .where(EvalItem.run_id.in_(run_ids))
                .group_by(EvalItem.run_id, EvalItem.status)
            )
        ).all()
        for rid, status, n, cached in rows:
            p = out[rid]
            p["total"] += n
            p[status] = p.get(status, 0) + n
            p["cached"] += int(cached or 0)
        costs = (
            await s.execute(
                select(EvalItem.run_id, func.sum(EvalItem.cost))
                .where(
                    EvalItem.run_id.in_(run_ids),
                    EvalItem.reused_from_item_id.is_(None),
                    EvalItem.status.in_(("scoring", "done")),
                )
                .group_by(EvalItem.run_id)
            )
        ).all()
        for rid, c in costs:
            out[rid]["cost_new"] = round(float(c or 0.0), 8)
        return out

    async def _run_outs(self, s: AsyncSession, runs: list[EvalRun]) -> list[EvalRunOut]:
        if not runs:
            return []
        agents = {a.id: a.name for a in (await s.execute(select(Agent))).scalars()}
        vids = {r.version_id for r in runs}
        seqs = dict((await s.execute(select(Version.id, Version.seq).where(Version.id.in_(vids)))).all())
        revs = {
            r.id: (r.asset_id, r.rev, name)
            for r, name in (
                await s.execute(
                    select(AssetRevision, Asset.name)
                    .join(Asset, Asset.id == AssetRevision.asset_id)
                    .where(AssetRevision.id.in_({r.dataset_rev_id for r in runs}))
                )
            ).all()
        }
        batches = {
            b.id: b.name
            for b in (
                await s.execute(select(EvalBatch).where(EvalBatch.id.in_({r.batch_id for r in runs})))
            ).scalars()
        }
        progress = await self._progress_map(s, [r.id for r in runs])
        outs = []
        for r in runs:
            asset_id, rev, name = revs.get(r.dataset_rev_id, (None, None, None))
            stored = r.progress or {}
            prog = {**progress[r.id], "pause_reason": stored.get("pause_reason")}
            outs.append(
                EvalRunOut(
                    id=r.id,
                    batch_id=r.batch_id,
                    batch_name=batches.get(r.batch_id),
                    agent_id=r.agent_id,
                    agent_name=agents.get(r.agent_id),
                    version_id=r.version_id,
                    version_seq=seqs.get(r.version_id),
                    dataset=DatasetBrief(revision_id=r.dataset_rev_id, asset_id=asset_id, name=name, rev=rev),
                    case_filter=r.case_filter or {},
                    evaluators=r.evaluators or [],
                    evaluators_hash=r.evaluators_hash,
                    repeats=r.repeats,
                    concurrency=r.concurrency,
                    seed=r.seed,
                    cache_policy=r.cache_policy,
                    budget=r.budget or {},
                    status=r.status,  # type: ignore[arg-type]
                    pause_reason=stored.get("pause_reason") if r.status == "paused" else None,
                    progress=prog,
                    stats=r.stats,
                    error=r.error,
                    created_at=r.created_at,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
            )
        return outs

    async def get(self, run_id: str) -> EvalDetail:
        """评测详情（含同批次的其他 Run）。"""
        async with self.ctx.db.read() as s:
            run = await self._run_row(s, run_id)
            (out,) = await self._run_outs(s, [run])
            siblings = (
                (
                    await s.execute(
                        select(EvalRun).where(EvalRun.batch_id == run.batch_id).order_by(EvalRun.id)
                    )
                )
                .scalars()
                .all()
            )
            batch_runs = [{"id": r.id, "version_id": r.version_id, "status": r.status} for r in siblings]
        return EvalDetail(**out.model_dump(), batch_runs=batch_runs)

    async def list(
        self,
        *,
        agent: str | None = None,
        version_id: str | None = None,
        batch: str | None = None,
        status: str | list[str] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[EvalRunOut]:
        """评测列表（新的在前），可按 agent（id 或名称）、版本、批次、状态筛选。"""
        statuses = [x.strip() for x in status.split(",") if x.strip()] if isinstance(status, str) else status
        async with self.ctx.db.read() as s:
            stmt = select(EvalRun)
            if agent:
                a = await AgentService.find_orm(s, agent)
                stmt = stmt.where(EvalRun.agent_id == (a.id if a else agent))
            if version_id:
                stmt = stmt.where(EvalRun.version_id == version_id)
            if batch:
                stmt = stmt.where(EvalRun.batch_id == batch)
            if statuses:
                stmt = stmt.where(EvalRun.status.in_(statuses))
            page = await paginate(s, stmt, EvalRun.id, limit=limit, cursor=cursor)
            return Page(items=await self._run_outs(s, page.items), next_cursor=page.next_cursor)

    async def progress(self, run_id: str) -> dict[str, Any]:
        """实时进度（按 Item 状态统计）。"""
        async with self.ctx.db.read() as s:
            run = await self._run_row(s, run_id)
            p = (await self._progress_map(s, [run_id]))[run_id]
            p["pause_reason"] = (run.progress or {}).get("pause_reason") if run.status == "paused" else None
            return p

    async def wait_finished(self, run_id: str, timeout: float = 600.0, interval: float = 0.2) -> EvalDetail:
        """等待 Run 进入终态（completed / completed_with_errors / cancelled），供 CLI ``--wait`` 使用。
        超时抛 TimeoutError。"""
        deadline = time.monotonic() + timeout
        while True:
            detail = await self.get(run_id)
            if detail.status in TERMINAL_RUN_STATUSES:
                return detail
            if time.monotonic() > deadline:
                raise TimeoutError(f"评测 {run_id} 在 {timeout}s 内没有结束（{detail.status}）")
            await asyncio.sleep(interval)

    # ---------------------------------------------------------------- 取行数据

    async def _stat_rows(self, s: AsyncSession, run_id: str) -> tuple[list[ItemRow], list[ScoreRow]]:
        rows = (
            await s.execute(
                select(EvalItem, EvalCase.case_key, EvalCase.tags)
                .join(EvalCase, EvalCase.id == EvalItem.case_id)
                .where(EvalItem.run_id == run_id)
                .order_by(EvalItem.id)
            )
        ).all()
        items = [
            ItemRow(
                id=it.id,
                case_id=it.case_id,
                case_key=key,
                repeat_idx=it.repeat_idx,
                status=it.status,
                tags=list(tags or []),
                passed=it.passed,
                latency_ms=it.latency_ms,
                usage=it.usage,
                cost=it.cost,
                cached=it.reused_from_item_id is not None,
            )
            for it, key, tags in rows
        ]
        srows = (
            await s.execute(
                select(
                    EvalScore.item_id,
                    EvalScore.evaluator_key,
                    EvalScore.status,
                    EvalScore.score,
                    EvalScore.passed,
                    EvalScore.detail,
                )
                .join(EvalItem, EvalItem.id == EvalScore.item_id)
                .where(EvalItem.run_id == run_id)
            )
        ).all()
        scores = [ScoreRow(r[0], r[1], r[2], r[3], r[4], r[5]) for r in srows]
        return items, scores

    async def _compute_stats(self, s: AsyncSession, run: EvalRun) -> dict[str, Any]:
        items, scores = await self._stat_rows(s, run.id)
        return compute_stats(
            items, scores, evaluator_keys=[c["key"] for c in run.evaluators], repeats=run.repeats
        )

    # ================================================================ 事件

    def _publish(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        self.ctx.bus.publish(eval_topic(run_id), EvalEvent(event, {"run_id": run_id, **data}))

    async def _publish_status(self, run_id: str, *, with_stats: bool = False) -> None:
        if self.ctx.bus.subscriber_count(eval_topic(run_id)) == 0:
            return
        try:
            async with self.ctx.db.read() as s:
                run = await self._run_row(s, run_id)
                prog = (await self._progress_map(s, [run_id]))[run_id]
            data: dict[str, Any] = {
                "status": run.status,
                "pause_reason": (run.progress or {}).get("pause_reason") if run.status == "paused" else None,
                "progress": prog,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            if with_stats or run.status in TERMINAL_RUN_STATUSES:
                data["stats"] = run.stats
            self._publish(run_id, "eval.status", data)
        except Exception as e:  # noqa: BLE001
            log.warning("eval.publish_status_failed", run_id=run_id, error=str(e))

    def _notify_progress(self, run_id: str) -> None:
        """请求推送 eval.progress：每个 Run 最多每 0.25 秒一条（节流，保证最后一次变化一定会被推出）。"""
        if self.ctx.bus.subscriber_count(eval_topic(run_id)) == 0:
            return
        loop = asyncio.get_running_loop()
        th = self._throttles.setdefault(run_id, _Throttle())
        if th.timer is not None:
            return
        wait = th.last + PROGRESS_INTERVAL - loop.time()
        if wait <= 0:
            self._spawn_progress(run_id, th)
        else:
            th.timer = loop.call_later(wait, self._fire_progress, run_id, th)

    def _fire_progress(self, run_id: str, th: _Throttle) -> None:
        th.timer = None
        self._spawn_progress(run_id, th)

    def _spawn_progress(self, run_id: str, th: _Throttle) -> None:
        th.last = asyncio.get_running_loop().time()
        task = asyncio.create_task(self._emit_progress(run_id), name=f"eval-progress-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit_progress(self, run_id: str) -> None:
        try:
            p = await self.progress(run_id)
            self._publish(run_id, "eval.progress", {"progress": p})
        except Exception as e:  # noqa: BLE001
            log.warning("eval.publish_progress_failed", run_id=run_id, error=str(e))

    async def stream(self, run_id: str) -> AsyncIterator[EvalEvent]:
        """SSE 事件源：先给当前快照（eval.status + eval.progress），再推送实时事件；到达终态后结束。

        事件：``eval.progress``（节流）、``eval.item_done``、``eval.status``、``eval.budget_exceeded``。
        """
        sub = self.ctx.bus.subscribe(eval_topic(run_id))
        try:
            detail = await self.get(run_id)
            yield EvalEvent(
                "eval.status",
                {
                    "run_id": run_id,
                    "status": detail.status,
                    "pause_reason": detail.pause_reason,
                    "progress": detail.progress,
                    "started_at": detail.started_at,
                    "finished_at": detail.finished_at,
                    "stats": detail.stats,
                },
            )
            yield EvalEvent("eval.progress", {"run_id": run_id, "progress": detail.progress})
            if detail.status in TERMINAL_RUN_STATUSES:
                return
            async for item in sub:
                yield item
                if item.event == "eval.status" and item.data.get("status") in TERMINAL_RUN_STATUSES:
                    return
        finally:
            sub.close()

    # ================================================================ 调度器回调

    async def _group_info(self, run_id: str) -> GroupInfo | None:
        async with self.ctx.db.read() as s:
            run = await s.get(EvalRun, run_id)
        if run is None:
            return None
        return GroupInfo(limit=run.concurrency, paused=run.status in ("paused", "cancelled"))

    async def reconcile(self) -> None:
        """启动对账：所有 Item 都已结束、但 Run 没来得及收尾的（进程在收尾前退出），补做收尾。"""
        async with self.ctx.db.read() as s:
            ids = (
                (
                    await s.execute(
                        select(EvalRun.id).where(EvalRun.status.in_(("queued", "running", "paused")))
                    )
                )
                .scalars()
                .all()
            )
        for rid in ids:
            await self._maybe_finalize(rid)

    async def _on_retry(self, job: Job, exc: BaseException) -> None:
        async with self.ctx.db.write() as s:
            await s.execute(
                update(EvalItem)
                .where(EvalItem.id == job.ref_id, EvalItem.status.in_(("running", "pending")))
                .values(status="pending", error=error_text(getattr(exc, "code", "RETRIABLE"), str(exc), True))
            )
        item = await self._item_run_id(job.ref_id)
        if item:
            self._notify_progress(item)

    async def _on_fail(self, job: Job, exc: BaseException) -> None:
        """任务确定失败（不可重试的异常，或可重试的错误用尽了重试次数）：Item 记为 failed。"""
        code = getattr(exc, "code", None) or type(exc).__name__
        retriable = isinstance(exc, Retriable)
        run_id: str | None = None
        async with self.ctx.db.write() as s:
            it = await s.get(EvalItem, job.ref_id)
            if it is None or it.status not in ACTIVE_ITEM:
                return
            run_id = it.run_id
            it.status, it.finished_at = "failed", now_iso()
            it.error = error_text(code, str(exc) or code, retriable)
            if not retriable:  # 可重试的错误在 handler 里已经记录过失败尝试
                it.usage = _with_failed_attempt(
                    it.usage,
                    {"attempt": job.attempts, "error": {"code": code, "message": str(exc)}, "cost": 0.0},
                )
        await self._publish_item_done(job.ref_id, run_id)
        await self._maybe_finalize(run_id)

    async def _on_cancel(self, job: Job) -> None:
        async with self.ctx.db.write() as s:
            await s.execute(
                update(EvalItem)
                .where(EvalItem.id == job.ref_id, EvalItem.status.in_(ACTIVE_ITEM))
                .values(status="cancelled", finished_at=now_iso())
            )

    async def _on_reset(self, job: Job) -> None:
        """进程重启 / 停机把任务放回了队列：running 的 Item 重置为 pending，废弃的会话标记为 error。"""
        await self.ctx.svc(SessionService).recover()
        async with self.ctx.db.write() as s:
            it = await s.get(EvalItem, job.ref_id)
            if it is None or it.status != "running":
                return
            wasted = 0.0
            if it.session_id:
                from evalp.store.models import Session as SessionRow

                sess = await s.get(SessionRow, it.session_id)
                if sess is not None:
                    sess.status, sess.updated_at = "error", now_iso()
                    wasted = float((sess.stats or {}).get("cost", 0.0) or 0.0)
            it.usage = _with_failed_attempt(
                it.usage,
                {
                    "attempt": max(job.attempts, 1),
                    "session_id": it.session_id,
                    "error": {"code": "INTERRUPTED", "message": "服务重启或停机，本次执行被中断"},
                    "cost": wasted,
                },
            )
            it.status = "pending"
            it.started_at = None

    async def _rescore_revert(self, job: Job, exc: BaseException) -> None:
        """重新评分任务失败：Item 回到 done（保留旧评分）。"""
        await self._rescore_revert_job(job)
        log.warning("eval.rescore_failed", item_id=job.ref_id, error=str(exc))
        rid = await self._item_run_id(job.ref_id)
        if rid:
            await self._maybe_finalize(rid)

    async def _rescore_revert_job(self, job: Job) -> None:
        async with self.ctx.db.write() as s:
            await s.execute(
                update(EvalItem)
                .where(EvalItem.id == job.ref_id, EvalItem.status == "scoring")
                .values(status="done")
            )

    async def _item_run_id(self, item_id: str) -> str | None:
        async with self.ctx.db.read() as s:
            return (
                await s.execute(select(EvalItem.run_id).where(EvalItem.id == item_id))
            ).scalar_one_or_none()

    # ================================================================ 执行单元（8.4）

    async def _handle_item(self, job: Job) -> None:
        """job 处理函数：执行（缓存命中的跳过）-> 评分 -> 收尾。"""
        async with self.ctx.db.read() as s:
            item = await s.get(EvalItem, job.ref_id)
            if item is None:
                return
            run = await s.get(EvalRun, item.run_id)
            case_row = await s.get(EvalCase, item.case_id)
        if run is None or case_row is None or run.status == "cancelled":
            return
        if item.status not in ACTIVE_ITEM:
            return  # 已经完成（进程在 job 标记完成之前退出过）
        await self._mark_run_started(run.id)
        if (
            item.status == "scoring"
            and item.reused_from_item_id
            and not await self._session_exists(item.session_id)
        ):
            # 缓存来源的会话已被清理：放弃复用，重新执行
            async with self.ctx.db.write() as s:
                await s.execute(
                    update(EvalItem)
                    .where(EvalItem.id == item.id, EvalItem.status == "scoring")
                    .values(
                        status="pending",
                        reused_from_item_id=None,
                        session_id=None,
                        cost=None,
                        usage=None,
                        latency_ms=None,
                    )
                )
            item.status = "pending"
        if item.status in ("pending", "running"):
            if not await self._execute_item(job, item, run, case_row):
                return
            await self._check_budget(run.id)
        await self._score_item(item.id)
        await self._maybe_finalize(run.id)

    async def _handle_rescore(self, job: Job) -> None:
        async with self.ctx.db.read() as s:
            item = await s.get(EvalItem, job.ref_id)
            run = await s.get(EvalRun, item.run_id) if item is not None else None
        if item is None or run is None or run.status == "cancelled" or item.status != "scoring":
            return
        await self._score_item(item.id)
        await self._maybe_finalize(run.id)

    async def _session_exists(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        from evalp.store.models import Session as SessionRow

        async with self.ctx.db.read() as s:
            return await s.get(SessionRow, session_id) is not None

    async def _mark_run_started(self, run_id: str) -> None:
        if run_id in self._started_runs:
            return
        changed = False
        async with self.ctx.db.write() as s:
            run = await s.get(EvalRun, run_id)
            if run is not None and run.status == "queued":
                run.status, run.started_at = "running", run.started_at or now_iso()
                changed = True
            elif run is not None and run.started_at is None:
                run.started_at = now_iso()
        self._started_runs.add(run_id)
        if changed:
            await self._publish_status(run_id)

    async def _execute_item(self, job: Job, item: EvalItem, run: EvalRun, case_row: EvalCase) -> bool:
        """执行一个 Item：新建 eval 会话，按顺序在同一个会话里跑完各轮。成功返回 True（Item 进入 scoring）。

        失败：不可重试 -> Item 记为 failed 并返回 False；可重试 -> 记录失败尝试后抛
        Retriable（由调度器退避重试）。
        """
        sessions = self.ctx.svc(SessionService)
        md = case_row.metadata_ or {}
        turns = case_row.input.get("turns", [])
        async with self.ctx.db.write() as s:
            ok = await s.execute(
                update(EvalItem)
                .where(EvalItem.id == item.id, EvalItem.status.in_(("pending", "running")))
                .values(status="running", attempts=job.attempts, started_at=item.started_at or now_iso())
            )
            if ok.rowcount != 1:  # type: ignore[attr-defined]
                return False  # 刚被取消
        self._notify_progress(run.id)
        outs: list[Any] = []
        session_id: str | None = None
        err: dict[str, Any] | None = None
        try:
            sess = await sessions.create(
                version_id=run.version_id,
                mode="eval",
                title=f"{case_row.case_key} #{item.repeat_idx}",
                variables=md.get("variables") or {},
            )
            session_id = sess.id
            async with self.ctx.db.write() as s:
                await s.execute(
                    update(EvalItem)
                    .where(EvalItem.id == item.id, EvalItem.status == "running")
                    .values(session_id=sess.id)
                )
            for text in turns:
                out = await sessions.run_turn(
                    sess.id,
                    text,
                    tool_mocks=md.get("tool_mocks") or None,
                    priority=PRIORITY_EVAL,
                    wait=True,
                )
                outs.append(out)
                if out.status != "ok":
                    e = out.error
                    err = {
                        "code": e.code if e else out.status.upper(),
                        "message": e.message if e else f"第 {out.idx + 1} 轮以 {out.status} 结束",
                        "retriable": bool(e.retriable) if e else False,
                    }
                    break
        except EvalpError as e:  # 版本 / 会话层面的错误：不可重试
            err = {"code": e.code, "message": e.message, "retriable": False}
        cost = sum(float(o.cost or 0.0) for o in outs)
        if err is not None:
            await self._record_failed_attempt(item.id, job.attempts, session_id, cost, err)
            if err["retriable"]:
                raise Retriable(f"{err['code']}: {err['message']}", code=err["code"])
            await self._fail_item(item.id, err)
            await self._publish_item_done(item.id, run.id)
            await self._maybe_finalize(run.id)
            return False
        usage_sum: dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
        }
        for o in outs:
            for k in usage_sum:
                usage_sum[k] += int((o.usage or {}).get(k, 0) or 0)
        usage_sum["turns"] = len(outs)
        latency = sum(int(o.latency_ms or 0) for o in outs)
        async with self.ctx.db.write() as s:
            it = await s.get(EvalItem, item.id)
            if it is None or it.status != "running":
                return False
            prev = it.usage or {}
            for k in ("failed_attempts", "wasted_cost"):
                if k in prev:
                    usage_sum[k] = prev[k]
            it.status, it.cost, it.usage, it.latency_ms, it.error = (
                "scoring",
                round(cost, 8),
                usage_sum,
                latency,
                None,
            )
        return True

    async def _record_failed_attempt(
        self, item_id: str, attempt: int, session_id: str | None, cost: float, err: dict[str, Any]
    ) -> None:
        """失败尝试的成本记在 usage.failed_attempts / wasted_cost 里（不计入预算）。"""
        async with self.ctx.db.write() as s:
            it = await s.get(EvalItem, item_id)
            if it is None:
                return
            it.usage = _with_failed_attempt(
                it.usage,
                {
                    "attempt": attempt,
                    "session_id": session_id,
                    "error": {"code": err["code"], "message": err["message"][:500]},
                    "cost": round(cost, 8),
                },
            )
            if err["retriable"]:
                it.error = error_text(err["code"], err["message"], True)

    async def _fail_item(self, item_id: str, err: dict[str, Any]) -> None:
        async with self.ctx.db.write() as s:
            await s.execute(
                update(EvalItem)
                .where(EvalItem.id == item_id, EvalItem.status.in_(ACTIVE_ITEM))
                .values(
                    status="failed",
                    finished_at=now_iso(),
                    error=error_text(err["code"], err["message"], err["retriable"]),
                )
            )

    # ---------------------------------------------------------------- 预算

    async def _spent(self, s: AsyncSession, run_id: str) -> tuple[float, int]:
        """本 Run 新增的成本与 token（非缓存命中、已成功执行的 Item）。"""
        usage_txt = cast(EvalItem.usage, Text)
        row = (
            await s.execute(
                select(
                    func.coalesce(func.sum(EvalItem.cost), 0.0),
                    func.coalesce(
                        func.sum(
                            func.coalesce(func.json_extract(usage_txt, "$.prompt_tokens"), 0)
                            + func.coalesce(func.json_extract(usage_txt, "$.completion_tokens"), 0)
                        ),
                        0,
                    ),
                ).where(
                    EvalItem.run_id == run_id,
                    EvalItem.reused_from_item_id.is_(None),
                    EvalItem.status.in_(("scoring", "done")),
                )
            )
        ).one()
        return float(row[0]), int(row[1])

    @staticmethod
    def _over_budget(budget: dict[str, Any], cost: float, tokens: int) -> bool:
        mc, mt = budget.get("max_cost"), budget.get("max_tokens")
        return (mc is not None and cost > mc) or (mt is not None and tokens > mt)

    async def _check_budget(self, run_id: str) -> None:
        """每个 Item 执行完成后检查预算：超出则暂停该 Run，并推送 eval.budget_exceeded。"""
        exceeded: dict[str, Any] | None = None
        async with (
            self.ctx.db.read() as s
        ):  # 先用读连接判断有没有预算，避免没设预算时每个 Item 都开一次写事务
            run = await s.get(EvalRun, run_id)
            if run is None or not run.budget or run.status not in ("running", "queued"):
                return
        async with self.ctx.db.write() as s:
            run = await s.get(EvalRun, run_id)
            if run is None or not run.budget or run.status not in ("running", "queued"):
                return
            cost, tokens = await self._spent(s, run_id)
            if self._over_budget(run.budget, cost, tokens):
                run.status = "paused"
                run.progress = {**(run.progress or {}), "pause_reason": "budget"}
                exceeded = {
                    "max_cost": run.budget.get("max_cost"),
                    "max_tokens": run.budget.get("max_tokens"),
                    "cost": round(cost, 8),
                    "tokens": tokens,
                }
        if exceeded is not None:
            log.warning("eval.budget_exceeded", run_id=run_id, **exceeded)
            self.scheduler.group_changed(run_id)
            await self._publish_status(run_id)
            self._publish(run_id, "eval.budget_exceeded", exceeded)

    # ---------------------------------------------------------------- 评分

    async def _score_item(self, item_id: str) -> None:
        """对一个 Item 评分：只计算缺失 / 配置变化 / 出错的评估器，写入评分与 Item 的通过结果。"""
        async with self.ctx.db.read() as s:
            item = await s.get(EvalItem, item_id)
            if item is None or item.status != "scoring":
                return
            run = await s.get(EvalRun, item.run_id)
            case_row = await s.get(EvalCase, item.case_id)
            existing = {
                r.evaluator_key: r
                for r in (await s.execute(select(EvalScore).where(EvalScore.item_id == item_id))).scalars()
            }
        assert run is not None and case_row is not None
        configs: list[dict[str, Any]] = run.evaluators
        evaluators = build_evaluators(self.ctx, configs)
        hashes = {c["key"]: evaluator_hash(c) for c in configs}
        todo = [
            e
            for e in evaluators
            if (row := existing.get(e.key)) is None
            or row.evaluator_hash != hashes[e.key]
            or row.status == "error"
        ]
        results: dict[str, ScoreResult] = {}
        retry: Retriable | None = None
        if todo:
            transcript, events, loaded = await self._load_transcript(item, any(e.needs_events for e in todo))
            case_view = _case_view(case_row)
            for ev in todo:
                if not loaded:
                    results[ev.key] = ScoreResult.error("会话已被清理，无法评分")
                    continue
                try:
                    results[ev.key] = await ev.evaluate(case_view, transcript, events)
                except Retriable as e:
                    retry = e
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - 评估器自己的缺陷不应让 Item 失败
                    log.warning("eval.evaluator_crashed", key=ev.key, error=f"{type(e).__name__}: {e}")
                    results[ev.key] = ScoreResult.error(f"评估器异常: {type(e).__name__}: {e}"[:300])
        await self._write_scores(item_id, configs, hashes, results, finish=retry is None)
        if retry is not None:
            raise retry
        await self._publish_item_done(item_id, run.id)

    async def _write_scores(
        self,
        item_id: str,
        configs: list[dict[str, Any]],
        hashes: dict[str, str],
        results: dict[str, ScoreResult],
        *,
        finish: bool,
    ) -> None:
        keys = [c["key"] for c in configs]
        now = now_iso()
        async with self.ctx.db.write() as s:
            it = await s.get(EvalItem, item_id)
            if it is None or it.status != "scoring":
                return
            rows = {
                r.evaluator_key: r
                for r in (await s.execute(select(EvalScore).where(EvalScore.item_id == item_id))).scalars()
            }
            for key, res in results.items():
                row = rows.get(key)
                values = {
                    "evaluator_hash": hashes[key],
                    "status": res.status,
                    "score": res.score,
                    "raw_score": res.raw_score,
                    "passed": None if res.passed is None else int(res.passed),
                    "reason": res.reason,
                    "detail": res.detail,
                    "judge_session_id": res.judge_session_id,
                    "annotator": None,
                    "created_at": now,
                }
                if row is None:
                    row = EvalScore(id=_score_id(), item_id=item_id, evaluator_key=key, **values)
                    s.add(row)
                    rows[key] = row
                else:
                    for k, v in values.items():
                        setattr(row, k, v)
            for key in [k for k in rows if k not in keys]:  # 评估器被删除了
                await s.delete(rows.pop(key))
            if finish:
                gate_keys = [c["key"] for c in configs if c.get("gate")]
                it.passed = compute_passed(
                    gate_keys,
                    [ScoreRow(item_id, k, r.status, r.score, r.passed) for k, r in rows.items()],
                )
                it.status, it.finished_at, it.error = "done", it.finished_at or now, None

    async def _load_transcript(self, item: EvalItem, with_events: bool) -> tuple[Transcript, list[Any], bool]:
        """读取 Item 的会话，构造评估器看到的对话与事件。第三个返回值表示会话是否还在。"""
        if not item.session_id:
            return Transcript(), [], False
        sessions = self.ctx.svc(SessionService)
        try:
            detail = await sessions.get(item.session_id, include_turns=True)
        except NotFound:
            return Transcript(), [], False
        turns = [_turn_view(t) for t in detail.turns]
        events = await sessions.get_events(item.session_id, resolve_blobs=True) if with_events else []
        return Transcript(turns), events, True

    async def _publish_item_done(self, item_id: str, run_id: str | None = None) -> None:
        if run_id is None:
            run_id = await self._item_run_id(item_id)
            if run_id is None:
                return
        self._notify_progress(run_id)
        if self.ctx.bus.subscriber_count(eval_topic(run_id)) == 0:
            return  # 没有订阅者：不必组装事件
        async with self.ctx.db.read() as s:
            row = (
                await s.execute(
                    select(EvalItem, EvalCase.case_key)
                    .join(EvalCase, EvalCase.id == EvalItem.case_id)
                    .where(EvalItem.id == item_id)
                )
            ).first()
            if row is None:
                return
            it, case_key = row
            scores = (await s.execute(select(EvalScore).where(EvalScore.item_id == item_id))).scalars().all()
        self._publish(
            it.run_id,
            "eval.item_done",
            {
                "item_id": it.id,
                "case_key": case_key,
                "repeat_idx": it.repeat_idx,
                "status": it.status,
                "passed": None if it.passed is None else bool(it.passed),
                "cached": it.reused_from_item_id is not None,
                "latency_ms": it.latency_ms,
                "cost": it.cost,
                "error": _parse_error(it.error),
                "scores": {
                    sc.evaluator_key: {"status": sc.status, "score": sc.score, "passed": _b(sc.passed)}
                    for sc in scores
                },
            },
        )

    # ---------------------------------------------------------------- 收尾

    async def _maybe_finalize(self, run_id: str) -> None:
        """所有 Item 都结束后，计算汇总指标并把 Run 置为 completed / completed_with_errors。"""
        async with self.ctx.db.read() as s:
            run = await s.get(EvalRun, run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            remaining = (
                await s.execute(
                    select(func.count())
                    .select_from(EvalItem)
                    .where(EvalItem.run_id == run_id, EvalItem.status.in_(ACTIVE_ITEM))
                )
            ).scalar_one()
        if remaining:
            self._notify_progress(run_id)
            return
        done = False
        async with self.ctx.db.write() as s:
            run = await s.get(EvalRun, run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            stats = await self._compute_stats(s, run)
            run.stats = stats
            run.status = "completed_with_errors" if stats["failed"] else "completed"
            run.finished_at = now_iso()
            run.started_at = run.started_at or run.finished_at
            run.progress = {k: v for k, v in (run.progress or {}).items() if k != "pause_reason"}
            done = True
        if done:
            self._started_runs.discard(run_id)
            self.scheduler.group_changed(run_id)
            await self._publish_status(run_id, with_stats=True)
            log.info("eval.finished", run_id=run_id)

    async def _refresh_stats(self, run_id: str) -> None:
        async with self.ctx.db.write() as s:
            run = await s.get(EvalRun, run_id)
            if run is not None and run.status in TERMINAL_RUN_STATUSES:
                run.stats = await self._compute_stats(s, run)
        await self._publish_status(run_id, with_stats=True)

    # ================================================================ 生命周期操作

    async def pause(self, run_id: str) -> EvalDetail:
        """暂停：不再领取该 Run 的任务，正在执行的继续跑完。"""
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            if run.status not in ("queued", "running"):
                raise InvalidState(f"评测当前状态为 {run.status}，不能暂停", {"status": run.status})
            run.status = "paused"
            run.progress = {**(run.progress or {}), "pause_reason": "manual"}
        self.scheduler.group_changed(run_id)
        await self._publish_status(run_id)
        return await self.get(run_id)

    async def resume(self, run_id: str, budget: Budget | None = None) -> EvalDetail:
        """继续已暂停的评测；因预算暂停时可同时调高预算。"""
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            if run.status != "paused":
                raise InvalidState(
                    f"评测当前状态为 {run.status}，只有 paused 可以继续", {"status": run.status}
                )
            if budget is not None:
                run.budget = {**(run.budget or {}), **budget.model_dump(exclude_none=True)}
            cost, tokens = await self._spent(s, run_id)
            if self._over_budget(run.budget or {}, cost, tokens):
                raise InvalidState(
                    "已用成本 / token 超出预算，请在 resume 时调高 budget",
                    {"cost": round(cost, 8), "tokens": tokens, "budget": run.budget},
                )
            run.status = "running" if run.started_at else "queued"
            run.progress = {k: v for k, v in (run.progress or {}).items() if k != "pause_reason"}
        self.scheduler.group_changed(run_id)
        await self._publish_status(run_id)
        return await self.get(run_id)

    async def cancel(self, run_id: str) -> EvalDetail:
        """取消：未开始的 Item 置为 cancelled，正在执行的任务被取消。"""
        now = now_iso()
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                raise InvalidState(f"评测当前状态为 {run.status}，不能取消", {"status": run.status})
            run.status, run.finished_at = "cancelled", now
            run.progress = {k: v for k, v in (run.progress or {}).items() if k != "pause_reason"}
            await s.execute(
                update(EvalItem)
                .where(EvalItem.run_id == run_id, EvalItem.status.in_(ACTIVE_ITEM))
                .values(status="cancelled", finished_at=now)
            )
            await s.flush()
            run.stats = await self._compute_stats(s, run)
        await self.scheduler.cancel_group(run_id)
        self._started_runs.discard(run_id)
        await self._publish_status(run_id, with_stats=True)
        return await self.get(run_id)

    async def retry_failed(self, run_id: str) -> dict[str, Any]:
        """把 failed 的 Item 重新放回队列。返回 ``{"retried": n}``。"""
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            if run.status not in ("completed_with_errors", "completed", "running", "paused"):
                raise InvalidState(f"评测当前状态为 {run.status}，不能重试失败项", {"status": run.status})
            ids = (
                (
                    await s.execute(
                        select(EvalItem.id)
                        .where(EvalItem.run_id == run_id, EvalItem.status == "failed")
                        .order_by(EvalItem.id)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                return {"retried": 0}
            for i in range(0, len(ids), 500):
                await s.execute(
                    update(EvalItem)
                    .where(EvalItem.id.in_(ids[i : i + 500]))
                    .values(
                        status="pending", error=None, attempts=0, finished_at=None, cost=None, latency_ms=None
                    )
                )
            await self.scheduler.enqueue_many(
                [(KIND_ITEM, i) for i in ids], group_id=run_id, priority=0, session=s
            )
            if run.status in ("completed_with_errors", "completed"):
                run.status, run.finished_at = "running", None
        self.scheduler.group_changed(run_id)
        await self._publish_status(run_id)
        return {"retried": len(ids)}

    async def rescore(self, run_id: str, body: RescoreIn | None = None) -> dict[str, Any]:
        """仅重新评分（详设 8.4）：不调用被测 agent。

        对比每个评估器的 ``evaluator_hash``，只重新计算配置变化 / 缺失 / 出错的评估器。``body.dataset``
        可把评测换绑到
        数据集的新修订（只改了 expected / rubric 时），换绑后全部评分按新的期望重算。
        """
        body = body or RescoreIn()
        new_configs = (
            [config_to_dict(c) for c in await resolve_evaluators(self.ctx, body.evaluators)]
            if body.evaluators is not None
            else None
        )
        rebase: dict[str, Any] | None = None
        async with self.ctx.db.read() as s:
            run0 = await self._run_row(s, run_id)
        if body.dataset is not None:
            rebase = await self._plan_rebase(run0, body.dataset)
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            if run.status not in ("completed", "completed_with_errors"):
                raise InvalidState(
                    f"评测当前状态为 {run.status}，只有已完成的评测可以重新评分", {"status": run.status}
                )
            old_configs = list(run.evaluators)
            configs = new_configs if new_configs is not None else old_configs
            changed_cases: set[str] = set()
            if rebase is not None:
                for old_id, (new_id_, differs) in rebase["map"].items():
                    await s.execute(
                        update(EvalItem)
                        .where(EvalItem.run_id == run_id, EvalItem.case_id == old_id)
                        .values(case_id=new_id_)
                    )
                    if differs:
                        changed_cases.add(new_id_)
                run.dataset_rev_id = rebase["rev_id"]
                await s.flush()
            if configs != old_configs:
                run.evaluators, run.evaluators_hash = configs, evaluators_hash(configs)
            hashes = {c["key"]: evaluator_hash(c) for c in configs}
            keys = set(hashes)
            items = (
                await s.execute(
                    select(EvalItem.id, EvalItem.case_id, EvalItem.session_id).where(
                        EvalItem.run_id == run_id, EvalItem.status == "done"
                    )
                )
            ).all()
            score_rows = (
                await s.execute(
                    select(
                        EvalScore.item_id, EvalScore.evaluator_key, EvalScore.evaluator_hash, EvalScore.status
                    )
                    .join(EvalItem, EvalItem.id == EvalScore.item_id)
                    .where(EvalItem.run_id == run_id)
                )
            ).all()
            have: dict[str, dict[str, tuple[str, str]]] = {}
            for iid, key, h, st in score_rows:
                have.setdefault(iid, {})[key] = (h, st)
            gates_changed = {c["key"]: c.get("gate") for c in configs} != {
                c["key"]: c.get("gate") for c in old_configs
            }
            todo: list[str] = []
            for iid, cid, _sid in items:
                cur = have.get(iid, {})
                stale = (
                    set(cur) != keys
                    or any(cur[k][0] != hashes[k] or cur[k][1] == "error" for k in keys if k in cur)
                    or cid in changed_cases
                    or gates_changed
                )
                if stale:
                    todo.append(iid)
            for i in range(0, len(todo), 500):
                await s.execute(
                    update(EvalItem)
                    .where(EvalItem.id.in_(todo[i : i + 500]))
                    .values(status="scoring", finished_at=None)
                )
            if todo and changed_cases:  # expected / rubric 变了：这些用例的旧评分一律作废
                await s.execute(
                    delete(EvalScore).where(
                        EvalScore.item_id.in_(
                            select(EvalItem.id).where(
                                EvalItem.run_id == run_id, EvalItem.case_id.in_(changed_cases)
                            )
                        )
                    )
                )
            if todo:
                await self.scheduler.enqueue_many(
                    [(KIND_RESCORE, i) for i in todo], group_id=run_id, priority=0, session=s
                )
                run.status, run.finished_at = "running", None
            else:
                run.stats = await self._compute_stats(s, run)
        if todo:
            self._started_runs.add(run_id)
            self.scheduler.group_changed(run_id)
            await self._publish_status(run_id)
        else:
            await self._publish_status(run_id, with_stats=True)
        return {
            "rescored_items": len(todo),
            "evaluators_changed": sorted(
                k
                for k in keys
                if k not in {c["key"] for c in old_configs}
                or evaluator_hash(next(c for c in old_configs if c["key"] == k)) != hashes[k]
            )
            if configs != old_configs
            else [],
            "rebased": rebase is not None,
        }

    async def _plan_rebase(self, run: EvalRun, dataset_ref: str) -> dict[str, Any]:
        """换绑到数据集的新修订：要求同一个数据集资产，且每个用例的 case_hash 不变。返回 {rev_id, map}。"""
        new_rev, new_asset = await self._resolve_dataset(dataset_ref)
        async with self.ctx.db.read() as s:
            old_asset = (
                await s.execute(select(AssetRevision.asset_id).where(AssetRevision.id == run.dataset_rev_id))
            ).scalar_one()
            if old_asset != new_asset:
                raise ValidationFailed(
                    "只能换绑到同一个数据集的其他修订",
                    {"errors": [{"path": "dataset", "message": "不是同一个数据集"}]},
                )
            old_cases = (
                (
                    await s.execute(
                        select(EvalCase).where(
                            EvalCase.id.in_(
                                select(EvalItem.case_id).where(EvalItem.run_id == run.id).distinct()
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            new_cases = {
                c.case_key: c
                for c in (
                    await s.execute(select(EvalCase).where(EvalCase.dataset_rev_id == new_rev))
                ).scalars()
            }
        mapping: dict[str, tuple[str, bool]] = {}
        problems: list[dict[str, str]] = []
        for oc in old_cases:
            nc = new_cases.get(oc.case_key)
            if nc is None:
                problems.append({"path": oc.case_key, "message": "新修订中没有这个用例"})
            elif nc.case_hash != oc.case_hash:
                problems.append(
                    {"path": oc.case_key, "message": "用例输入 / 变量 / tool_mocks 变了，需要重新运行"}
                )
            else:
                mapping[oc.id] = (nc.id, nc.expected != oc.expected or nc.rubric != oc.rubric)
        if problems:
            raise ValidationFailed(
                f"数据集新修订里有 {len(problems)} 个用例的输入变了或被删除，不能只重新评分，请新建评测",
                {"errors": problems[:100], "total": len(problems)},
            )
        return {"rev_id": new_rev, "map": mapping}

    # ================================================================ Item 查询

    async def _score_outs(
        self, scores: list[EvalScore], configs: list[dict[str, Any]]
    ) -> dict[str, list[ScoreOut]]:
        by_key = {c["key"]: c for c in configs}
        out: dict[str, list[ScoreOut]] = {}
        order = {c["key"]: i for i, c in enumerate(configs)}
        for sc in sorted(scores, key=lambda x: order.get(x.evaluator_key, 999)):
            cfg = by_key.get(sc.evaluator_key)
            out.setdefault(sc.item_id, []).append(
                ScoreOut(
                    evaluator_key=sc.evaluator_key,
                    evaluator_type=cfg["type"] if cfg else None,
                    gate=cfg.get("gate") if cfg else None,
                    status=sc.status,  # type: ignore[arg-type]
                    score=sc.score,
                    raw_score=sc.raw_score,
                    passed=_b(sc.passed),
                    reason=sc.reason,
                    detail=sc.detail,
                    judge_session_id=sc.judge_session_id,
                    annotator=sc.annotator,
                    stale=bool(cfg and sc.evaluator_hash != evaluator_hash(cfg)),
                )
            )
        return out

    def _item_out(
        self,
        it: EvalItem,
        case_row: EvalCase,
        scores: list[ScoreOut],
        gate_keys: list[str],
        cls: type[ItemOut] = ItemOut,
    ) -> ItemOut:
        vals = [
            s.score
            for s in scores
            if s.evaluator_key in gate_keys and s.status == "done" and s.score is not None
        ]
        return cls(
            id=it.id,
            run_id=it.run_id,
            case=CaseBrief(
                id=case_row.id,
                case_key=case_row.case_key,
                idx=case_row.idx,
                tags=list(case_row.tags or []),
                input=case_row.input,
            ),
            repeat_idx=it.repeat_idx,
            status=it.status,  # type: ignore[arg-type]
            passed=_b(it.passed),
            score=0.0 if it.status == "failed" else mean(vals),
            cached=it.reused_from_item_id is not None,
            reused_from_item_id=it.reused_from_item_id,
            attempts=it.attempts,
            error=_parse_error(it.error),
            latency_ms=it.latency_ms,
            cost=it.cost,
            usage=it.usage,
            session_id=it.session_id,
            started_at=it.started_at,
            finished_at=it.finished_at,
            scores=scores,
        )

    async def list_items(
        self,
        run_id: str,
        *,
        status: str | list[str] | None = None,
        cls: str | None = None,
        tag: str | list[str] | None = None,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[ItemOut]:
        """Run 的 Item 列表（按用例顺序、重复序号排序）。

        ``cls``：passed（门禁通过）| failed（评分未通过）| error（执行失败或有评估器出错）|
        flaky（用例的重复结果不一致）|
        pending_human（有等待人工打分的评分）。
        """
        statuses = [x.strip() for x in status.split(",") if x.strip()] if isinstance(status, str) else status
        tags = [tag] if isinstance(tag, str) else list(tag or [])
        if cls is not None and cls not in ITEM_CLASSES:
            raise ValidationFailed(
                f"class 必须是 {' / '.join(ITEM_CLASSES)} 之一",
                {"errors": [{"path": "class", "message": cls}]},
            )
        async with self.ctx.db.read() as s:
            run = await self._run_row(s, run_id)
            stmt = (
                select(EvalItem)
                .join(EvalCase, EvalCase.id == EvalItem.case_id)
                .where(EvalItem.run_id == run_id)
            )
            if statuses:
                stmt = stmt.where(EvalItem.status.in_(statuses))
            if tags:
                je = func.json_each(cast(EvalCase.tags, Text)).table_valued("value")
                stmt = stmt.where(select(je.c.value).where(je.c.value.in_(tags)).exists())
            if q:
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                stmt = stmt.where(
                    EvalCase.case_key.like(like, escape="\\")
                    | cast(EvalCase.input, Text).like(like, escape="\\")
                )
            if cls == "passed":
                stmt = stmt.where(EvalItem.status == "done", EvalItem.passed == 1)
            elif cls == "failed":
                stmt = stmt.where(EvalItem.status == "done", EvalItem.passed == 0)
            elif cls == "error":
                bad = exists().where(EvalScore.item_id == EvalItem.id, EvalScore.status == "error")
                stmt = stmt.where(or_(EvalItem.status == "failed", bad))
            elif cls == "pending_human":
                stmt = stmt.where(
                    exists().where(EvalScore.item_id == EvalItem.id, EvalScore.status == "pending_human")
                )
            elif cls == "flaky":
                outcome = case((and_(EvalItem.status == "done", EvalItem.passed == 1), 1.0), else_=0.0)
                flaky = (
                    select(EvalItem.case_id)
                    .where(
                        EvalItem.run_id == run_id,
                        or_(
                            EvalItem.status == "failed",
                            and_(EvalItem.status == "done", EvalItem.passed.is_not(None)),
                        ),
                    )
                    .group_by(EvalItem.case_id)
                    .having(func.avg(outcome) > 0, func.avg(outcome) < 1)
                )
                stmt = stmt.where(EvalItem.case_id.in_(flaky))
            page = await paginate(s, stmt, EvalItem.id, limit=limit, cursor=cursor, order="asc")
            items: list[EvalItem] = page.items
            case_rows = {
                c.id: c
                for c in (
                    await s.execute(select(EvalCase).where(EvalCase.id.in_({i.case_id for i in items})))
                ).scalars()
            }
            scores = (
                list(
                    (
                        await s.execute(select(EvalScore).where(EvalScore.item_id.in_([i.id for i in items])))
                    ).scalars()
                )
                if items
                else []
            )
        score_map = await self._score_outs(scores, run.evaluators)
        gates = [c["key"] for c in run.evaluators if c.get("gate")]
        return Page(
            items=[self._item_out(i, case_rows[i.case_id], score_map.get(i.id, []), gates) for i in items],
            next_cursor=page.next_cursor,
        )

    async def get_item(self, run_id: str, item_id: str) -> ItemDetail:
        """Item 详情：完整用例、各轮输入输出、各评估器分数与会话 id。"""
        async with self.ctx.db.read() as s:
            run = await self._run_row(s, run_id)
            it = await s.get(EvalItem, item_id)
            if it is None or it.run_id != run_id:
                raise NotFound(f"Item 不存在: {item_id}", {"id": item_id})
            case_row = await s.get(EvalCase, it.case_id)
            assert case_row is not None
            scores = list((await s.execute(select(EvalScore).where(EvalScore.item_id == item_id))).scalars())
        score_map = await self._score_outs(scores, run.evaluators)
        gates = [c["key"] for c in run.evaluators if c.get("gate")]
        base = self._item_out(it, case_row, score_map.get(item_id, []), gates)
        transcript, _, loaded = await self._load_transcript(it, False)
        turns = [
            {
                "idx": t.idx,
                "input": t.input,
                "output": t.output,
                "status": t.status,
                "cost": t.cost,
                "latency_ms": t.latency_ms,
                "usage": t.usage,
                "error": t.error,
            }
            for t in transcript.turns
        ]
        return ItemDetail(
            **base.model_dump(),
            case_detail=_case_view(case_row).to_dict(),
            output=transcript.final if loaded else None,
            turns=turns,
        )

    # ================================================================ 人工打分

    async def human_scores(self, run_id: str, entries: list[HumanScoreIn]) -> dict[str, Any]:
        """提交人工评分：评分变为 done 并计入通过判定与汇总。整个请求要么全部生效要么全部不生效。"""
        errors: list[dict[str, str]] = []
        touched: set[str] = set()
        now = now_iso()
        async with self.ctx.db.write() as s:
            run = await self._run_row(s, run_id)
            human = {c["key"]: c for c in run.evaluators if c["type"] == "human"}
            for i, e in enumerate(entries):
                cfg = human.get(e.evaluator_key)
                if cfg is None:
                    errors.append(
                        {"path": f"[{i}].evaluator_key", "message": f"不是 human 评估器: {e.evaluator_key}"}
                    )
                    continue
                lo, hi = cfg["scale"]
                if not lo <= e.raw_score <= hi:
                    errors.append({"path": f"[{i}].raw_score", "message": f"必须在 [{lo:g}, {hi:g}] 内"})
                    continue
                it = await s.get(EvalItem, e.item_id)
                if it is None or it.run_id != run_id:
                    errors.append({"path": f"[{i}].item_id", "message": f"Item 不存在: {e.item_id}"})
                    continue
                if it.status not in ("done", "scoring"):
                    errors.append({"path": f"[{i}].item_id", "message": f"Item 状态为 {it.status}，不能打分"})
                    continue
                threshold = cfg.get("pass_threshold")
                threshold = lo + 0.6 * (hi - lo) if threshold is None else threshold
                row = (
                    await s.execute(
                        select(EvalScore).where(
                            EvalScore.item_id == e.item_id, EvalScore.evaluator_key == e.evaluator_key
                        )
                    )
                ).scalar_one_or_none()
                values = {
                    "evaluator_hash": evaluator_hash(cfg),
                    "status": "done",
                    "score": (e.raw_score - lo) / (hi - lo),
                    "raw_score": e.raw_score,
                    "passed": int(e.raw_score >= threshold),
                    "reason": e.reason,
                    "annotator": e.annotator or "human",
                    "created_at": now,
                }
                if row is None:
                    s.add(
                        EvalScore(id=_score_id(), item_id=e.item_id, evaluator_key=e.evaluator_key, **values)
                    )
                else:
                    for k, v in values.items():
                        setattr(row, k, v)
                touched.add(e.item_id)
            if errors:
                raise ValidationFailed("人工评分不合法", {"errors": errors})
            await s.flush()
            gate_keys = [c["key"] for c in run.evaluators if c.get("gate")]
            for iid in touched:
                it = await s.get(EvalItem, iid)
                rows = (await s.execute(select(EvalScore).where(EvalScore.item_id == iid))).scalars().all()
                if it is not None and it.status == "done":
                    it.passed = compute_passed(
                        gate_keys, [ScoreRow(iid, r.evaluator_key, r.status, r.score, r.passed) for r in rows]
                    )
        await self._refresh_stats(run_id)
        for iid in touched:
            await self._publish_item_done(iid, run_id)
        return {"updated": len(entries)}

    # ================================================================ 对比与门禁（8.7）

    async def _side(self, run_id: str) -> dict[str, Any]:
        async with self.ctx.db.read() as s:
            run = await self._run_row(s, run_id)
            items, scores = await self._stat_rows(s, run_id)
            (out,) = await self._run_outs(s, [run])
            dataset_name = out.dataset
        gates = [c["key"] for c in run.evaluators if c.get("gate")]
        stats = compute_stats(
            items, scores, evaluator_keys=[c["key"] for c in run.evaluators], repeats=run.repeats
        )
        return {
            "run": run,
            "out": out,
            "items": items,
            "scores": scores,
            "gates": gates,
            "stats": stats,
            "case_scores": case_scores(items, scores, gates),
            "dataset": dataset_name,
        }

    async def compare(
        self,
        left: str,
        right: str,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        cls: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """评测对比（详设 8.7）：按 case_key 关联，Δ、分类、配对 bootstrap 置信区间、符号检验、
        指标差值与分页的用例明细。"""
        if threshold < 0:
            raise ValidationFailed(
                "threshold 不能为负", {"errors": [{"path": "threshold", "message": "不能为负"}]}
            )
        a, b = await self._side(left), await self._side(right)
        deltas = classify_cases(a["case_scores"], b["case_scores"], threshold)
        summary = summarize_deltas(deltas)
        tags_by_case: dict[str, list[str]] = {}
        item_ids: dict[tuple[str, str], list[str]] = {}
        for side, name in ((a, "left"), (b, "right")):
            for it in side["items"]:
                tags_by_case.setdefault(it.case_key, it.tags)
                if it.status in ("done", "failed"):
                    item_ids.setdefault((name, it.case_key), []).append(it.id)
        warnings: list[dict[str, Any]] = []
        if a["run"].dataset_rev_id != b["run"].dataset_rev_id:
            warnings.append(
                {
                    "code": "DATASET_REVISION_DIFFERS",
                    "message": "两次评测使用的数据集修订不同，按 case_key 关联对比",
                    "left": a["run"].dataset_rev_id,
                    "right": b["run"].dataset_rev_id,
                }
            )
        for side, name in ((a, "left"), (b, "right")):
            if side["run"].status not in TERMINAL_RUN_STATUSES:
                warnings.append(
                    {
                        "code": "RUN_NOT_FINISHED",
                        "message": f"{name} 评测尚未结束（{side['run'].status}），结果是暂时的",
                        "side": name,
                    }
                )
        if a["gates"] != b["gates"]:
            warnings.append(
                {"code": "GATES_DIFFER", "message": "两次评测的门禁评估器不同，分数口径可能不一致"}
            )

        rows = [
            {
                "case_key": d.case_key,
                "class": d.cls,
                "flaky": d.flaky,
                "left": {"score": d.left, "item_ids": item_ids.get(("left", d.case_key), [])},
                "right": {"score": d.right, "item_ids": item_ids.get(("right", d.case_key), [])},
                "delta": d.delta,
                "tags": tags_by_case.get(d.case_key, []),
            }
            for d in deltas
        ]
        rows.sort(key=lambda r: (CLASS_ORDER[r["class"]], -abs(r["delta"] or 0.0), r["case_key"]))
        if cls:
            if cls != "flaky" and cls not in CLASS_ORDER:
                raise ValidationFailed(
                    f"class 必须是 {' / '.join([*CLASS_ORDER, 'flaky'])} 之一",
                    {"errors": [{"path": "class", "message": cls}]},
                )
            rows = [r for r in rows if (r["flaky"] if cls == "flaky" else r["class"] == cls)]
        if tag:
            rows = [r for r in rows if tag in r["tags"]]
        if q:
            rows = [r for r in rows if q.lower() in r["case_key"].lower()]
        start = 0
        if cursor:
            start = next((i + 1 for i, r in enumerate(rows) if r["case_key"] == cursor), 0)
        limit = max(1, min(limit, 200))
        page = rows[start : start + limit]
        next_cursor = page[-1]["case_key"] if start + limit < len(rows) and page else None

        def brief(side: dict[str, Any]) -> dict[str, Any]:
            o = side["out"]
            return {
                "id": o.id,
                "agent_id": o.agent_id,
                "agent_name": o.agent_name,
                "version_id": o.version_id,
                "version_seq": o.version_seq,
                "status": o.status,
                "dataset": o.dataset.model_dump(),
                "pass_rate": side["stats"]["pass_rate"],
            }

        return {
            "left": brief(a),
            "right": brief(b),
            "threshold": threshold,
            "warnings": warnings,
            "summary": {**summary, "metrics": _metric_deltas(a["stats"], b["stats"])},
            "items": page,
            "next_cursor": next_cursor,
        }

    async def gate(
        self,
        run_id: str,
        baseline_run_id: str | None = None,
        *,
        max_regressions: int = 0,
        min_pass_rate: float | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> dict[str, Any]:
        """门禁（供 CLI ``eval gate``）：相对基线的退化用例数不超过 ``max_regressions``，且通过率不低于
        ``min_pass_rate``。

        返回 ``{passed, reasons, summary}``；评测未结束、基线未结束、退化过多、通过率过低都会让
        ``passed=false``。
        """
        reasons: list[str] = []
        detail = await self.get(run_id)
        stats = detail.stats
        if detail.status not in ("completed", "completed_with_errors"):
            reasons.append(f"评测尚未成功结束（状态 {detail.status}）")
        if stats is None and detail.status in ("completed", "completed_with_errors"):
            async with self.ctx.db.read() as s:
                stats = await self._compute_stats(s, await self._run_row(s, run_id))
        pass_rate = (stats or {}).get("pass_rate")
        summary: dict[str, Any] = {
            "run_id": run_id,
            "status": detail.status,
            "pass_rate": pass_rate,
            "failed_items": (stats or {}).get("failed"),
            "baseline_run_id": baseline_run_id,
            "max_regressions": max_regressions,
            "min_pass_rate": min_pass_rate,
        }
        if min_pass_rate is not None and (pass_rate is None or pass_rate < min_pass_rate):
            shown = "无" if pass_rate is None else f"{pass_rate:.1%}"
            reasons.append(f"通过率 {shown} 低于要求的 {min_pass_rate:.1%}")
        if baseline_run_id:
            base = await self.get(baseline_run_id)
            summary["baseline_status"] = base.status
            summary["baseline_pass_rate"] = (base.stats or {}).get("pass_rate")
            if base.status not in ("completed", "completed_with_errors"):
                reasons.append(f"基线评测尚未结束（状态 {base.status}）")
            cmp = await self.compare(baseline_run_id, run_id, threshold=threshold, cls="regressed", limit=200)
            sm = cmp["summary"]
            summary.update(
                regressions=sm["regressed"],
                improved=sm["improved"],
                unchanged=sm["unchanged"],
                mean_delta=sm["mean_delta"],
                ci95=sm["ci95"],
                sign_test_p=sm["sign_test_p"],
                regressed_cases=[
                    {"case_key": r["case_key"], "delta": r["delta"], "item_ids": r["right"]["item_ids"]}
                    for r in cmp["items"]
                ][:50],
            )
            if sm["regressed"] > max_regressions:
                reasons.append(f"相对基线有 {sm['regressed']} 个用例退化，超过允许的 {max_regressions} 个")
        return {"passed": not reasons, "reasons": reasons, "summary": summary}


# ---------------------------------------------------------------- 辅助函数


def _b(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def _with_failed_attempt(usage: dict[str, Any] | None, entry: dict[str, Any]) -> dict[str, Any]:
    """返回追加了一条失败尝试记录的新 usage（不原地修改：JSON 列只有换成新对象才会被 ORM 检测到变化）。"""
    out = dict(usage or {})
    out["failed_attempts"] = [*out.get("failed_attempts", []), entry]
    out["wasted_cost"] = round(float(out.get("wasted_cost", 0.0)) + float(entry.get("cost", 0.0)), 8)
    return out


def _clean_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """缓存命中的 Item 沿用来源的用量，但不带来源自己的失败尝试记录。"""
    if usage is None:
        return None
    return {k: v for k, v in usage.items() if k not in ("failed_attempts", "wasted_cost")}


def _case_view(c: EvalCase) -> CaseView:
    return CaseView(
        case_key=c.case_key,
        input=c.input,
        expected=c.expected,
        rubric=c.rubric,
        tags=list(c.tags or []),
        metadata=c.metadata_ or {},
    )


def _turn_view(t: Any) -> TurnView:
    final = t.output.final.text() if t.output and t.output.final else ""
    msgs = [m.model_dump(mode="json", exclude_none=True) for m in (t.output.messages if t.output else [])]
    return TurnView(
        idx=t.idx,
        input=t.input.text(),
        output=final,
        status=t.status,
        messages=msgs,
        usage=t.usage or {},
        latency_ms=t.latency_ms,
        cost=t.cost or 0.0,
        error=t.error.model_dump() if t.error else None,
    )


def _delta(a: float | None, b: float | None) -> dict[str, Any]:
    return {"left": a, "right": b, "delta": (b - a) if a is not None and b is not None else None}


def _metric_deltas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """各项指标的差值（右 - 左）。"""
    keys = [k for k in left["evaluators"] if k in right["evaluators"]]
    return {
        "pass_rate": _delta(left["pass_rate"], right["pass_rate"]),
        "error_rate": _delta(left["error_rate"], right["error_rate"]),
        "evaluators": {
            k: _delta(left["evaluators"][k]["mean"], right["evaluators"][k]["mean"]) for k in keys
        },
        "latency_ms": {p: _delta(left["latency_ms"][p], right["latency_ms"][p]) for p in ("p50", "p95")},
        "tokens": {k: _delta(left["tokens"][k], right["tokens"][k]) for k in ("prompt", "completion")},
        "cost": _delta(left["cost"], right["cost"]),
        "cost_new": _delta(left["cost_new"], right["cost_new"]),
    }
