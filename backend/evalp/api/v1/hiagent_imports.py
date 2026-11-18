"""通过本机 hicli 把已发布的 HiAgent 应用导入版本库。"""

from fastapi import APIRouter, Depends, Query

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.services.hiagent_import import HiAgentId, HiAgentImportRequest, HiAgentImportService

router = APIRouter(prefix="/hiagent-imports", tags=["hiagent-imports"])


@router.get("/context")
async def import_context(agent_id: str | None = None, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(HiAgentImportService).context(agent_id)


@router.get("/apps")
async def list_apps(workspace_id: HiAgentId, page: int = Query(1, ge=1), ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(HiAgentImportService).apps(workspace_id, page)


@router.get("/releases")
async def list_releases(
    workspace_id: HiAgentId,
    app_id: HiAgentId,
    page: int = Query(1, ge=1),
    ctx: AppContext = Depends(get_ctx),
):
    return await ctx.svc(HiAgentImportService).releases(workspace_id, app_id, page)


@router.post("")
async def import_release(body: HiAgentImportRequest, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(HiAgentImportService).save(body)
