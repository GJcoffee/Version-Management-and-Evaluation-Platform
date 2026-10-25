import json
import stat
import sys
import textwrap

import evalp.api.v1 as v1
import httpx
import pytest
from evalp.api.sse import format_sse, sse_response
from evalp.app import create_app, discover_routers
from evalp.config import load_config
from evalp.context import AppContext
from fastapi import APIRouter


async def test_lifespan_initializes_everything(tmp_path):
    cfg = load_config(tmp_path / "fresh")
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        assert cfg.data_dir.is_dir() and cfg.blobs_dir.is_dir() and cfg.logs_dir.is_dir()
        assert cfg.db_path.is_file()  # 已迁移
        assert stat.S_IMODE(cfg.token_path.stat().st_mode) == 0o600
        assert cfg.token_path.read_text() == app.state.token
        ctx = app.state.ctx
        assert isinstance(ctx, AppContext)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {app.state.token}"}
        ) as c:
            assert (await c.get("/api/v1/health")).status_code == 200
    assert list(cfg.logs_dir.glob("evalp-*.log"))  # 日志文件已写入
    lines = [json.loads(x) for x in next(cfg.logs_dir.glob("evalp-*.log")).read_text().splitlines()]
    assert any(rec["event"] == "app.started" for rec in lines)
    assert any(rec["event"] == "http.request" and rec.get("request_id") for rec in lines)


async def test_token_is_reused_across_restarts(tmp_path):
    cfg = load_config(tmp_path)
    tokens = []
    for _ in range(2):
        app = create_app(cfg)
        async with app.router.lifespan_context(app):
            tokens.append(app.state.token)
    assert tokens[0] == tokens[1]


def test_autodiscovery_finds_builtin_routers(config):
    paths = {r.prefix for r in discover_routers()}
    assert "/providers" in paths
    app_paths = {getattr(r, "path", None) for r in create_app(config).routes}
    assert "/auth/bootstrap" in app_paths


async def test_autodiscovery_mounts_new_module(tmp_path, config, ctx, monkeypatch):
    """在 api/v1 下新增模块即自动挂载，无需修改 app.py。"""
    pkg = tmp_path / "extra"
    pkg.mkdir()
    (pkg / "zz_demo.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter
            router = APIRouter(prefix="/demo", tags=["demo"])

            @router.get("/ping")
            async def ping():
                return {"pong": True}
            """
        )
    )
    (pkg / "_private.py").write_text("raise RuntimeError('不应被导入')")
    (pkg / "norouter.py").write_text("x = 1")
    monkeypatch.setattr(v1, "__path__", [*v1.__path__, str(pkg)])
    try:
        app = create_app(config, ctx=ctx)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://t",
                headers={"Authorization": f"Bearer {app.state.token}"},
            ) as c:
                r = await c.get("/api/v1/demo/ping")
                assert r.status_code == 200 and r.json() == {"pong": True}
                assert (await c.get("/api/v1/providers")).status_code == 200
            anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
            assert (await anon.get("/api/v1/demo/ping")).status_code == 401
            await anon.aclose()
    finally:
        sys.modules.pop("evalp.api.v1.zz_demo", None)


async def test_ctx_svc_is_lazy_singleton(ctx):
    class Foo:
        created = 0

        def __init__(self, c):
            Foo.created += 1
            self.ctx = c

    assert Foo.created == 0
    a = ctx.svc(Foo)
    assert ctx.svc(Foo) is a and Foo.created == 1 and a.ctx is ctx
    assert ctx.config and ctx.db and ctx.blobs and ctx.bus and ctx.secrets


# ---------------------------------------------------------------- 静态文件 / SPA


@pytest.fixture
def static_dir(tmp_path):
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside")
    return d


async def test_spa_fallback(config, ctx, static_dir):
    app = create_app(config, ctx=ctx, static_dir=static_dir)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/")
        assert r.status_code == 200 and "SPA" in r.text
        assert "SPA" in (await c.get("/agents/agt_1/versions")).text  # 前端路由回退
        r = await c.get("/assets/app.js")
        assert r.status_code == 200 and r.text == "console.log(1)"
        # /api 路径不回退：未鉴权 401；已鉴权的未知路径 404 JSON
        assert (await c.get("/api/v1/whatever")).status_code == 401
        r = await c.get("/api/v1/whatever", headers={"Authorization": f"Bearer {app.state.token}"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
        # bootstrap 不被回退吞掉
        assert (await c.get("/auth/bootstrap")).json() == {"ok": True}
        # 目录穿越不能读到静态目录之外的文件
        r = await c.get("/..%2Fsecret.txt")
        assert "outside" not in r.text
        r = await c.get("/assets/%2e%2e/%2e%2e/secret.txt")
        assert "outside" not in r.text


async def test_no_static_when_index_missing(config, ctx, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    app = create_app(config, ctx=ctx, static_dir=empty)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- SSE 经 API 输出


sse_router = APIRouter(prefix="/_sse")


async def test_sse_through_api_with_eventbus(make_client, ctx):
    async def frames(sub):
        try:
            async for ev in sub:
                yield format_sse(ev["data"], event=ev["type"], id=ev.get("seq"))
                if ev["type"] == "idle":
                    return
        finally:
            sub.close()

    @sse_router.get("/stream")
    async def stream():
        sub = ctx.bus.subscribe("session:ses_1")
        # 先发布再消费：队列缓存保证不丢
        ctx.bus.publish("session:ses_1", {"type": "llm.delta", "data": {"t": "你"}})
        ctx.bus.publish("session:ses_1", {"type": "turn.end", "seq": 3, "data": {"status": "ok"}})
        ctx.bus.publish("session:ses_1", {"type": "idle", "data": {}})
        return sse_response(frames(sub), heartbeat=15)

    async with make_client(sse_router) as client:
        r = await client.get("/api/v1/_sse/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == (
        'event: llm.delta\ndata: {"t":"你"}\n\n'
        'id: 3\nevent: turn.end\ndata: {"status":"ok"}\n\n'
        "event: idle\ndata: {}\n\n"
    )
    assert ctx.bus.subscriber_count() == 0  # 订阅已清理
