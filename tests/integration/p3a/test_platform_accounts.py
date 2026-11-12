"""模拟真实登录协议，验证恢复、隔离、不可变修订与账号 API。"""

import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.trace import TraceOnlyTarget, collect_trace
from evalp.runtime.credentials import CredentialsManager
from evalp.runtime.hiagent_auth import AccountAuthError, csrf_token, dump_cookies, load_cookies
from evalp.schemas.account import AccountCreate, AccountSessionImport, AccountUpdate
from evalp.store.models import PlatformAccount, PlatformAccountSession
from sqlalchemy import select

ORIGIN = "https://accounts.example"
PASSWORD = "private-account-password"


def decrypt(value):
    raw = base64.b64decode(value.removeprefix("$aes$"))
    assert raw[:8] == b"Salted__"
    salt, previous, key = raw[8:16], b"", b""
    while len(key) < 48:
        previous = hashlib.md5(previous + b"bytedance.eps.ed" + salt).digest()
        key += previous
    cipher = Cipher(algorithms.AES(key[:32]), modes.CBC(key[32:48])).decryptor()
    clear = cipher.update(raw[16:]) + cipher.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(clear) + unpadder.finalize()).decode()


async def create(ctx):
    manager = ctx.svc(CredentialsManager)
    manager.auth.recognize = AsyncMock(return_value="Ab12")
    account = await manager.create(AccountCreate(
        name="Trace 测试账号", origin=ORIGIN, username="test-user",
        password=PASSWORD, tenant_name="test-tenant",
    ))
    return manager, account["id"]


def upstream(router):
    state = {"valid": True, "logins": 0, "checks": 0}
    router.get(ORIGIN + "/").respond(200, headers={"set-cookie": "x-csrf-token=csrf; Path=/; Secure"})

    def check(request):
        state["checks"] += 1
        assert request.headers["x-csrf-token"] == "csrf"
        authenticated = "session=fresh" in request.headers.get("cookie", "") and state["valid"]
        return httpx.Response(200, json={
            "Result": {"ID": "u", "UserName": "test-user"} if authenticated else {},
        })

    def login(request):
        body = json.loads(request.content)
        assert body["UserName"] == "test-user" and body["TenantName"] == "test-tenant"
        assert decrypt(body["Password"]) in (PASSWORD, "new-password")
        assert json.loads(decrypt(body["Captcha"])) == {"Captcha": "Ab12", "Id": "captcha-id"}
        state["logins"] += 1
        state["valid"] = True
        return httpx.Response(
            200, json={"Result": {}}, headers={"set-cookie": "session=fresh; Path=/; Secure"},
        )

    router.get(ORIGIN + "/api/auth/checkLogin").mock(side_effect=check)
    router.post(ORIGIN + "/api/iam").respond(
        200, content=b"\x89PNG\r\n\x1a\nfake", headers={"id": "captcha-id"},
    )
    route = router.post(ORIGIN + "/api/auth/login").mock(side_effect=login)
    return state, route


@respx.mock
async def test_concurrent_initial_queries_login_once_and_encrypt_history(ctx):
    manager, aid = await create(ctx)
    state, _ = upstream(respx)
    respx.post(ORIGIN + "/api/observe").respond(200, json={"Result": {"TraceID": "t"}})
    results = await asyncio.gather(*[
        manager.request(aid, ORIGIN, params={"Action": "GetTraceInfo"}, body={"MessageID": str(n)})
        for n in range(12)
    ])
    assert state["logins"] == 1
    assert {audit["session_revision"] for _, audit in results} == {1}
    row = await manager.account(aid)
    assert row.status == "active"
    async with ctx.db.read() as s:
        revision = await s.get(PlatformAccountSession, (aid, 1))
    original = await ctx.secrets.get(revision.secret_ref)
    assert "session" in original
    public = json.dumps([await manager.list(), await manager.revisions(aid)])
    assert PASSWORD not in public and "csrf" not in public and "fresh" not in public
    assert PASSWORD.encode() not in ctx.config.db_path.read_bytes()
    assert PASSWORD.encode() not in ctx.config.secrets_path.read_bytes()


