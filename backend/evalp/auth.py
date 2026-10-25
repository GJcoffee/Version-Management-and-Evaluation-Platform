"""API token 与鉴权中间件（详设 11.1、16.1）。

- token 首次启动时由 ``secrets.token_urlsafe(32)`` 生成，写入 ``{data_dir}/token``（权限 600）；
- ``/api/*`` 需要 ``Authorization: Bearer <token>`` 或 Cookie ``evalp_token``；
- 使用纯 ASGI 中间件（不包装响应体），不影响 SSE 流式输出。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from evalp.errors import Unauthorized

COOKIE_NAME = "evalp_token"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def is_loopback(host: str | None) -> bool:
    """host 是否为回环地址。"""
    if not host:
        return False
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def ensure_token(token_path: Path) -> str:
    """读取或首次生成 API token，文件权限设为 600。"""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                pass
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(token_path, 0o600)
    return token


def _extract_token(headers: Headers) -> str | None:
    auth = headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    cookie = headers.get("cookie")
    if cookie:
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and value:
                return value
    return None


class AuthMiddleware:
    """拦截 ``/api/`` 下缺少或带有错误 token 的请求，返回 401。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        expected: str | None = getattr(scope["app"].state, "token", None)
        given = _extract_token(Headers(scope=scope))
        if not expected or not given or not secrets.compare_digest(given, expected):
            from starlette.responses import JSONResponse

            resp = JSONResponse(
                Unauthorized().to_dict(), status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)
