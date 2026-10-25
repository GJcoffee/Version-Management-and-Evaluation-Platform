"""评测接口（详设 8、11.3）：发起 / 查询 / 暂停继续取消 / 重试 / 重新评分 / Item / 人工打分 / SSE / 对比 /
门禁。

``/evals/compare`` 必须先于 ``/evals/{eval_id}`` 注册，避免被当成 id。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.api.sse import format_sse, sse_response
from evalp.compare.stats import DEFAULT_THRESHOLD
from evalp.context import AppContext
from evalp.schemas.eval import (
    Budget,
    EvalCreate,
    EvalCreated,
    EvalDetail,
    EvalRunOut,
    HumanScoreIn,
    ItemDetail,
    ItemOut,
    RescoreIn,
    ResumeIn,
)
from evalp.services.eval import EvalService

router = APIRouter(prefix="/evals", tags=["evals"])


class HumanScoresIn(BaseModel):
    scores: list[HumanScoreIn] = Field(min_length=1, max_length=2000)


@router.post("", response_model=EvalCreated, status_code=201)
async def create_eval(
    body: EvalCreate,
    response: Response,
    dry_run: bool | None = Query(None, description="只返回成本预估，不创建（也可放在请求体里）"),
    ctx: AppContext = Depends(get_ctx),
):
    """发起评测：每个版本一个 Run，同属一个批次。``dry_run`` 时返回 200 和 ``estimate``，不创建任何东西。"""
    out = await ctx.svc(EvalService).create(body, dry_run=dry_run)
    if out.dry_run:
        response.status_code = 200
    return out


@router.get("", response_model=Page[EvalRunOut])
async def list_evals(
    agent: str | None = Query(None, description="agent 名称或 id"),
    version_id: str | None = None,
    batch: str | None = Query(None, description="批次 id（evb_…）"),
    status: str | None = Query(None, description="状态，可用逗号分隔多个"),
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """评测列表（新的在前）。"""
    return await ctx.svc(EvalService).list(
        agent=agent, version_id=version_id, batch=batch, status=status, limit=p.limit, cursor=p.cursor
    )


@router.get("/compare")
async def compare_evals(
    left: str = Query(description="基线 Run id"),
    right: str = Query(description="对比 Run id"),
    threshold: float = Query(DEFAULT_THRESHOLD, ge=0, description="Δ 超过该值才算改进 / 退化"),
    cls: str | None = Query(
        None,
        alias="class",
        description="improved | regressed | unchanged | new | missing | unscored | flaky",
    ),
    tag: str | None = None,
    q: str | None = Query(None, description="case_key 模糊匹配"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="上一页最后一条的 case_key"),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """两次评测的对比（详设 8.7）：按 case_key 关联，Δ、分类、95% 置信区间、符号检验与用例明细。"""
    return await ctx.svc(EvalService).compare(
        left, right, threshold=threshold, cls=cls, tag=tag, q=q, limit=limit, cursor=cursor
    )


@router.get("/{eval_id}", response_model=EvalDetail)
async def get_eval(eval_id: str, ctx: AppContext = Depends(get_ctx)):
    """评测详情：配置、进度、汇总指标（``stats``）和同批次的其他 Run。"""
    return await ctx.svc(EvalService).get(eval_id)


@router.post("/{eval_id}/pause", response_model=EvalDetail)
async def pause_eval(eval_id: str, ctx: AppContext = Depends(get_ctx)):
    """暂停：不再领取新的 Item，正在执行的继续跑完。"""
    return await ctx.svc(EvalService).pause(eval_id)


@router.post("/{eval_id}/resume", response_model=EvalDetail)
async def resume_eval(eval_id: str, body: ResumeIn | None = None, ctx: AppContext = Depends(get_ctx)):
    """继续已暂停的评测；因预算暂停时可在请求体里调高 ``budget``。"""
    budget: Budget | None = body.budget if body else None
    return await ctx.svc(EvalService).resume(eval_id, budget)


@router.post("/{eval_id}/cancel", response_model=EvalDetail)
async def cancel_eval(eval_id: str, ctx: AppContext = Depends(get_ctx)):
    """取消：未开始的 Item 置为 cancelled，正在执行的被取消；已完成的结果保留。"""
    return await ctx.svc(EvalService).cancel(eval_id)


@router.post("/{eval_id}/retry-failed")
async def retry_failed(eval_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """把执行失败的 Item 重新放回队列。返回 ``{"retried": n}``。"""
    return await ctx.svc(EvalService).retry_failed(eval_id)


@router.post("/{eval_id}/rescore")
async def rescore_eval(
    eval_id: str, body: RescoreIn | None = None, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """只重新评分，不调用被测 agent：评估器配置变化 / 出错的重算；``dataset`` 可换绑到新的数据集修订。"""
    return await ctx.svc(EvalService).rescore(eval_id, body)


@router.get("/{eval_id}/items", response_model=Page[ItemOut])
async def list_items(
    eval_id: str,
    status: str | None = Query(None, description="Item 状态，可用逗号分隔多个"),
    cls: str | None = Query(
        None, alias="class", description="passed | failed | error | flaky | pending_human"
    ),
    tag: list[str] = Query(default=[], description="含任一标签"),
    q: str | None = Query(None, description="case_key / 输入的模糊匹配"),
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """Item 列表（按用例顺序、重复序号排序），带各评估器的分数。"""
    return await ctx.svc(EvalService).list_items(
        eval_id, status=status, cls=cls, tag=tag, q=q, limit=p.limit, cursor=p.cursor
    )


@router.get("/{eval_id}/items/{item_id}", response_model=ItemDetail)
async def get_item(eval_id: str, item_id: str, ctx: AppContext = Depends(get_ctx)):
    """Item 详情：完整用例、各轮输入输出、评估器分数（含理由 / 明细）与会话 id。"""
    return await ctx.svc(EvalService).get_item(eval_id, item_id)


@router.post("/{eval_id}/human-scores")
async def human_scores(
    eval_id: str, body: HumanScoresIn, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """提交人工评分（批量）；整个请求要么全部生效要么全部不生效。返回 ``{"updated": n}``。"""
    return await ctx.svc(EvalService).human_scores(eval_id, body.scores)


@router.get("/{eval_id}/gate")
async def gate_eval(
    eval_id: str,
    baseline: str | None = Query(None, description="基线 Run id"),
    max_regressions: int = Query(0, ge=0, description="允许退化的用例数"),
    min_pass_rate: float | None = Query(None, ge=0, le=1, description="最低通过率"),
    threshold: float = Query(DEFAULT_THRESHOLD, ge=0),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """CI 门禁：返回 ``{passed, reasons, summary}``（始终 200，由 ``passed`` 表示结论）。"""
    return await ctx.svc(EvalService).gate(
        eval_id,
        baseline,
        max_regressions=max_regressions,
        min_pass_rate=min_pass_rate,
        threshold=threshold,
    )


@router.get("/{eval_id}/stream")
async def stream_eval(eval_id: str, ctx: AppContext = Depends(get_ctx)):
    """SSE：先推一次当前快照（``eval.status`` + ``eval.progress``），再推送实时事件。

    事件：``eval.progress``（至多每秒 4 条）、``eval.item_done``、``eval.status``、``eval.budget_exceeded``；
    评测到达终态（completed / completed_with_errors / cancelled）后发送 ``end`` 帧并关闭。
    """
    svc = ctx.svc(EvalService)
    await svc.get(eval_id)  # 不存在时返回 404，而不是一个空的流

    async def frames() -> AsyncIterator[str]:
        async for ev in svc.stream(eval_id):
            yield format_sse(ev.data, event=ev.event)
        yield format_sse({"run_id": eval_id}, event="end")

    return sse_response(frames())
