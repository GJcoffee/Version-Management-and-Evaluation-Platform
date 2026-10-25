"""Agent 接口：/api/v1/agents。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.schemas.agent import AgentCreate, AgentCreated, AgentOut, AgentUpdate
from evalp.services.agent import AgentService

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=Page[AgentOut])
async def list_agents(
    include_archived: bool = False,
    include_hidden: bool = Query(False, description="包含平台内部的隐藏 agent（如评审 agent evalp-judge）"),
    kind: str | None = None,
    q: str | None = None,
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """列出 agent（默认不含已归档的和平台内部的隐藏 agent）。"""
    return await ctx.svc(AgentService).list(
        include_archived=include_archived,
        include_hidden=include_hidden,
        kind=kind,
        q=q,
        limit=p.limit,
        cursor=p.cursor,
    )


@router.post("", response_model=AgentCreated, status_code=201)
async def create_agent(body: AgentCreate, ctx: AppContext = Depends(get_ctx)):
    """创建 agent，同时创建 main 草稿（可带初始 spec，没带时生成该 kind 的最小模板）。"""
    return await ctx.svc(AgentService).create(body)


@router.get("/{agent_ref}", response_model=AgentOut)
async def get_agent(agent_ref: str, ctx: AppContext = Depends(get_ctx)):
    """按 id 或名称获取。"""
    return await ctx.svc(AgentService).get(agent_ref)


@router.patch("/{agent_ref}", response_model=AgentOut)
async def update_agent(agent_ref: str, body: AgentUpdate, ctx: AppContext = Depends(get_ctx)):
    """修改名称、描述，或归档 / 取消归档。"""
    return await ctx.svc(AgentService).update(agent_ref, body)


@router.delete("/{agent_ref}", status_code=204)
async def delete_agent(agent_ref: str, ctx: AppContext = Depends(get_ctx)):
    """删除 agent（已有版本时返回 409 AGENT_IN_USE，请改为归档）。"""
    await ctx.svc(AgentService).delete(agent_ref)
    return Response(status_code=204)
