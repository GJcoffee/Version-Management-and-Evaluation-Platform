"""轮间中间件目录与产物候选接口。"""

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.errors import ValidationFailed
from evalp.runtime.middleware.registry import descriptors
from evalp.schemas.middleware import ArtifactCandidates
from evalp.services.middleware import MiddlewareService

router = APIRouter(tags=["middlewares"])


@router.get("/middlewares")
async def list_middlewares():
    return {"middlewares": descriptors()}


@router.get(
    "/sessions/{session_id}/turns/{source_turn_id}/artifact-candidates",
    response_model=ArtifactCandidates,
)
async def artifact_candidates(
    session_id: str, source_turn_id: str, url_pointer: str | None = Query(None),
    ctx: AppContext = Depends(get_ctx),
):
    try:
        return await ctx.svc(MiddlewareService).artifact_candidates(session_id, source_turn_id, url_pointer)
    except ValidationError:
        raise ValidationFailed("链接字段须为有效的 JSON Pointer") from None
