"""资产接口：/api/v1/assets。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.errors import NotFound, ValidationFailed
from evalp.schemas.asset import (
    AssetCreate,
    AssetKind,
    AssetOut,
    AssetUpdate,
    AssetWriteOut,
    RevisionCreate,
    RevisionDetail,
    RevisionOut,
    UploadOut,
)
from evalp.services.asset import AssetService

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=Page[AssetOut])
async def list_assets(
    kind: AssetKind | None = None,
    q: str | None = Query(None, description="按名称 / 显示名 / 描述模糊搜索"),
    include_deleted: bool = False,
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """列出资产（默认不含已软删除的）。"""
    return await ctx.svc(AssetService).list(
        kind=kind, q=q, include_deleted=include_deleted, limit=p.limit, cursor=p.cursor
    )


@router.post("", response_model=AssetWriteOut)
async def create_asset(body: AssetCreate, response: Response, ctx: AppContext = Depends(get_ctx)):
    """创建资产（含首个修订）。同名资产已存在时新增修订（内容相同则 existed=true）。新建返回 201。"""
    out = await ctx.svc(AssetService).create(body)
    response.status_code = 201 if out.created else 200
    return out


@router.post("/upload", response_model=UploadOut)
async def upload_blob(file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)):
    """大文件上传（multipart，字段名 ``file``，最大 100MB）；返回的 blob_upload_id 可代替 content。"""
    return await ctx.svc(AssetService).upload(file.read)


@router.get("/diff")
async def diff_revisions(left: str, right: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """两个资产修订（arv_…）的 diff。"""
    return await ctx.svc(AssetService).diff(left, right)


@router.get("/revisions/{revision_id}", response_model=RevisionDetail)
async def get_revision_by_id(revision_id: str, ctx: AppContext = Depends(get_ctx)):
    """按修订 id 获取修订及内容。"""
    return await ctx.svc(AssetService).revision_detail(revision_id=revision_id)


@router.get("/revisions/{revision_id}/raw")
async def get_revision_raw(revision_id: str, ctx: AppContext = Depends(get_ctx)):
    """下载修订的原始内容（一律以附件形式返回，避免用户上传的 HTML 在本站点下被浏览器直接渲染）。"""
    rev = await ctx.svc(AssetService).get_revision(revision_id)
    if not ctx.blobs.exists(rev.blob_hash):
        raise NotFound(f"blob 不存在: {rev.blob_hash}")
    return FileResponse(
        ctx.blobs.path(rev.blob_hash),
        media_type=rev.content_type,
        filename=f"{rev.asset_id}@{rev.rev}",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AssetService).get(asset_id)


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: str, body: AssetUpdate, ctx: AppContext = Depends(get_ctx)):
    """修改资产描述。"""
    return await ctx.svc(AssetService).update(asset_id, body.description)


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: str, hard: bool = False, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """默认软删除；``?hard=true`` 物理删除，被版本或评测引用时返回 409 ASSET_IN_USE。"""
    return await ctx.svc(AssetService).delete(asset_id, hard=hard)


@router.post("/{asset_id}/restore", response_model=AssetOut)
async def restore_asset(asset_id: str, ctx: AppContext = Depends(get_ctx)):
    """恢复被软删除的资产。"""
    return await ctx.svc(AssetService).restore(asset_id)


@router.get("/{asset_id}/revisions", response_model=Page[RevisionOut])
async def list_revisions(
    asset_id: str, p: PageParams = Depends(page_params), ctx: AppContext = Depends(get_ctx)
):
    """修订列表（新修订在前）。"""
    return await ctx.svc(AssetService).list_revisions(asset_id, limit=p.limit, cursor=p.cursor)


@router.post("/{asset_id}/revisions", response_model=AssetWriteOut)
async def create_revision(
    asset_id: str, body: RevisionCreate, response: Response, ctx: AppContext = Depends(get_ctx)
):
    """新增修订；内容与已有修订相同时返回已有修订（existed=true，状态码 200），否则 201。"""
    out = await ctx.svc(AssetService).add_revision_to(asset_id, body)
    response.status_code = 200 if out.existed else 201
    return out


@router.get("/{asset_id}/revisions/{rev}", response_model=RevisionDetail)
async def get_revision(asset_id: str, rev: str, ctx: AppContext = Depends(get_ctx)):
    """按修订号（整数或 ``latest``）获取修订及内容。"""
    return await ctx.svc(AssetService).revision_detail(asset_id=asset_id, rev=_rev(rev))


@router.get("/{asset_id}/references")
async def get_references(
    asset_id: str, rev: int | None = None, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    """哪些版本 / 评测（以及草稿）引用了该资产；可用 ``?rev=`` 限定修订号。"""
    return await ctx.svc(AssetService).references(asset_id, rev)


def _rev(rev: str) -> int | str:
    if rev == "latest":
        return rev
    if rev.isdigit() and int(rev) >= 1:
        return int(rev)
    raise ValidationFailed("rev 必须是正整数或 latest", {"errors": [{"path": "rev", "message": rev}]})