@respx.mock
async def test_expired_cookie_recovers_once_and_keeps_old_revision(ctx):
    manager, aid = await create(ctx)
    state, _ = upstream(respx)
    await manager.ensure(aid, ORIGIN)
    async with ctx.db.read() as s:
        revision = await s.get(PlatformAccountSession, (aid, 1))
    old_value = await ctx.secrets.get(revision.secret_ref)
    state["valid"] = False
    # 新登录态刻意与旧 Cookie 相同；恢复也必须重放一次原查询。
    calls = []
    def observe(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200 if state["valid"] else 401, json={"Result": {"TraceID": "t"}})

    respx.post(ORIGIN + "/api/observe").mock(side_effect=observe)
    response, audit = await manager.request(
        aid, ORIGIN, params={"Action": "GetTraceInfo"}, body={"MessageID": "m"},
    )
    assert response.status_code == 200
    assert calls == [{"MessageID": "m"}, {"MessageID": "m"}]
    assert state["logins"] == 2
    assert audit["session_revision"] == 2
    assert await ctx.secrets.get(revision.secret_ref) == old_value


@respx.mock
async def test_permission_denied_does_not_relogin_or_repeat(ctx):
    manager, aid = await create(ctx)
    state, _ = upstream(respx)
    await manager.ensure(aid, ORIGIN)
    observe = respx.post(ORIGIN + "/api/observe").respond(403, json={})
    response, _ = await manager.request(aid, ORIGIN, params={}, body={"MessageID": "m"})
    assert response.status_code == 403 and observe.call_count == 1 and state["logins"] == 1


@respx.mock
async def test_password_failure_cools_down_and_manual_session_restores(ctx):
    manager, aid = await create(ctx)
    state, login = upstream(respx)
    login.respond(400, json={"ResponseMetadata": {"Error": {
        "Code": "InvalidPassword", "Message": f"error reflecting {PASSWORD}",
    }}})
    account = await manager.check(aid)
    assert account["status"] == "login_failed" and PASSWORD not in account["last_error"]
    with pytest.raises(AccountAuthError):
        await manager.ensure(aid, ORIGIN)
    assert login.call_count == 1
    result = await manager.import_session(aid, AccountSessionImport(
        headers=json.dumps({"Cookie": "session=fresh", "X-Csrf-Token": "csrf"}),
    ))
    assert result["status"] == "active" and state["logins"] == 0
    assert (await manager.revisions(aid))[0]["source"] == "manual"


@respx.mock
async def test_network_failure_does_not_classify_expiry_or_relogin(ctx):
    manager, aid = await create(ctx)
    state, login = upstream(respx)
    await manager.ensure(aid, ORIGIN)
    respx.get(ORIGIN + "/api/auth/checkLogin").mock(side_effect=httpx.ConnectError("private network details"))
    account = await manager.check(aid)
    assert account["status"] == "check_failed" and "private" not in account["last_error"]
    assert login.call_count == state["logins"] == 1
    assert len(await manager.revisions(aid)) == 1


@respx.mock
async def test_captcha_retries_are_bounded_and_unsupported_verification_stops(ctx):
    manager, aid = await create(ctx)
    _, login = upstream(respx)
    login.respond(400, json={"ResponseMetadata": {"Error": {"Code": "InvalidCaptcha"}}})
    account = await manager.check(aid)
    assert account["status"] == "verification_required"
    assert login.call_count == 3
    login.respond(400, json={"ResponseMetadata": {"Error": {"Code": "MFARequired"}}})
    await manager.check(aid, login=True)
    assert login.call_count == 4


@respx.mock
async def test_cancelled_check_releases_account_lock_and_can_recover(ctx):
    manager, aid = await create(ctx)
    started = asyncio.Event()
    async def blocked(request):
        started.set()
        await asyncio.Event().wait()

    upstream(respx)
    route = respx.get(ORIGIN + "/api/auth/checkLogin").mock(side_effect=blocked)
    task = asyncio.create_task(manager.check(aid))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager.lock(aid).locked()
    route.respond(200, json={"Result": {"ID": "u", "UserName": "test-user"}})
    assert (await manager.check(aid))["status"] == "active"


