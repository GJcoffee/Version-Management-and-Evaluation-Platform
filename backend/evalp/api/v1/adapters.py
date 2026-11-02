"""适配器注册项、凭据引用，以及生产 / 测试两套接入配置。"""

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.runtime.adapters.registry import list_adapters
from evalp.schemas.adapter import Environment
from evalp.services.adapter import (
    AdapterService,
    CredentialCreate,
    EnvironmentPush,
    EnvironmentRollback,
    EnvironmentUpdate,
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


@router.get("/adapter-environments")
async def environments(ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).get()


@router.get("/adapter-environments/{name}/revisions")
async def revisions(name: Environment, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).revisions(name)


@router.put("/adapter-environments/test")
async def update_test(body: EnvironmentUpdate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).update_test(body)


@router.post("/adapter-environments/production/push")
async def push_to_production(body: EnvironmentPush, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).push(body)


@router.post("/adapter-environments/production/rollback")
async def rollback_production(body: EnvironmentRollback, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).rollback(body)
