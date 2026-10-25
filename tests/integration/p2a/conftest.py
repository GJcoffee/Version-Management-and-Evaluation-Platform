"""P2-A 集成测试的公共 fixture：Provider、资产、agent 的快速创建函数。"""

from __future__ import annotations

from typing import Any

import pytest

API = "/api/v1"

TOOL_YAML = """\
name: query_order
description: 根据订单号查询订单状态
parameters:
  type: object
  properties:
    order_id: {type: string, description: 订单号}
  required: [order_id]
impl:
  type: mock
  responses:
    - match: {order_id: "A001"}
      output: {status: 已发货}
    - default: {status: 未找到}
"""


class Api:
    """对 httpx 客户端的薄封装，减少测试里的样板代码。"""

    def __init__(self, client: Any) -> None:
        self.c = client

    async def provider(self, name: str = "deepseek", models: list[str] | None = None) -> dict:
        r = await self.c.post(
            f"{API}/providers",
            json={
                "name": name,
                "base_url": "http://127.0.0.1:9/v1",
                "models": [{"name": m} for m in (models if models is not None else ["deepseek-chat"])],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    async def asset(
        self,
        kind: str,
        name: str,
        content: str,
        content_type: str | None = None,
        *,
        expect: int | None = None,
        **extra: Any,
    ) -> dict:
        body: dict[str, Any] = {"kind": kind, "name": name, "content": content, **extra}
        if content_type:
            body["content_type"] = content_type
        r = await self.c.post(f"{API}/assets", json=body)
        assert r.status_code in ((expect,) if expect else (200, 201)), r.text
        return r.json()

    async def revision(self, asset_id: str, content: str, **extra: Any) -> dict:
        r = await self.c.post(f"{API}/assets/{asset_id}/revisions", json={"content": content, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()

    async def agent(
        self, name: str = "bot", kind: str = "config", spec: dict | None = None, **extra: Any
    ) -> dict:
        body: dict[str, Any] = {"name": name, "kind": kind, **extra}
        if spec is not None:
            body["spec"] = spec
        r = await self.c.post(f"{API}/agents", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    async def save(self, draft: dict, spec: dict, expect: int = 200) -> dict:
        r = await self.c.put(
            f"{API}/drafts/{draft['id']}", json={"spec": spec, "expected_revision": draft["revision"]}
        )
        assert r.status_code == expect, r.text
        return r.json()

    async def publish(self, draft: dict, notes: str | None = None, expect: int = 200, **extra: Any) -> dict:
        r = await self.c.post(
            f"{API}/drafts/{draft['id']}/publish",
            json={"expected_revision": draft["revision"], "notes": notes, **extra},
        )
        assert r.status_code == expect, r.text
        return r.json()

    async def draft(self, draft_id: str) -> dict:
        r = await self.c.get(f"{API}/drafts/{draft_id}")
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def api(client) -> Api:
    return Api(client)


def config_spec(**over: Any) -> dict:
    """一个最小可发布的配置型 Spec。"""
    spec: dict[str, Any] = {
        "kind": "config",
        "model": {"provider": "deepseek", "name": "deepseek-chat"},
        "prompts": {"system": "你是客服助手。"},
    }
    spec.update(over)
    return spec
