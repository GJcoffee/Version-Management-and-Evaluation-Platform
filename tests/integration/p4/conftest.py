"""P4（CLI / MCP / 工作区客户端）测试的公共 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest
from evalp.client import EvalpClient
from evalp.workspace_client import Workspace, find_project

from tests.integration.p2a.conftest import TOOL_YAML, Api

TOML = """\
[server]
url = "http://test"

[agent]
name = "support"
spec = "agent.yaml"
draft = "main"

[assets]
"prompts/system.md" = "prompt/support-system"
"tools/*.yaml" = "tool/{stem}"
"datasets/regression.jsonl" = "dataset/support-regression"
"""

AGENT_YAML = """\
schema: evalp/v1
kind: config
model:
  provider: deepseek
  name: deepseek-chat
prompts:
  system: file:prompts/system.md
tools:
  - file:tools/query-order.yaml
"""

DATASET = '{"case_key": "a", "input": "你好", "expected": "您好"}\n'


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    (d / "prompts").mkdir(parents=True)
    (d / "tools").mkdir()
    (d / "datasets").mkdir()
    (d / ".evalp.toml").write_text(TOML, encoding="utf-8")
    (d / "agent.yaml").write_text(AGENT_YAML, encoding="utf-8")
    (d / "prompts" / "system.md").write_text("你是客服助手。\n", encoding="utf-8")
    (d / "tools" / "query-order.yaml").write_text(TOOL_YAML, encoding="utf-8")
    (d / "datasets" / "regression.jsonl").write_text(DATASET, encoding="utf-8")
    return d


@pytest.fixture
def ws(project_dir: Path) -> Workspace:
    return Workspace(find_project(project_dir))


@pytest.fixture
def evalp_client(client) -> EvalpClient:
    """接在进程内应用上的 EvalpClient（共用 conftest 的 httpx 客户端，不负责关闭）。"""
    return EvalpClient(http=client)


@pytest.fixture
async def provider(client) -> None:
    await Api(client).provider("deepseek")
