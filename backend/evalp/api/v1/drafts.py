"""草稿接口：/api/v1/agents/{id}/drafts、/api/v1/drafts/{id}。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.schemas.agent import (
    DraftCreate,
    DraftOut,
    DraftSave,
    DraftValidateOut,
    DraftWrapper,
    PublishIn,
    PublishOut,
)
from evalp.services.draft import DraftService

router = APIRouter(tags=["drafts"])


@router.get("/agents/{agent_ref}/drafts", response_model=list[DraftOut])
async def list_drafts(agent_ref: str, ctx: AppContext = Depends(get_ctx)):
    """agent 的全部草稿。"""
    return await ctx.svc(DraftService).list(agent_ref)


@router.post("/agents/{agent_ref}/drafts", response_model=DraftWrapper, status_code=201)
async def create_draft(agent_ref: str, body: DraftCreate, ctx: AppContext = Depends(get_ctx)):
    """新建草稿（可指定 spec，或用 from_version_id 从版本复制）。"""
    return DraftWrapper(draft=await ctx.svc(DraftService).create(agent_ref, body))


@router.get("/drafts/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(DraftService).get(draft_id)


@router.put("/drafts/{draft_id}", response_model=DraftWrapper)
async def save_draft(draft_id: str, body: DraftSave, ctx: AppContext = Depends(get_ctx)):
    """保存草稿（乐观锁）：revision 不一致返回 409 DRAFT_CONFLICT，details.current_revision 为当前值。"""
    return DraftWrapper(draft=await ctx.svc(DraftService).save(draft_id, body.spec, body.expected_revision))


class DraftValidateIn(BaseModel):
    """可选：传入 spec 时做"干跑校验"（不保存、不改 revision）。"""

    spec: dict[str, Any] | None = None


@router.post("/drafts/{draft_id}/validate", response_model=DraftValidateOut)
async def validate_draft(
    draft_id: str, body: DraftValidateIn | None = Body(default=None), ctx: AppContext = Depends(get_ctx)
):
    """校验草稿，返回 errors / warnings 与固化后的 Spec 预览。请求体带 spec 时校验该 spec 而非已保存内容。"""
    return await ctx.svc(DraftService).validate(draft_id, spec=body.spec if body else None)


@router.post("/drafts/{draft_id}/publish", response_model=PublishOut)
async def publish_draft(draft_id: str, body: PublishIn, ctx: AppContext = Depends(get_ctx)):
    """发布为版本；同一 agent 下内容相同（spec_hash 相同）时返回已有版本（existed=true）。"""
    return await ctx.svc(DraftService).publish(draft_id, body.expected_revision, body.notes, body.via)
