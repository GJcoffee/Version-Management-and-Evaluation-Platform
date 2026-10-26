import os
import stat

import httpx
from evalp.app import create_app
from evalp.auth import COOKIE_NAME, ensure_token


async def test_no_token_401(anon_client):
    r = await anon_client.get("/api/v1/health")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert set(r.json()["error"]) == {"code", "message", "details"}


async def test_wrong_token_401(anon_client):
    r = await anon_client.get("/api/v1/health", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    r = await anon_client.get("/api/v1/health", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    r = await anon_client.get("/api/v1/health", headers={"Cookie": f"{COOKIE_NAME}=nope"})
    assert r.status_code == 401


async def test_bearer_ok(client):
    assert (await client.get("/api/v1/health")).status_code == 200


async def test_cookie_ok(anon_client, token):
    r = await anon_client.get("/api/v1/health", headers={"Cookie": f"foo=bar; {COOKIE_NAME}={token}"})
    assert r.status_code == 200


async def test_unknown_api_path_requires_auth_first(anon_client, client):
    assert (await anon_client.get("/api/v1/nope")).status_code == 401
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_bootstrap_sets_cookie(anon_client, token):
    r = await anon_client.get("/auth/bootstrap")  # 无需鉴权
    assert r.status_code == 200 and r.json() == {"ok": True}
    header = r.headers["set-cookie"]
    assert f"{COOKIE_NAME}={token}" in header
    low = header.lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    # httpx 保存 cookie 后，无需 Authorization 头即可访问
    assert (await anon_client.get("/api/v1/health")).status_code == 200


async def test_bootstrap_loopback_only(app, token):
    for host, status in (("127.0.0.1", 200), ("::1", 200), ("192.168.1.5", 403), ("8.8.8.8", 403)):
        transport = httpx.ASGITransport(app=app, client=(host, 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/auth/bootstrap")
            assert r.status_code == status, host
            if status == 403:
                assert "set-cookie" not in r.headers
                assert r.json()["error"]["code"] == "FORBIDDEN"



async def test_bootstrap_remote_allowed_by_config(config, ctx):
    config.server.allow_remote_bootstrap = True
    app = create_app(config, ctx=ctx)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("172.17.0.1", 5000))  # Docker 网关
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/auth/bootstrap")
            assert r.status_code == 200 and f"{COOKIE_NAME}={app.state.token}" in r.headers["set-cookie"]


def test_token_file_created_with_mode_600(config, app, token):
    assert config.token_path.read_text() == token
    assert len(token) >= 43  # token_urlsafe(32)
    if os.name == "posix":
        assert stat.S_IMODE(config.token_path.stat().st_mode) == 0o600


def test_ensure_token_is_stable(tmp_path):
    p = tmp_path / "sub" / "token"
    t1 = ensure_token(p)
    assert ensure_token(p) == t1
    p.write_text("")
    assert ensure_token(p) != t1 and p.read_text()


async def test_response_has_request_id(client):
    r = await client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id!"})
    assert r.headers["x-request-id"] != "bad id!" and r.headers["x-request-id"]


async def test_no_cors(client):
    r = await client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = await client.options(
        "/api/v1/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers
