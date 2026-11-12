"""账号凭据管理：持久化状态、不可变 Cookie 修订、串行恢复与定期探活。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from evalp.context import AppContext
from evalp.errors import Conflict, NotFound, ValidationFailed
from evalp.ids import new_id
from evalp.runtime.hiagent_auth import (
    AccountAuthError,
    HiAgentAuth,
    dump_cookies,
    load_cookies,
)
from evalp.schemas.account import AccountCreate, AccountSessionImport, AccountUpdate, normalize_origin
from evalp.store.models import PlatformAccount, PlatformAccountSession
from evalp.utils.time import now_iso, to_iso


class CredentialsManager:
    close_order = -20

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.auth = HiAgentAuth()
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    @staticmethod
    def output(row: PlatformAccount) -> dict[str, Any]:
        return {key: getattr(row, key) for key in (
            "id", "adapter_id", "name", "origin", "tenant_name", "username",
            "config_revision", "session_revision", "enabled", "status",
            "last_checked_at", "last_error", "next_check_at", "created_at", "updated_at",
        )}

    async def account(self, account_id: str, origin: str | None = None) -> PlatformAccount:
        async with self.ctx.db.read() as s:
            row = await s.get(PlatformAccount, account_id)
        if row is None:
            raise NotFound("账号不存在")
        if origin is not None:
            try:
                matches = normalize_origin(origin) == row.origin
            except ValueError:
                matches = False
            if row.adapter_id != "hiagent" or not matches:
                raise AccountAuthError("configuration_error", "Trace 账号与观测站点不匹配")
            if not row.enabled:
                raise AccountAuthError("disabled", "Trace 账号已停用，请在设置中启用或选择其他账号")
        return row

    async def list(self) -> list[dict]:
        async with self.ctx.db.read() as s:
            rows = (await s.execute(
                select(PlatformAccount).order_by(PlatformAccount.created_at.desc()),
            )).scalars()
            return [self.output(row) for row in rows]

    async def create(self, body: AccountCreate) -> dict:
        if body.adapter_id != "hiagent":
            raise ValidationFailed("该平台尚未提供自动登录协议")
        if not body.name or not body.username:
            raise ValidationFailed("账号名称和用户名不能为空")
        account_id, stamp = new_id("acc"), now_iso()
        ref = f"account:{account_id}:password:1"
        await self.ctx.secrets.set(ref, body.password.get_secret_value())
        try:
            async with self.ctx.db.write() as s:
                row = PlatformAccount(
                    id=account_id, adapter_id=body.adapter_id, name=body.name, origin=body.origin,
                    tenant_name=body.tenant_name, username=body.username, password_secret_ref=ref,
                    config_revision=1, session_revision=0, enabled=True, status="pending",
                    next_check_at=stamp, created_at=stamp, updated_at=stamp,
                )
                s.add(row)
        except BaseException as exc:
            await self.ctx.secrets.delete(ref)
            if isinstance(exc, IntegrityError):
                raise Conflict("该站点、租户和用户名的账号已存在") from None
            raise
        self._wake.set()
        return self.output(row)

    async def update(self, account_id: str, body: AccountUpdate) -> dict:
        async with self.lock(account_id):
            account = await self.account(account_id)
            if body.expected_revision != account.config_revision:
                raise Conflict("账号配置已更新，请重新加载")
            password = body.password.get_secret_value() if body.password else ""
            ref = f"account:{account_id}:password:{account.config_revision + 1}"
            if password:
                await self.ctx.secrets.set(ref, password)
            try:
                async with self.ctx.db.write() as s:
                    row = await s.get(PlatformAccount, account_id)
                    assert row is not None
                    if body.name is not None:
                        if not body.name.strip():
                            raise ValidationFailed("账号名称不能为空")
                        row.name = body.name.strip()
                    if body.enabled is not None:
                        row.enabled = body.enabled
                    row.config_revision += 1
                    row.updated_at = now_iso()
                    if password:
                        row.password_secret_ref = ref
                        # 修订号保持单调，旧登录态因 config_revision 不匹配不再使用。
                        row.status, row.last_error = "pending", None
                    elif row.enabled != account.enabled:
                        row.status, row.last_error = ("pending" if row.enabled else "disabled"), None
                    row.next_check_at = now_iso() if row.enabled else None
                    if not row.enabled:
                        row.status = "disabled"
            except BaseException:
                if password:
                    await self.ctx.secrets.delete(ref)
                raise
        self._wake.set()
        return self.output(row)

    async def revisions(self, account_id: str) -> list[dict]:
        account = await self.account(account_id)
        async with self.ctx.db.read() as s:
            rows = (await s.execute(select(PlatformAccountSession).where(
                PlatformAccountSession.account_id == account_id,
            ).order_by(PlatformAccountSession.revision.desc()))).scalars()
            return [{
                "revision": row.revision, "config_revision": row.config_revision,
                "expires_at": row.expires_at, "source": row.source, "created_at": row.created_at,
                "current": row.revision == account.session_revision and account.status == "active",
            } for row in rows]

    async def _client(self, row: PlatformAccount) -> httpx.AsyncClient:
        async with self.ctx.db.read() as s:
            session = await s.get(PlatformAccountSession, (row.id, row.session_revision))
        # 名称/启停不会使密码变更；以密码引用里的修订确定可用性。
        password_revision = int(row.password_secret_ref.rsplit(":", 1)[1])
        value = (await self.ctx.secrets.get(session.secret_ref)
                 if session and session.config_revision >= password_revision else None)
        return httpx.AsyncClient(cookies=load_cookies(value), timeout=15, follow_redirects=False)

    async def _save(self, account_id: str, client: httpx.AsyncClient, source: str,
                    *, checked: bool = False) -> int:
        client.cookies.jar.clear_expired_cookies()
        value = dump_cookies(client.cookies)
        async with self.ctx.db.read() as s:
            row = await s.get(PlatformAccount, account_id)
            assert row is not None
            previous = await s.get(PlatformAccountSession, (account_id, row.session_revision))
            maximum = (await s.execute(select(func.max(PlatformAccountSession.revision)).where(
                PlatformAccountSession.account_id == account_id,
            ))).scalar() or 0
        same = (source not in ("login", "manual") and previous is not None
                and previous.config_revision == row.config_revision
                and await self.ctx.secrets.get(previous.secret_ref) == value)
        revision = row.session_revision if same else maximum + 1
        ref = f"account:{account_id}:session:{revision}"
        expires = [cookie.expires for cookie in client.cookies.jar if cookie.expires]
        expires_at = to_iso(datetime.fromtimestamp(min(expires), UTC)) if expires else None
        next_check = datetime.now(UTC) + timedelta(minutes=5)
        if expires_at:
            next_check = min(next_check, datetime.fromtimestamp(min(expires), UTC) - timedelta(seconds=60))
        next_check = max(next_check, datetime.now(UTC) + timedelta(seconds=30))
        if not same:
            await self.ctx.secrets.set(ref, value)
        try:
            async with self.ctx.db.write() as s:
                account = await s.get(PlatformAccount, account_id)
                assert account is not None
                if not same:
                    s.add(PlatformAccountSession(
                        account_id=account_id, revision=revision, config_revision=account.config_revision,
                        secret_ref=ref, expires_at=expires_at, source=source, created_at=now_iso(),
                    ))
                account.session_revision = revision
                account.status, account.last_error = "active", None
                if checked:
                    account.last_checked_at = now_iso()
                    account.next_check_at = to_iso(next_check)
                account.updated_at = now_iso()
        except BaseException:
            if not same:
                await self.ctx.secrets.delete(ref)
            raise
        return revision

    async def _failure(self, account_id: str, exc: AccountAuthError) -> None:
        async with self.ctx.db.write() as s:
            row = await s.get(PlatformAccount, account_id)
            if row:
                row.status, row.last_error, row.last_checked_at = exc.status, str(exc), now_iso()
                # 网络错误冷却后再探活；密码/额外验证由用户处理，防止锁号或重复触发验证。
                row.next_check_at = (
                    to_iso(datetime.now(UTC) + timedelta(seconds=60))
                    if exc.status == "check_failed" else None
                )
                row.updated_at = now_iso()

    async def _ensure(self, row: PlatformAccount, client: httpx.AsyncClient,
                      *, force: bool = False, login: bool = False) -> int:
        if not row.enabled:
            raise AccountAuthError("disabled", "Trace 账号已停用")
        if not force and row.status in ("login_failed", "verification_required"):
            raise AccountAuthError(row.status, row.last_error or "账号需要处理，请在设置中检查")
        if not force and row.next_check_at and row.next_check_at > now_iso():
            if row.status == "check_failed":
                raise AccountAuthError(row.status, row.last_error or "账号暂不可用，请在设置中检查")
            if row.status == "active":
                return row.session_revision
        try:
            async with asyncio.timeout(60):
                valid = False if login else await self.auth.check(client, row.origin, row.username)
                source = "check"
                if not valid:
                    password = await self.ctx.secrets.get(row.password_secret_ref)
                    if not password:
                        raise AccountAuthError("login_failed", "账号密码不可用，请重新填写")
                    await self.auth.login(client, row.origin, row.username, password, row.tenant_name)
                    source = "login"
                return await self._save(row.id, client, source, checked=True)
        except AccountAuthError as exc:
            await self._failure(row.id, exc)
            raise
        except (httpx.HTTPError, ValueError, TypeError, TimeoutError, ImportError):
            exc = AccountAuthError("check_failed", "认证检查未完成，请检查网络或稍后重试")
            await self._failure(row.id, exc)
            raise exc from None

    async def check(self, account_id: str, *, login: bool = False) -> dict:
        async with self.lock(account_id):
            row = await self.account(account_id)
            try:
                async with await self._client(row) as client:
                    await self._ensure(row, client, force=True, login=login)
            except AccountAuthError:
                pass
            return self.output(await self.account(account_id))

    async def ensure(self, account_id: str, origin: str) -> None:
        async with self.lock(account_id):
            row = await self.account(account_id, origin)
            async with await self._client(row) as client:
                await self._ensure(row, client)

    async def request(self, account_id: str, origin: str, *, params: dict, body: dict) -> tuple[
        httpx.Response, dict,
    ]:
        """只读 Observe；认证失败后探活，确认过期才重登，最多重放一次原请求。"""
        async with self.lock(account_id):
            row = await self.account(account_id, origin)
            async with await self._client(row) as client:
                revision = await self._ensure(row, client)
                async def send() -> httpx.Response:
                    return await client.post(origin + "/api/observe", params=params, json=body,
                                             headers=await self.auth.headers(client, origin, "/api/observe"))

                response = await send()
                if self.auth.authentication_failed(response):
                    row = await self.account(account_id, origin)
                    before = revision
                    revision = await self._ensure(row, client, force=True)
                    # 会话仍有效的 403 是权限问题；CSRF/Cookie 确有更新才尝试一次。
                    if revision != before:
                        response = await send()
                latest = await self._save(account_id, client, "response")
                return response, {
                    "account_ref": account_id, "config_revision": row.config_revision,
                    "session_revision": revision, "latest_session_revision": latest,
                }

    async def import_session(self, account_id: str, body: AccountSessionImport) -> dict:
        async with self.lock(account_id):
            row = await self.account(account_id)
            if not row.enabled:
                raise ValidationFailed("请先启用账号")
            try:
                headers = json.loads(body.headers.get_secret_value())
                normalized = {key.lower(): value for key, value in headers.items()}
                if not all(isinstance(value, str) for value in normalized.values()):
                    raise ValueError
                cookie = SimpleCookie()
                cookie.load(normalized.get("cookie", ""))
                if not cookie:
                    raise ValueError
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    host = httpx.URL(row.origin).host
                    for key, morsel in cookie.items():
                        client.cookies.set(key, morsel.value, domain=host, path="/")
                    if normalized.get("x-csrf-token"):
                        client.cookies.set("x-csrf-token", normalized["x-csrf-token"], domain=host, path="/")
                    if not await self.auth.check(client, row.origin, row.username):
                        raise AccountAuthError("verification_required", "补充的登录态无效，请重新登录站点")
                    await self._save(account_id, client, "manual", checked=True)
            except AccountAuthError as exc:
                raise ValidationFailed(str(exc)) from None
            except (ValueError, TypeError, AttributeError, CookieError, httpx.HTTPError):
                raise ValidationFailed("登录态未通过检查，请提供当前站点的 Cookie／CSRF JSON") from None
            return self.output(await self.account(account_id))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="credentials-probe")

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                async with self.ctx.db.read() as s:
                    ids = (await s.execute(select(PlatformAccount.id).where(
                        PlatformAccount.enabled.is_(True),
                        PlatformAccount.next_check_at <= now_iso(),
                    ))).scalars().all()
                for account_id in ids:
                    # ensure() 在锁内再次读状态；其他请求刚探活则不重复执行。
                    row = await self.account(account_id)
                    with contextlib.suppress(AccountAuthError):
                        await self.ensure(account_id, row.origin)
            except Exception:  # noqa: BLE001 - 单次探活失败不能中止后续账号检查
                pass
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except TimeoutError:
                pass

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
