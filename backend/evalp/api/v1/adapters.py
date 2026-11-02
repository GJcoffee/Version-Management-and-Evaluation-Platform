"""适配器注册项、凭据引用、版本覆盖与恢复。"""

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.runtime.adapters.registry import list_adapters
from evalp.services.adapter import (
    AdapterService,
    BindingBatch,
    BindingRollback,
    BindingUpdate,
    CredentialCreate,
)
from evalp.spec.models import AdapterBinding

router = APIRouter(tags=["adapters"])


@router.get("/adapters")
async def adapters():
    return list_adapters()


@router.get("/adapters/credentials")
async def credentials(ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).credentials()


@router.post("/adapters/credentials", status_code=201)
async def create_credential(body: CredentialCreate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).create_credential(body)


@router.post("/adapters/check-connection")
async def check_connection(body: AdapterBinding, ctx: AppContext = Depends(get_ctx)):
    """只检查配置与凭据，不自动执行 LLM 请求。"""
    from evalp.runtime.adapters.hiagent_adapter import HiAgentAdapter

    binding = await ctx.svc(AdapterService).validate(body)
    return await HiAgentAdapter(binding, ctx).healthcheck()


@router.post("/adapters/bindings/batch")
async def batch(body: BindingBatch, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).batch(body)


@router.get("/versions/{ref}/adapter-binding")
async def get_binding(ref: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).get(ref)


@router.put("/versions/{ref}/adapter-binding")
async def update_binding(ref: str, body: BindingUpdate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).update(ref, body)


@router.post("/versions/{ref}/adapter-binding/rollback")
async def rollback_binding(ref: str, body: BindingRollback, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).rollback(ref, body)
