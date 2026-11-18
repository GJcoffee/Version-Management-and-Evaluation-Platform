"""观测密钥连接 API；响应不回显账号密钥。"""

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.runtime.trace_credentials import KeyConnectionService
from evalp.schemas.key_connection import KeyConnectionCreate, KeyConnectionUpdate

router = APIRouter(prefix="/adapters/key-connections", tags=["key-connections"])


@router.get("")
async def connections(ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(KeyConnectionService).list()


@router.post("", status_code=201)
async def create(body: KeyConnectionCreate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(KeyConnectionService).create(body)


@router.put("/{connection_id}")
async def update(connection_id: str, body: KeyConnectionUpdate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(KeyConnectionService).update(connection_id, body)


@router.post("/{connection_id}/check")
async def check(connection_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(KeyConnectionService).check(connection_id)
