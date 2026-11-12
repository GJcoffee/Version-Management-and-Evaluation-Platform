"""账号管理接口；密码与登录态均不可读回。"""

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.runtime.credentials import CredentialsManager
from evalp.schemas.account import AccountCreate, AccountSessionImport, AccountUpdate

router = APIRouter(prefix="/adapters/accounts", tags=["accounts"])


@router.get("")
async def accounts(ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).list()


@router.post("", status_code=201)
async def create(body: AccountCreate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).create(body)


@router.put("/{account_id}")
async def update(account_id: str, body: AccountUpdate, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).update(account_id, body)


@router.post("/{account_id}/check")
async def check(account_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).check(account_id)


@router.post("/{account_id}/login")
async def login(account_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).check(account_id, login=True)


@router.post("/{account_id}/session")
async def import_session(account_id: str, body: AccountSessionImport, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).import_session(account_id, body)


@router.get("/{account_id}/revisions")
async def revisions(account_id: str, ctx: AppContext = Depends(get_ctx)):
    return await ctx.svc(CredentialsManager).revisions(account_id)
