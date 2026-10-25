"""P3-A 集成测试的公共 fixture：快速创建各类 agent 的版本、会话，以及示例 HTTP / 子进程 agent。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from evalp.runtime.llm.registry import LLMRegistry
from evalp.services.session import SessionService
from evalp.services.version import VersionService

from tests.integration.p2a.conftest import Api

API = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SDK_AGENT = FIXTURES / "sdk_agents.py"

SEARCH_TOOL_YAML = """\
name: query_order
description: 根据订单号查询订单状态
parameters:
  type: object
  properties:
    order_id: {type: string}
  required: [order_id]
impl:
  type: mock
  responses:
    - match: {order_id: "A001"}
      output: {status: 已发货}
    - match: {order_id: "A002"}
      output: {status: 已签收}
    - default: {status: 未找到}
"""


class World:
    """测试用的"世界"：包装 REST 客户端与服务，快速创建版本和会话。"""

    def __init__(self, ctx: Any, client: Any) -> None:
        self.ctx = ctx
        self.api = Api(client)
        self.client = client
        self.sessions = ctx.svc(SessionService)
        self._n = 0

    def _name(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    async def config_version(self, spec_over: dict | None = None, name: str | None = None) -> dict:
        """发布一个配置型 agent（Provider fake / fake-model）的版本，返回版本 dict。"""
        spec: dict[str, Any] = {
            "kind": "config",
            "model": {"provider": "fake", "name": "fake-model", "params": {"temperature": 0}},
            "prompts": {"system": "你是客服助手。"},
        }
        spec.update(spec_over or {})
        out = await self.api.agent(name or self._name("cfg"), spec=spec)
        return (await self.api.publish(out["draft"]))["version"]

    async def code_version(self, spec: dict, name: str | None = None) -> dict:
        full = {"kind": "code", "source": {"capture_git": False}, **spec}
        out = await self.api.agent(name or self._name("code"), kind="code", spec=full)
        return (await self.api.publish(out["draft"]))["version"]

    async def http_version(self, base_url: str, **over: Any) -> dict:
        return await self.code_version({"transport": "http", "http": {"base_url": base_url}, **over})

    async def subprocess_version(
        self, args: list[str] | None = None, env: dict | None = None, **over: Any
    ) -> dict:
        spec = {
            "transport": "subprocess",
            "subprocess": {
                "command": [sys.executable, str(SDK_AGENT), *(args or ["stdio"])],
                "env": env or {},
            },
            **over,
        }
        return await self.code_version(spec)

    async def session(self, version: dict, **kw: Any):
        return await self.sessions.create(version_id=version["id"], mode=kw.pop("mode", "chat"), **kw)

    async def chat(self, version: dict, text: str, **kw: Any):
        """新建会话并执行一轮，返回 (session, turn)。"""
        s = await self.session(version)
        t = await self.sessions.run_turn(s.id, text, **kw)
        return s, t

    async def events(self, session_id: str, **kw: Any):
        return await self.sessions.get_events(session_id, **kw)

    async def types(self, session_id: str, **kw: Any) -> list[str]:
        return [e.type for e in await self.events(session_id, **kw)]


@pytest.fixture
def llm_fast(ctx, fake_openai):
    """把 LLM 重试间隔调小，加快带故障注入的测试。"""
    from evalp.runtime.llm.client import RetryPolicy

    client = ctx.svc(LLMRegistry).llm
    client.retry = RetryPolicy(base_delay=0.01, max_delay=0.05)
    return client


@pytest.fixture
async def world(ctx, client, fake_openai) -> World:
    # 版本引用解析等依赖服务单例，这里提前构造，保证 ctx.close 时统一清理
    ctx.svc(VersionService)
    return World(ctx, client)
