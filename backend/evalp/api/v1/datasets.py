"""数据集接口：/api/v1/datasets。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.schemas.asset import CaseFilter, CaseOut, DatasetImport, DatasetImportOut
from evalp.services.dataset import DatasetService

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("/import", response_model=DatasetImportOut)
async def import_dataset(body: DatasetImport, response: Response, ctx: AppContext = Depends(get_ctx)):
    """导入 JSONL / CSV（CSV 需要 column_map）。同名数据集已存在时新增修订；新建资产返回 201。"""
    out = await ctx.svc(DatasetService).import_dataset(body)
    response.status_code = 201 if out.created else 200
    return out


@router.get("/{rev_id}/cases", response_model=Page[CaseOut])
async def list_cases(
    rev_id: str,
    tag: list[str] = Query(default=[], description="含任一标签"),
    case_key: list[str] = Query(default=[]),
    q: str | None = Query(None, description="case_key 模糊匹配"),
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """数据集修订（arv_…）的用例，按 idx 升序分页。"""
    return await ctx.svc(DatasetService).page_cases(
        rev_id, CaseFilter(tags=tag, case_keys=case_key), q=q, limit=p.limit, cursor=p.cursor
    )
