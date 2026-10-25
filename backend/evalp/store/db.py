"""数据库访问（详设 3.1）：aiosqlite 异步引擎、单写连接 + 读连接池、JSONText 类型。

- ``async with db.read() as s``：只读会话（读连接池，大小 8，PRAGMA query_only）；
- ``async with db.write() as s``：写会话，持有全局 asyncio.Lock，退出时自动提交，异常时回滚。
  **write() 不可重入**：在 write() 块内再次调用 write() 会死锁，需要事务内操作时请传递 session。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from sqlalchemy import Text, event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.types import TypeDecorator

READ_POOL_SIZE = 8

_COMMON_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)


class JSONText(TypeDecorator):
    """以 TEXT 存储的 JSON 列：写入时 json.dumps(ensure_ascii=False)，读取时 json.loads。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        return json.loads(value)


def _install_pragmas(engine: AsyncEngine, *, read_only: bool) -> None:
    """连接建立时执行 PRAGMA，并让 SQLAlchemy 自己控制事务边界（写用 BEGIN IMMEDIATE）。"""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.isolation_level = None  # 关闭 pysqlite 的隐式事务
        cur = dbapi_conn.cursor()
        try:
            for stmt in _COMMON_PRAGMAS:
                cur.execute(stmt)
            if read_only:
                cur.execute("PRAGMA query_only = ON")
        finally:
            cur.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN" if read_only else "BEGIN IMMEDIATE")


class Database:
    """SQLite 数据库：一个写引擎（单连接）+ 一个读引擎（连接池）。"""

    def __init__(self, path: Path | str, *, read_pool_size: int = READ_POOL_SIZE) -> None:
        self.path = Path(path)
        url = f"sqlite+aiosqlite:///{self.path.as_posix()}"
        self._write_engine = create_async_engine(
            url, poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0
        )
        self._read_engine = create_async_engine(
            url, poolclass=AsyncAdaptedQueuePool, pool_size=read_pool_size, max_overflow=0
        )
        _install_pragmas(self._write_engine, read_only=False)
        _install_pragmas(self._read_engine, read_only=True)
        self._write_sm = async_sessionmaker(self._write_engine, expire_on_commit=False)
        self._read_sm = async_sessionmaker(self._read_engine, expire_on_commit=False)
        self.write_lock = asyncio.Lock()

    @property
    def write_engine(self) -> AsyncEngine:
        return self._write_engine

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """只读会话；退出时关闭会话（释放读快照）。已加载的对象在会话关闭后仍可读取其属性。"""
        async with self._read_sm() as session:
            yield session

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """写会话：全局单写锁 + 事务；正常退出提交，异常回滚。"""
        async with self.write_lock:
            async with self._write_sm() as session:
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    with suppress(Exception):
                        await asyncio.shield(session.rollback())
                    raise

    def file_size(self) -> int:
        """数据库文件（含 WAL）占用的字节数。"""
        total = 0
        for suffix in ("", "-wal"):
            p = Path(f"{self.path}{suffix}")
            if p.exists():
                total += p.stat().st_size
        return total

    async def ping(self) -> None:
        """触发一次连接，确保 PRAGMA（含 WAL）已生效。"""
        async with self.read() as s:
            await s.connection()

    async def dispose(self) -> None:
        """关闭所有连接。"""
        await self._read_engine.dispose()
        await self._write_engine.dispose()
