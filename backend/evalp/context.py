"""AppContext：基础设施的统一持有者。

``config``、``db``、``blobs``、``bus``、``secrets`` 在创建时构建；业务服务通过 ``ctx.svc(Cls)``
懒加载为单例（``Cls(ctx)``）。路由里用 ``ctx: AppContext = Depends(get_ctx)``。
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

from evalp.config import Config
from evalp.runtime.eventbus import EventBus
from evalp.secrets import SecretStore
from evalp.store.blobstore import BlobStore
from evalp.store.db import Database
from evalp.store.migrate import run_migrations

T = TypeVar("T")


class AppContext:
    """应用上下文。"""

    def __init__(
        self,
        config: Config,
        db: Database,
        blobs: BlobStore,
        bus: EventBus,
        secrets: SecretStore,
    ) -> None:
        self.config = config
        self.db = db
        self.blobs = blobs
        self.bus = bus
        self.secrets = secrets
        self._services: dict[type, object] = {}

    @classmethod
    async def create(cls, config: Config, *, migrate: bool = True) -> AppContext:
        """创建目录、（可选）执行迁移并构建全部基础设施。"""
        config.ensure_dirs()
        if migrate:
            await asyncio.to_thread(run_migrations, config.db_path)
        db = Database(config.db_path)
        await db.ping()
        return cls(
            config=config,
            db=db,
            blobs=BlobStore(config.blobs_dir, db),
            bus=EventBus(),
            secrets=SecretStore(config.secrets_path),
        )

    def svc(self, cls: type[T]) -> T:
        """返回服务单例（首次调用时以 ``cls(self)`` 构造）。"""
        inst = self._services.get(cls)
        if inst is None:
            inst = cls(self)  # type: ignore[call-arg]
            self._services[cls] = inst
        return inst  # type: ignore[return-value]

    async def close(self) -> None:
        """关闭总线与数据库连接；有 ``aclose()`` 的服务也会被关闭。"""
        for inst in list(self._services.values()):
            aclose = getattr(inst, "aclose", None)
            if aclose is not None:
                await aclose()
        self._services.clear()
        self.bus.close()
        await self.db.dispose()
