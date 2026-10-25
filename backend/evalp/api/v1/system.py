"""系统接口：/api/v1/health、/settings、/system/gc。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.services.settings import SettingsService
from evalp.services.system import SystemService

router = APIRouter(tags=["system"])


class SettingsOut(BaseModel):
    values: dict[str, int]


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class GcOut(BaseModel):
    blobs_deleted: int
    bytes_freed: int


@router.get("/health")
async def health(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """健康检查：版本、数据库大小、队列、worker、各 Provider 状态。"""
    return await ctx.svc(SystemService).health()


@router.get("/settings", response_model=SettingsOut)
async def get_settings(ctx: AppContext = Depends(get_ctx)):
    return SettingsOut(values=await ctx.svc(SettingsService).get_all())


@router.patch("/settings", response_model=SettingsOut)
async def patch_settings(body: SettingsPatch, ctx: AppContext = Depends(get_ctx)):
    """部分更新设置，只接受已知 key 且带类型校验。"""
    return SettingsOut(values=await ctx.svc(SettingsService).update(body.values))


@router.post("/system/gc", response_model=GcOut)
async def system_gc(ctx: AppContext = Depends(get_ctx)):
    """执行 GC：删除 refcount=0 且超过 24 小时的 blob，并清理软删除资产的无引用修订。"""
    return await ctx.svc(SystemService).gc()
