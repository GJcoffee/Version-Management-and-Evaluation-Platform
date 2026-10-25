"""Provider 接口：/api/v1/providers。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.context import AppContext
from evalp.schemas.provider import ProviderCreate, ProviderOut, ProviderTestResult, ProviderUpdate
from evalp.services.provider import ProviderService

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=Page[ProviderOut])
async def list_providers(p: PageParams = Depends(page_params), ctx: AppContext = Depends(get_ctx)):
    """列出模型服务。"""
    return await ctx.svc(ProviderService).list(limit=p.limit, cursor=p.cursor)


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderCreate, ctx: AppContext = Depends(get_ctx)):
    """创建模型服务；api_key 只写入密钥存储，响应中不会返回。"""
    return await ctx.svc(ProviderService).create(body)


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(provider_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(ProviderService).get(provider_id)


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: str, body: ProviderUpdate, ctx: AppContext = Depends(get_ctx)):
    """部分更新；``api_key: ""`` 删除密钥。"""
    return await ctx.svc(ProviderService).update(provider_id, body)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, ctx: AppContext = Depends(get_ctx)):
    await ctx.svc(ProviderService).delete(provider_id)
    return Response(status_code=204)


@router.post("/{provider_id}/test", response_model=ProviderTestResult, response_model_exclude_none=True)
async def test_provider(provider_id: str, ctx: AppContext = Depends(get_ctx)):
    """调用 GET {base_url}/models 测试连通性。"""
    return await ctx.svc(ProviderService).test(provider_id)
