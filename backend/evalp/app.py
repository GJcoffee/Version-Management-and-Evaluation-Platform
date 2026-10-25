"""FastAPI 应用工厂。

``create_app(config)``：
- lifespan 中初始化数据目录、日志、token 文件、执行迁移、构建 AppContext；
- 自动发现 ``evalp.api.v1`` 下所有模块导出的 ``router`` 并挂到 ``/api/v1``（新增模块无需改本文件）；
- 全局异常处理器输出 ``{"error": {code, message, details}}``；
- ``/api/*`` 需要 token（Bearer 或 Cookie ``evalp_token``），``GET /auth/bootstrap`` 为回环地址下发 Cookie；
- ``evalp/static/index.html`` 存在时托管前端并做 SPA 回退；不启用 CORS。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import evalp
from evalp.auth import COOKIE_NAME, AuthMiddleware, ensure_token, is_loopback
from evalp.config import Config
from evalp.context import AppContext
from evalp.errors import EvalpError, Forbidden, InternalError, NotFound, ValidationFailed
from evalp.ids import _ulid_str
from evalp.logging import get_logger, setup_logging, shutdown_logging
from evalp.runtime.scheduler import Scheduler
from evalp.services.eval import start_eval_runtime

log = get_logger(__name__)

API_PREFIX = "/api/v1"
COOKIE_MAX_AGE = 365 * 24 * 3600
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def discover_routers() -> list[APIRouter]:
    """导入 evalp.api.v1 下的所有模块，收集导出的 ``router``（按模块名排序）。"""
    import evalp.api.v1 as v1

    routers: list[APIRouter] = []
    for mod_info in sorted(pkgutil.iter_modules(v1.__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{v1.__name__}.{mod_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            routers.append(router)
    return routers


# ---------------------------------------------------------------- 中间件


class RequestContextMiddleware:
    """为每个请求绑定 request_id（日志上下文 + X-Request-ID 响应头），并记录访问日志。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = raw if _REQUEST_ID_RE.match(raw) else _ulid_str()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------- 异常处理


def _error_response(err: EvalpError) -> JSONResponse:
    return JSONResponse(err.to_dict(), status_code=err.status)


def _validation_details(errors: list[dict]) -> dict:
    """精简 Pydantic 错误：只保留字段路径、消息、类型（不回显 input，避免泄露密钥）。"""
    items = [
        {
            "path": ".".join(str(p) for p in e.get("loc", ())),
            "message": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        for e in errors
    ]
    return {"errors": items}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(EvalpError)
    async def _evalp_error(_: Request, exc: EvalpError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(ValidationFailed("请求校验失败", _validation_details(list(exc.errors()))))

    @app.exception_handler(ValidationError)
    async def _pydantic_validation(_: Request, exc: ValidationError) -> JSONResponse:
        return _error_response(ValidationFailed("数据校验失败", _validation_details(list(exc.errors()))))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            err = NotFound("接口不存在")
        else:
            err = EvalpError(
                str(exc.detail),
                code="HTTP_ERROR" if exc.status_code != 405 else "METHOD_NOT_ALLOWED",
                status=exc.status_code,
            )
        resp = _error_response(err)
        if exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("http.unhandled_exception", error=type(exc).__name__)
        return _error_response(InternalError())


# ---------------------------------------------------------------- 应用工厂


async def _recover_sessions(app_ctx: AppContext) -> None:
    """启动恢复：把上次退出时遗留的 running 轮次标记为 INTERRUPTED（失败只记录日志，不阻止启动）。"""
    from evalp.services.session import SessionService

    try:
        await app_ctx.svc(SessionService).recover()
    except Exception as e:  # noqa: BLE001
        log.error("app.session_recovery_failed", error=f"{type(e).__name__}: {e}")


def create_app(
    config: Config,
    *,
    ctx: AppContext | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """创建应用。

    ctx：测试用，传入已构建的 AppContext 时由调用方负责其生命周期；
    static_dir：前端构建产物目录，默认 ``evalp/static``，其中存在 index.html 才会托管。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config.ensure_dirs()
        setup_logging(config.logs_dir)
        app.state.token = ensure_token(config.token_path)
        owned = ctx is None
        app_ctx = ctx if ctx is not None else await AppContext.create(config)
        app.state.ctx = app_ctx
        await _recover_sessions(app_ctx)
        await start_eval_runtime(app_ctx, seed_builtin=owned)
        log.info("app.started", version=evalp.__version__, data_dir=str(config.data_dir))
        try:
            yield
        finally:
            log.info("app.stopping")
            await app_ctx.svc(Scheduler).stop()  # 优雅等待在跑的任务，超时后取消；任务回到 pending 等下次启动
            if owned:
                await app_ctx.close()
            shutdown_logging()

    app = FastAPI(
        title="EvalP",
        version=evalp.__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.config = config
    if ctx is not None:  # 注入 ctx 时无需等待 lifespan 也可使用（如 httpx ASGITransport）
        config.ensure_dirs()
        app.state.ctx = ctx
        app.state.token = ensure_token(config.token_path)

    # 不启用 CORS；后加的中间件在外层：RequestContext -> Auth -> 路由
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)

    for router in discover_routers():
        app.include_router(router, prefix=API_PREFIX)

    @app.get("/auth/bootstrap", include_in_schema=False)
    async def bootstrap(request: Request) -> JSONResponse:
        """前端启动时调用：仅限回环地址，下发 HttpOnly + SameSite=Strict 的 token Cookie。"""
        host = request.client.host if request.client else None
        if not is_loopback(host):
            raise Forbidden("bootstrap 只允许本机访问")
        resp = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        resp.set_cookie(
            COOKIE_NAME,
            request.app.state.token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return resp

    _mount_static(app, static_dir or Path(evalp.__file__).parent / "static")
    return app


def _mount_static(app: FastAPI, static_dir: Path) -> None:
    """若存在 index.html，则托管静态文件并对非 /api 路径做 SPA 回退。"""
    index = static_dir / "index.html"
    if not index.is_file():
        return
    root = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise NotFound("接口不存在")
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index, media_type="text/html")
