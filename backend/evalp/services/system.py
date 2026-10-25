"""系统服务：健康检查、GC。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from evalp import __version__
from evalp.context import AppContext
from evalp.services.settings import SettingsService
from evalp.store.gc import run_gc
from evalp.store.models import Job, Provider


class SystemService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def health(self) -> dict[str, Any]:
        """GET /health 的响应体（busy / inflight / rpm_left / tpm_left 待调度器接入后补充真实数据）。"""
        async with self.ctx.db.read() as s:
            counts = dict(
                (
                    await s.execute(
                        select(Job.status, func.count())
                        .where(Job.status.in_(("pending", "running")))
                        .group_by(Job.status)
                    )
                ).all()
            )
            provider_names = (await s.execute(select(Provider.name).order_by(Provider.name))).scalars().all()
        workers_total = await self.ctx.svc(SettingsService).get("scheduler.workers")
        return {
            "ok": True,
            "version": __version__,
            "db_size": self.ctx.db.file_size(),
            "queue": {"pending": counts.get("pending", 0), "running": counts.get("running", 0)},
            "workers": {"busy": 0, "total": workers_total},
            "providers": [
                {"name": n, "inflight": 0, "rpm_left": None, "tpm_left": None} for n in provider_names
            ],
        }

    async def gc(self) -> dict[str, int]:
        """POST /system/gc：清理无引用 blob 与已软删除的资产修订。"""
        result = await run_gc(self.ctx.db, self.ctx.blobs)
        return {"blobs_deleted": result.blobs_deleted, "bytes_freed": result.bytes_freed}
