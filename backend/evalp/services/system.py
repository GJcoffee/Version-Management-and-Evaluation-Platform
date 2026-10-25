"""系统服务：健康检查、GC。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from evalp import __version__
from evalp.context import AppContext
from evalp.errors import EvalpError
from evalp.runtime.llm.registry import LLMRegistry
from evalp.runtime.scheduler import Scheduler
from evalp.services.settings import SettingsService
from evalp.store.gc import run_gc
from evalp.store.models import Job, Provider


class SystemService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def health(self) -> dict[str, Any]:
        """GET /health 的响应体。providers 的 inflight / rpm_left / tpm_left 来自 LLM 限流器的实时状态；
        workers.busy 是调度器当前正在执行任务的 worker 数。"""
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
        providers = [await self._provider_status(n) for n in provider_names]
        return {
            "ok": True,
            "version": __version__,
            "db_size": self.ctx.db.file_size(),
            "queue": {"pending": counts.get("pending", 0), "running": counts.get("running", 0)},
            "workers": {"busy": self.ctx.svc(Scheduler).busy, "total": workers_total},
            "providers": providers,
        }

    async def _provider_status(self, name: str) -> dict[str, Any]:
        """单个 Provider 的限流状态；取不到（刚被删除等）时给出空闲状态。"""
        try:
            st = await self.ctx.svc(LLMRegistry).status(name)
        except EvalpError:
            return {"name": name, "inflight": 0, "rpm_left": None, "tpm_left": None}
        return {"name": name, **{k: st[k] for k in ("inflight", "rpm_left", "tpm_left")}}

    async def gc(self) -> dict[str, int]:
        """POST /system/gc：清理无引用 blob、已软删除的资产修订，以及超过保留期的评测会话。"""
        days = await self.ctx.svc(SettingsService).get("retention.eval_sessions_days")
        result = await run_gc(self.ctx.db, self.ctx.blobs, eval_sessions_days=int(days))
        return {
            "blobs_deleted": result.blobs_deleted,
            "bytes_freed": result.bytes_freed,
            "eval_sessions_deleted": result.eval_sessions_deleted,
        }
