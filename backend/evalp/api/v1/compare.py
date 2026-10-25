"""对比与重放接口（契约 contract-eval 第 3 节）：/compare、/sessions/{id}/replay。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.schemas.compare import (
    CompareCreate,
    CompareGroupOut,
    ComparePairAccepted,
    ComparePairCreate,
    ReplayAccepted,
    ReplayCreate,
)
from evalp.services.compare import CompareService

router = APIRouter(tags=["compare"])


@router.post("/compare", response_model=CompareGroupOut, status_code=201)
async def create_compare(body: CompareCreate, ctx: AppContext = Depends(get_ctx)):
    """创建会话对比。live：传两个版本，自动创建两个 chat 会话；history：传两个已有会话。"""
    return await ctx.svc(CompareService).create(body)


@router.get("/compare", response_model=Page[CompareGroupOut])
async def list_compare(p: PageParams = Depends(page_params), ctx: AppContext = Depends(get_ctx)):
    """对比列表（新的在前）。"""
    return await ctx.svc(CompareService).list(limit=p.limit, cursor=p.cursor)


@router.get("/compare/{compare_id}", response_model=CompareGroupOut)
async def get_compare(compare_id: str, ctx: AppContext = Depends(get_ctx)):
    """对比详情。"""
    return await ctx.svc(CompareService).get(compare_id)


@router.post("/compare/{compare_id}/turns", response_model=ComparePairAccepted, status_code=202)
async def create_pair_turn(compare_id: str, body: ComparePairCreate, ctx: AppContext = Depends(get_ctx)):
    """双栏发送：两侧会话并发执行同一输入，立即返回；任意一侧正在执行时 409 ``SESSION_BUSY``。"""
    return await ctx.svc(CompareService).run_pair(compare_id, body.input, body.variables)


@router.get("/compare/{compare_id}/alignment")
async def get_alignment(compare_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """轮次与步骤对齐结果（服务端计算，包含正在执行的轮次的当前进度）。"""
    return await ctx.svc(CompareService).alignment(compare_id)


@router.post("/sessions/{session_id}/replay", response_model=ReplayAccepted, status_code=201)
async def replay_session(session_id: str, body: ReplayCreate, ctx: AppContext = Depends(get_ctx)):
    """用另一个版本重放历史会话（锚定模式）：立即返回新会话与对比组，各轮在后台执行。"""
    return await ctx.svc(CompareService).replay(session_id, body.target_version_id, body.turns, body.mode)