@respx.mock
async def test_background_probe_checks_due_account_and_stops_cleanly(ctx):
    manager, aid = await create(ctx)
    state, _ = upstream(respx)
    done = asyncio.Event()
    save = manager._save
    async def saved(*args, **kwargs):
        result = await save(*args, **kwargs)
        done.set()
        return result

    manager._save = saved
    manager.start()
    await asyncio.wait_for(done.wait(), timeout=5)
    await manager.aclose()
    assert state["logins"] == 1 and manager._task is None
    assert (await manager.account(aid)).next_check_at is not None


@respx.mock
async def test_cookie_updated_by_observe_has_new_immutable_revision(ctx):
    manager, aid = await create(ctx)
    upstream(respx)
    respx.post(ORIGIN + "/api/observe").respond(
        200, json={"Result": {}}, headers={"set-cookie": "session=rotated; Path=/; Secure; HttpOnly"},
    )
    _, audit = await manager.request(aid, ORIGIN, params={}, body={})
    assert audit["session_revision"] == 1 and audit["latest_session_revision"] == 2
    async with ctx.db.read() as s:
        first = await s.get(PlatformAccountSession, (aid, 1))
        second = await s.get(PlatformAccountSession, (aid, 2))
    assert "fresh" in await ctx.secrets.get(first.secret_ref)
    assert "rotated" in await ctx.secrets.get(second.secret_ref)


@respx.mock
async def test_password_change_invalidates_session_and_blank_preserves_password(ctx):
    manager, aid = await create(ctx)
    state, _ = upstream(respx)
    await manager.ensure(aid, ORIGIN)
    original = (await manager.account(aid)).password_secret_ref
    await manager.update(aid, AccountUpdate(expected_revision=1, name="renamed", password=""))
    assert (await manager.account(aid)).password_secret_ref == original
    await manager.update(aid, AccountUpdate(expected_revision=2, password="new-password"))
    await manager.ensure(aid, ORIGIN)
    assert state["logins"] == 2
    assert len(await manager.revisions(aid)) == 2
    assert await ctx.secrets.get(original) == PASSWORD
    await manager.update(aid, AccountUpdate(expected_revision=3, enabled=False))
    with pytest.raises(AccountAuthError, match="停用"):
        await manager.ensure(aid, ORIGIN)


@respx.mock
async def test_origin_mismatch_rejects_before_network(ctx):
    manager, aid = await create(ctx)
    with pytest.raises(AccountAuthError, match="不匹配"):
        await manager.ensure(aid, "https://other.example")
    assert len(respx.calls) == 0


@respx.mock
async def test_authenticated_trace_uses_account_and_audits_actual_revision(ctx):
    manager, aid = await create(ctx)
    upstream(respx)
    def observe(request):
        assert "session=fresh" in request.headers["cookie"]
        assert "wrong-old-cookie" not in request.headers["cookie"]
        action = request.url.params["Action"]
        result = ({"TraceID": "tid", "EndTime": "2026-11-11T00:00:01Z"} if action == "GetTraceInfo"
                  else {"Items": [], "HasMore": False, "Total": 0})
        return httpx.Response(200, json={"Result": result})

    respx.post(ORIGIN + "/api/observe").mock(side_effect=observe)
    config = HiAgentConfig(
        base_url=ORIGIN, app_id="a", user_id="u", published_version="1",
        observe_base_url=ORIGIN, observe_account_ref=aid, observation_wait_s=5,
    )
    events = [event async for event in collect_trace(
        config, {"observe_headers": {"Cookie": "wrong-old-cookie"}},
        TraceOnlyTarget("m"), credentials=manager,
    )]
    metrics = next(event.payload for event in events if event.type == "metrics")
    assert metrics["trace_status"] == "partial"
    assert metrics["trace_auth"]["account_ref"] == aid and metrics["trace_auth"]["session_revision"] == 1


