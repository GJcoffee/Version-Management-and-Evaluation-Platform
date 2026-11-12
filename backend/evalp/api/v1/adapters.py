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
from evalp.services.adapter_migration import AdapterMigrationService, BindingMigrationRequest
from evalp.spec.models import AdapterBinding

router = APIRouter(tags=["adapters"])


@router.post("/adapters/binding-migrations")
async def migrate_bindings(body: BindingMigrationRequest, ctx: AppContext = Depends(get_ctx)):
    """显式迁移历史接入，支持干跑与独立审计。"""
    return await ctx.svc(AdapterMigrationService).migrate(body)


@router.get("/adapters/binding-migrations/{migration_id}")
async def binding_migration(migration_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterMigrationService).get(migration_id)


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
    """检查配置、凭据与只读上游认证，不执行 LLM 请求。"""
    from evalp.runtime.adapters.registry import resolve

    binding = await ctx.svc(AdapterService).validate(body)
    _, adapter_cls = resolve(binding)
    adapter = adapter_cls(binding, ctx)
    try:
        return await adapter.healthcheck()
    finally:
        await adapter.aclose()


@router.get("/adapter-environments")
async def environments(adapter: str = "hiagent", ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).get(adapter)


@router.get("/adapter-environments/{name}/revisions")
async def revisions(name: Environment, adapter: str = "hiagent", ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).revisions(name, adapter)


@router.put("/adapter-environments/test")
async def update_test(body: EnvironmentUpdate, adapter: str = "hiagent", ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(AdapterService).update_test(body, adapter)


@router.post("/adapter-environments/production/push")
async def push_to_production(
    body: EnvironmentPush, adapter: str = "hiagent", ctx: AppContext = Depends(get_ctx),
):
    return await ctx.svc(AdapterService).push(body, adapter)


@router.post("/adapter-environments/production/rollback")
async def rollback_production(
    body: EnvironmentRollback, adapter: str = "hiagent", ctx: AppContext = Depends(get_ctx),
):
    return await ctx.svc(AdapterService).rollback(body, adapter)
