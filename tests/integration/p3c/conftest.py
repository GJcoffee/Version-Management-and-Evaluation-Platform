"""P3-C（对比与重放）集成测试的公共 fixture。"""

from __future__ import annotations

import asyncio

import pytest
from evalp.services.compare import CompareService
from evalp.services.session import SessionService
from evalp.services.version import VersionService

from tests.fixtures.fake_openai import Call
from tests.integration.p3a.conftest import SEARCH_TOOL_YAML, World

API = "/api/v1"


class CmpWorld(World):
    """在 World 上增加：按 system 提示区分三个版本的 fake 脚本，以及等待空闲的辅助方法。"""

    def __init__(self, ctx, client, fake) -> None:
        super().__init__(ctx, client)
        self.fake = fake
        self.compare = ctx.svc(CompareService)

    async def setup_versions(self) -> dict[str, dict]:
        """P：不用工具；T1 / T2：先调用 query_order（订单号 A001 / A002）再回答。"""
        await self.api.asset("tool", "q", SEARCH_TOOL_YAML)
        tools = {"tools": ["asset://tool/q@latest"]}
        vs = {
            "P": await self.config_version({"prompts": {"system": "你是P号客服"}}),
            "T1": await self.config_version({"prompts": {"system": "你是T1号客服"}, **tools}),
            "T2": await self.config_version({"prompts": {"system": "你是T2号客服"}, **tools}),
        }
        f = self.fake
        for name, oid in (("T1", "A001"), ("T2", "A002")):
            f.add_rule({"system": name, "has_tool": False}, [Call("query_order", {"order_id": oid})])
            f.add_rule(
                {"system": name, "has_tool": True}, lambda r, n=name: f"{n}答复：{r.last_tool_content}"
            )
        f.add_rule({"system": "P号"}, lambda r: f"P答复：{r.last_user}")
        return vs

    async def ref(self, version: dict) -> str:
        """版本的 ``agent:vN`` 引用。"""
        r = await self.client.get(f"{API}/agents/{version['agent_id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        name = (body.get("agent") or body)["name"]
        return f"{name}:v{version['seq']}"

    async def wait_idle(self, *session_ids: str, timeout: float = 10.0) -> None:
        svc = self.ctx.svc(SessionService)

        async def _wait() -> None:
            while any(svc.is_running(s) for s in session_ids):
                await asyncio.sleep(0.02)

        await asyncio.wait_for(_wait(), timeout)

    async def new_compare(self, left: dict, right: dict, **kw) -> dict:
        r = await self.client.post(
            f"{API}/compare",
            json={"kind": "session", "mode": "live", "left": {"version_id": left["id"]},
                  "right": {"version_id": right["id"]}, **kw},
        )  # fmt: skip
        assert r.status_code == 201, r.text
        return r.json()

    async def pair(self, cmp_id: str, text: str) -> dict:
        r = await self.client.post(f"{API}/compare/{cmp_id}/turns", json={"input": text})
        assert r.status_code == 202, r.text
        return r.json()

    async def alignment(self, cmp_id: str) -> dict:
        r = await self.client.get(f"{API}/compare/{cmp_id}/alignment")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
async def cw(ctx, client, fake_openai) -> CmpWorld:
    ctx.svc(VersionService)
    return CmpWorld(ctx, client, fake_openai)