async def test_account_api_hides_secrets_and_rejects_stale_changes(client, ctx):
    # fixture lifespan 已启动管理器；停止后台探活，显式 check 由协议模拟测试覆盖。
    await ctx.svc(CredentialsManager).aclose()
    body = {"name": "account", "origin": ORIGIN + "/", "username": "user", "password": PASSWORD}
    response = await client.post("/api/v1/adapters/accounts", json=body)
    assert response.status_code == 201
    row = response.json()
    assert row["origin"] == ORIGIN and row["config_revision"] == 1
    assert "password" not in response.text and "secret_ref" not in response.text
    listed = await client.get("/api/v1/adapters/accounts")
    assert listed.json() == [row]
    duplicate = await client.post("/api/v1/adapters/accounts", json=body)
    assert duplicate.status_code == 409
    updated = await client.put(f"/api/v1/adapters/accounts/{row['id']}", json={
        "expected_revision": 1, "enabled": False, "password": "",
    })
    assert updated.json()["status"] == "disabled"
    stale = await client.put(
        f"/api/v1/adapters/accounts/{row['id']}", json={"expected_revision": 1, "name": "stale"},
    )
    assert stale.status_code == 409
    invalid = await client.post("/api/v1/adapters/accounts", json={**body, "origin": ORIGIN + "/path"})
    assert invalid.status_code == 400 and PASSWORD not in invalid.text
    async with ctx.db.read() as s:
        assert len((await s.execute(select(PlatformAccount))).scalars().all()) == 1


def test_cookie_roundtrip_and_csrf_path_domain_expiry():
    cookies = httpx.Cookies()
    cookies.set("x-csrf-token", "other", domain="other.example")
    cookies.set("x-csrf-token", "root", domain="accounts.example", path="/")
    cookies.set("x-csrf-token", "observe", domain="accounts.example", path="/api/observe")
    restored = load_cookies(dump_cookies(cookies))
    assert csrf_token(restored, ORIGIN + "/api/observe") == "observe"
    assert csrf_token(restored, ORIGIN + "/api/observers") == "root"
    assert csrf_token(restored, "https://unrelated.example/api/observe") is None


async def test_refresh_audits_account_revision_and_preserves_original_execution(world, client):
    from tests.integration.adapter_env import configure_production
    from tests.integration.p3a.test_hiagent_adapter import (
        OBSERVE,
        TRACE,
        llm_span,
        make_session,
        mock_upstream,
        observe,
    )

    await world.ctx.svc(CredentialsManager).aclose()
    _, session = await make_session(world, client)
    with respx.mock() as router:
        mock_upstream(router)
        router.post(OBSERVE).respond(403)
        turn = await world.sessions.run_turn(session.id, "查订单", wait=True)
        before = await world.sessions.wait_trace(turn.id)
    snapshot = (await world.sessions.get_session(session.id)).adapter_snapshot
    manager, aid = await create(world.ctx)
    binding = {**snapshot["binding"], "config": {
        **snapshot["binding"]["config"], "observe_base_url": ORIGIN, "observe_account_ref": aid,
    }}
    await configure_production(client, binding)
    with respx.mock() as router:
        upstream(router)
        router.post(ORIGIN + "/api/observe").mock(side_effect=observe(
            TRACE, [llm_span("s", input_tokens=9, output_tokens=3)],
        ))
        response = await client.post(f"/api/v1/turns/{turn.id}/trace/refresh")
    assert response.status_code == 200
    output = response.json()
    assert output["usage"]["trace_status"] == "complete"
    assert output["usage"]["trace_refresh"]["trace_auth"]["account_ref"] == aid
    assert output["usage"]["trace_refresh"]["trace_auth"]["session_revision"] == 1
    assert output["status"] == before.status
    assert output["output"] == before.output.model_dump(mode="json")
    assert (await world.sessions.get_session(session.id)).adapter_snapshot == snapshot
