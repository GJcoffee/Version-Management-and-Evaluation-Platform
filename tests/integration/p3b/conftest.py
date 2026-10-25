"""P3-B（评测模块）集成测试的公共 fixture：带调度器的评测"世界"。

``ev`` fixture 依赖 ``client``（因此应用的 lifespan 已启动调度器），并把调度器调成测试友好的参数：
轮询间隔 / 退避都很短，worker 数默认 8。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from evalp.runtime.scheduler import Scheduler
from evalp.schemas.eval import EvalCreate
from evalp.services.eval import EvalService

from tests.integration.p3a.conftest import World

API = "/api/v1"


def jl(*rows: Any) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def make_cases(n: int, *, prefix: str = "c", expected: bool = True) -> list[dict[str, Any]]:
    """n 个简单用例：输入 ``问题 i``，期望输出（精确匹配 fake 的回显）。"""
    out = []
    for i in range(n):
        row: dict[str, Any] = {
            "case_key": f"{prefix}{i:03d}",
            "input": f"问题{i}",
            "tags": ["even" if i % 2 == 0 else "odd"],
        }
        if expected:
            row["expected"] = f"echo: 问题{i}"
        out.append(row)
    return out


class EvalWorld(World):
    def __init__(self, ctx: Any, client: Any, fake: Any) -> None:
        super().__init__(ctx, client)
        self.fake = fake
        self.evals = ctx.svc(EvalService)
        self.sched = ctx.svc(Scheduler)

    async def dataset(self, name: str, cases: list[dict[str, Any]]) -> dict:
        r = await self.client.post(
            f"{API}/datasets/import", json={"name": name, "content": jl(*cases), "format": "jsonl"}
        )
        assert r.status_code == 201, r.text
        return r.json()

    async def create(
        self, versions: list[dict] | dict, dataset: str, evaluators: list[dict] | None = None, **kw: Any
    ):
        vs = versions if isinstance(versions, list) else [versions]
        body = EvalCreate(
            version_ids=[v["id"] for v in vs],
            dataset=dataset,
            evaluators=evaluators if evaluators is not None else [{"type": "exact_match"}],
            **{"repeats": 1, **kw},
        )
        return await self.evals.create(body)

    async def run(
        self, versions: list[dict] | dict, dataset: str, evaluators: list[dict] | None = None, **kw: Any
    ):
        """创建并等待结束，返回 (EvalDetail, EvalCreated)。"""
        created = await self.create(versions, dataset, evaluators, **kw)
        detail = await self.evals.wait_finished(created.runs[0].id, timeout=60, interval=0.05)
        return detail, created

    async def items(self, run_id: str, **kw: Any) -> list:
        out, cursor = [], None
        while True:
            page = await self.evals.list_items(run_id, limit=200, cursor=cursor, **kw)
            out += page.items
            cursor = page.next_cursor
            if not cursor:
                return out

    def calls(self) -> int:
        """fake 模型收到的 chat 请求数。"""
        return len(self.fake.chat_requests)


@pytest.fixture
async def ev(ctx, client, fake_openai) -> EvalWorld:
    sched = ctx.svc(Scheduler)
    sched.poll_interval = 0.05
    sched.backoff_base = 0.05
    sched.backoff_max = 0.3
    sched.group_ttl = 0.2
    await sched.set_workers(8)
    return EvalWorld(ctx, client, fake_openai)
