"""版本接口：/api/v1/agents/{id}/versions、/api/v1/versions。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.schemas.agent import (
    DraftWrapper,
    MilestoneIn,
    TagIn,
    TagOut,
    ToDraftIn,
    VersionAssetsOut,
    VersionOut,
    VersionSummary,
)
from evalp.services.version import VersionService

router = APIRouter(tags=["versions"])


@router.get("/agents/{agent_ref}/versions", response_model=Page[VersionSummary])
async def list_versions(
    agent_ref: str,
    archived: bool | None = Query(
        None, description="false 只返回未归档的（里程碑与普通版本），true 只返回已归档的；缺省返回全部"
    ),
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """版本列表（新版本在前，含标签、里程碑与归档信息）。"""
    return await ctx.svc(VersionService).list(agent_ref, limit=p.limit, cursor=p.cursor, archived=archived)


@router.get("/agents/{agent_ref}/tags", response_model=list[TagOut])
async def list_tags(agent_ref: str, ctx: AppContext = Depends(get_ctx)):
    """agent 的全部标签。"""
    return await ctx.svc(VersionService).list_tags(agent_ref)


@router.get("/versions/diff")
async def diff_versions(left: str, right: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """版本 diff。left / right 可以是版本 id，也可以写成 ``agent名:v12`` 或 ``agent名:标签``；
    还可以是草稿（取已保存的内容）：草稿 id ``drf_…`` 或 ``agent名:@草稿名``。"""
    return await ctx.svc(VersionService).diff(left, right)


@router.get("/versions/{version_ref}", response_model=VersionOut)
async def get_version(version_ref: str, ctx: AppContext = Depends(get_ctx)):
    """版本详情（含标签、固化后的 Spec、引用的资产）。"""
    return await ctx.svc(VersionService).get(version_ref)


@router.get("/versions/{version_ref}/assets", response_model=VersionAssetsOut)
async def get_version_assets(version_ref: str, ctx: AppContext = Depends(get_ctx)):
    """版本绑定的资产清单：所用修订、来源（origin），以及每个资产最近一次变化发生在哪个版本。"""
    return await ctx.svc(VersionService).assets(version_ref)


@router.post("/versions/{version_ref}/tags", response_model=TagOut)
async def set_tag(version_ref: str, body: TagIn, ctx: AppContext = Depends(get_ctx)):
    """把标签指向这个版本（UPSERT：同一个标签会从旧版本移过来）。"""
    return await ctx.svc(VersionService).set_tag(version_ref, body.tag)


@router.delete("/versions/{version_ref}/tags/{tag}", status_code=204)
async def delete_tag(version_ref: str, tag: str, ctx: AppContext = Depends(get_ctx)):
    """删除版本上的标签。"""
    await ctx.svc(VersionService).delete_tag(version_ref, tag)
    return Response(status_code=204)


@router.post("/versions/{version_ref}/to-draft", response_model=DraftWrapper)
async def version_to_draft(version_ref: str, body: ToDraftIn, ctx: AppContext = Depends(get_ctx)):
    """用该版本的 Spec 覆盖草稿（或新建草稿），@rev 保持原样，base_version_id 设为该版本。"""
    return DraftWrapper(draft=await ctx.svc(VersionService).to_draft(version_ref, body.draft_name))


@router.post("/versions/{version_ref}/milestone", response_model=VersionSummary)
async def set_milestone(version_ref: str, body: MilestoneIn, ctx: AppContext = Depends(get_ctx)):
    """设为阶段性成果（里程碑），并把此前的过程版本归档到它名下。

    ``version_ids`` 缺省 = 归档此前所有未归档、非里程碑的版本；给出时只归档所列版本。归档只影响列表展示。
    """
    return await ctx.svc(VersionService).set_milestone(
        version_ref,
        title=body.title,
        set_title="title" in body.model_fields_set,
        version_ids=body.version_ids,
    )


@router.delete("/versions/{version_ref}/milestone", response_model=VersionSummary)
async def remove_milestone(version_ref: str, ctx: AppContext = Depends(get_ctx)):
    """取消阶段性成果：其下归档的版本全部移出归档。"""
    return await ctx.svc(VersionService).remove_milestone(version_ref)


@router.post("/versions/{version_ref}/unarchive", response_model=VersionSummary)
async def unarchive_version(version_ref: str, ctx: AppContext = Depends(get_ctx)):
    """把版本移出归档；对里程碑调用则把它名下的版本全部移出归档。"""
    return await ctx.svc(VersionService).unarchive(version_ref)
