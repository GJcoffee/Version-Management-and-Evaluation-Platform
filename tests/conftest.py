"""全局 fixture。

- ``config``：指向 tmp_path 的配置；
- ``ctx``：临时数据目录 + 已迁移的数据库 + 全部基础设施（AppContext）；
- ``app``：基于 ``ctx`` 的 FastAPI 应用（已运行 lifespan）；
- ``client``：``httpx.AsyncClient`` + ASGITransport，已带 Bearer token；
- ``anon_client``：不带任何凭据的客户端（测试 401 / bootstrap）。

测试中 keyring 一律强制失败，使密钥存储走 Fernet 文件回退。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import keyring
import pytest
from evalp.app import create_app
from evalp.config import Config, load_config
from evalp.context import AppContext
from fastapi import FastAPI
from keyring.backends import fail


@pytest.fixture(autouse=True, scope="session")
def _no_system_keyring() -> Iterator[None]:
    """禁用系统钥匙串，强制走 Fernet 文件回退。"""
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除开发机上可能存在的 EVALP_* 环境变量。"""
    for key in list(os.environ):
        if key.startswith("EVALP_"):
            monkeypatch.delenv(key)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def config(data_dir: Path) -> Config:
    return load_config(data_dir)


@pytest.fixture
async def ctx(config: Config) -> AsyncIterator[AppContext]:
    context = await AppContext.create(config)
    try:
        yield context
    finally:
        await context.close()


@pytest.fixture
async def app(config: Config, ctx: AppContext) -> AsyncIterator[FastAPI]:
    application = create_app(config, ctx=ctx)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def token(app: FastAPI) -> str:
    return app.state.token


@pytest.fixture
async def client(app: FastAPI, token: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as c:
        yield c


@pytest.fixture
async def anon_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def make_client(config: Config, ctx: AppContext):
    """构造带额外路由的应用与客户端：``async with make_client(router, ...) as client``。

    额外路由与自动发现的路由一起挂在 /api/v1 下（FastAPI 不支持启动后再追加路由）。
    """
    import contextlib

    import evalp.app as app_module

    @contextlib.asynccontextmanager
    async def _make(*routers, raise_app_exceptions: bool = True):  # noqa: ANN002
        original = app_module.discover_routers
        app_module.discover_routers = lambda: [*original(), *routers]
        try:
            application = app_module.create_app(config, ctx=ctx)
        finally:
            app_module.discover_routers = original
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application, raise_app_exceptions=raise_app_exceptions)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Authorization": f"Bearer {application.state.token}"},
            ) as c:
                yield c

    return _make
