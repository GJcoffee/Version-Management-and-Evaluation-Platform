"""工作区同步接口：/api/v1/workspace。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.schemas.agent import PullIn, PullOut, PushIn, PushOut
from evalp.services.workspace import WorkspaceService

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.post("/push", response_model=PushOut)
async def push(body: PushIn, ctx: AppContext = Depends(get_ctx)):
    """一个事务内：创建 agent（如不存在）、为变化的文件创建资产修订、改写 file: 引用、保存草稿。"""
    return await ctx.svc(WorkspaceService).push(body)


@router.post("/pull", response_model=PullOut)
async def pull(body: PullIn, ctx: AppContext = Depends(get_ctx)):
    """返回草稿 Spec 与 asset_map 中各资产的最新内容。"""
    return await ctx.svc(WorkspaceService).pull(body)
