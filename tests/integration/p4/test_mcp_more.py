"""MCP 其余工具：list_agents / get_draft / update_draft / 资产与数据集 / 评测状态 / 会话 / 重放 / 对比。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from evalp.client import EvalpClient
from evalp.mcp.server import build_server
from mcp import Client

from tests.integration.p2a.conftest import Api
from tests.integration.p4 import mocks
from tests.integration.p4.test_mcp import MOCK_API, text


@pytest.fixture
def real_server(project_dir, client):
    """工具调用打到进程内的真实应用。"""
    return build_server(project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client))


# ---------------------------------------------------------------- agent / 草稿


async def test_list_agents_and_draft_roundtrip(real_server, project_dir, client, provider):
    async with Client(real_server) as c:
        assert "还没有 agent" in text(await c.call_tool("list_agents", {}))
        r = await c.call_tool("get_draft", {})
        assert r.is_error and "push_draft" in text(r)  # 服务端还没有草稿

        await c.call_tool("push_draft", {})
        await c.call_tool("publish_version", {"tag": "baseline"})
        out = text(await c.call_tool("list_agents", {}))
        assert "`support`" in out and "v1" in out and "baseline" in out and "config" in out

        out = text(await c.call_tool("get_draft", {}))
        assert "revision 1" in out and "```yaml" in out and "kind: config" in out
        assert "asset://prompt/support-system@latest" in out
        assert "与基础版本 v1 相同" in out

        out = text(
            await c.call_tool(
                "update_draft",
                {"patch": {"model": {"params": {"temperature": 0.2}}}, "expected_revision": 1},
            )
        )
        assert "revision 1 -> 2" in out and "草稿校验: 通过" in out and "pull_draft" in out
        out = text(await c.call_tool("get_draft", {}))
        assert "revision 2" in out and "temperature: 0.2" in out and "model.params" in out
        assert "provider: deepseek" in out  # 合并补丁：没提到的键保持不变
        assert "相对基础版本 v1 的变更" in out
        # 改成登记里没有的模型：能保存，但校验不通过（报告里直接给出原因）
        out = text(
            await c.call_tool(
                "update_draft", {"patch": {"model": {"name": "deepseek-reasoner"}}, "expected_revision": 2}
            )
        )
        assert "revision 2 -> 3" in out and "未通过" in out and "deepseek-reasoner" in out
        assert "变更摘要不可用" in text(await c.call_tool("get_draft", {}))

        # 过期的 revision：给出可操作的提示，而不是原始错误
        r = await c.call_tool("update_draft", {"patch": {"model": {"name": "x"}}, "expected_revision": 1})
        assert r.is_error and "get_draft" in text(r) and "revision=3" in text(r)
        # null 删除键；草稿允许保存不完整的内容，问题体现在校验结果里
        out = text(await c.call_tool("update_draft", {"patch": {"model": None}, "expected_revision": 3}))
        assert "revision 3 -> 4" in out and "未通过" in out
        assert "model:" not in text(await c.call_tool("get_draft", {}))


async def test_update_draft_without_pull_makes_push_conflict(real_server, project_dir, provider):
    async with Client(real_server) as c:
        await c.call_tool("push_draft", {})
        await c.call_tool("update_draft", {"patch": {"runtime": {"max_steps": 3}}, "expected_revision": 1})
        (project_dir / "prompts" / "system.md").write_text("改了\n", encoding="utf-8")
        r = await c.call_tool("push_draft", {})
        assert r.is_error and "草稿冲突" in text(r)  # 工具说明里提示过：需要先 pull_draft


# ---------------------------------------------------------------- 资产与数据集


async def test_asset_tools(real_server, project_dir, client):
    async with Client(real_server) as c:
        assert "没有匹配的资产" in text(await c.call_tool("list_assets", {}))
        out = text(await c.call_tool("upsert_asset", {"kind": "prompt", "name": "greet", "content": "你好"}))
        assert "已创建资产 `prompt/greet`" in out and "rev 1" in out and "asset://prompt/greet@latest" in out
        out = text(await c.call_tool("upsert_asset", {"kind": "prompt", "name": "greet", "content": "你好"}))
        assert "没有新增修订" in out
        out = text(
            await c.call_tool(
                "upsert_asset", {"kind": "prompt", "name": "greet", "content": "您好", "message": "改称呼"}
            )
        )
        assert "已新增修订 rev 2" in out
        # 从项目目录内的文件上传
        out = text(
            await c.call_tool("upsert_asset", {"kind": "prompt", "name": "sys", "path": "prompts/system.md"})
        )
        assert "已创建资产 `prompt/sys`" in out

        out = text(await c.call_tool("list_assets", {"kind": "prompt"}))
        assert "`prompt/greet`" in out and "rev 2" in out and "`prompt/sys`" in out
        out = text(await c.call_tool("list_assets", {"q": "greet"}))
        assert "greet" in out and "`prompt/sys`" not in out

        # content / path 必须且只能给一个；path 不能跑出项目目录
        r = await c.call_tool("upsert_asset", {"kind": "prompt", "name": "x"})
        assert r.is_error and "content 与 path" in text(r)
        (project_dir.parent / "secret.txt").write_text("s", encoding="utf-8")
        r = await c.call_tool("upsert_asset", {"kind": "prompt", "name": "x", "path": "../secret.txt"})
        assert r.is_error and "项目目录内" in text(r)
        r = await c.call_tool("upsert_asset", {"kind": "prompt", "name": "x", "path": "/etc/hostname"})
        assert r.is_error and "项目目录内" in text(r)


async def test_upsert_asset_binary_uses_blob_upload(real_server, project_dir, client):
    (project_dir / "logo.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    async with Client(real_server) as c:
        out = text(await c.call_tool("upsert_asset", {"kind": "file", "name": "logo", "path": "logo.bin"}))
    assert "已创建资产 `file/logo`" in out
    assets = (await client.get("/api/v1/assets", params={"kind": "file"})).json()["items"]
    rev = (await client.get(f"/api/v1/assets/{assets[0]['id']}/revisions/latest")).json()
    assert rev["size"] == 10


async def test_import_dataset_tool(real_server, project_dir, client):
    (project_dir / "datasets" / "q.csv").write_text("问题,答案\n你好,您好\n再见,慢走\n", encoding="utf-8")
    async with Client(real_server) as c:
        out = text(await c.call_tool("import_dataset", {"name": "reg", "path": "datasets/regression.jsonl"}))
        assert "已创建数据集 `reg`" in out and "用例数: 1" in out and "dataset/reg@1" in out
        out = text(
            await c.call_tool("import_dataset", {"name": "dataset/reg", "path": "datasets/regression.jsonl"})
        )
        assert "内容未变化" in out
        out = text(
            await c.call_tool(
                "import_dataset",
                {"name": "qa", "path": "datasets/q.csv", "column_map": {"input": "问题", "expected": "答案"}},
            )
        )
        assert "用例数: 2" in out
        # CSV 不给列映射：服务端的解析错误原样转述
        r = await c.call_tool("import_dataset", {"name": "bad", "path": "datasets/q.csv"})
        assert r.is_error and "input" in text(r)
        r = await c.call_tool("import_dataset", {"name": "bad", "path": "../x.jsonl"})
        assert r.is_error and "项目目录内" in text(r)
        r = await c.call_tool("import_dataset", {"name": "bad", "path": "datasets/nope.jsonl"})
        assert r.is_error and "找不到文件" in text(r)


# ---------------------------------------------------------------- 评测状态（mock）


@pytest.fixture
def mock_server(project_dir):
    server = build_server(
        project_dir=project_dir, client_factory=lambda p: EvalpClient("http://test", "tok"), poll_interval=0
    )
    with respx.mock(base_url=MOCK_API, assert_all_called=False) as m:
        yield server, m


async def test_get_eval_status_reports_eta(mock_server):
    server, api = mock_server
    started = (datetime.now(UTC) - timedelta(seconds=100)).isoformat().replace("+00:00", "Z")
    running = {**mocks.run("evr_a", 1, "running", done=1), "started_at": started}
    running["progress"] = {"total": 4, "done": 1, "failed": 0, "running": 3, "cached": 0, "cost_new": 0.002}
    api.get("/evals/evr_a").mock(return_value=httpx.Response(200, json=running))
    async with Client(server) as c:
        out = text(await c.call_tool("get_eval_status", {"run_id": "evr_a"}))
        assert "运行中" in out and "1/4" in out and "预计剩余: 5 分" in out  # 100s 完成 1 个 -> 还要约 300s
        api.get("/evals/evr_a").mock(
            return_value=httpx.Response(200, json=mocks.run("evr_a", 1, pass_rate=0.75))
        )
        out = text(await c.call_tool("get_eval_status", {"run_id": "evr_a"}))
        assert "已完成" in out and "预计剩余" not in out and "75.0%" in out
        api.get("/evals/evr_x").mock(
            return_value=httpx.Response(
                404, json={"error": {"code": "NOT_FOUND", "message": "评测不存在: evr_x"}}
            )
        )
        r = await c.call_tool("get_eval_status", {"run_id": "evr_x"})
        assert r.is_error and "评测不存在" in text(r)


# ---------------------------------------------------------------- run_eval 读取 [eval] 默认值


async def test_run_eval_uses_eval_section_defaults(project_dir):
    (project_dir / "evals.yaml").write_text(mocks.EVALUATORS_YAML, encoding="utf-8")
    (project_dir / "my-evals.yaml").write_text("- key: other\n  type: exact_match\n", encoding="utf-8")
    toml = project_dir / ".evalp.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[eval]\ndataset = "dataset/support-regression"\nevaluators = "my-evals.yaml"\n'
        'repeats = 2\nbaseline = "baseline"\nconcurrency = 4\nversions = ["v2"]\n',
        encoding="utf-8",
    )
    server = build_server(
        project_dir=project_dir, client_factory=lambda p: EvalpClient("http://test", "tok"), poll_interval=0
    )
    with respx.mock(base_url=MOCK_API, assert_all_called=False) as api:
        created = api.post("/evals").mock(
            return_value=httpx.Response(
                201, json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "ver_1"}, {"id": "evr_b", "version_id": "ver_2"}]}  # noqa: E501
            )
        )  # fmt: skip
        async with Client(server) as c:
            r = await c.call_tool("run_eval", {"wait": False})  # 什么参数都不传
            assert not r.is_error, text(r)
            body = json.loads(created.calls[0].request.content)
            assert body["version_ids"] == ["support:baseline", "support:v2"]
            assert body["dataset"] == "asset://dataset/support-regression@latest"
            assert body["repeats"] == 2 and body["concurrency"] == 4
            assert body["evaluators"][0]["key"] == "other"
            # 工具参数优先于 [eval]
            await c.call_tool(
                "run_eval",
                {"dataset": "other", "repeats": 5, "baseline": "v1", "versions": ["v3"], "concurrency": 9,
                 "evaluators": [{"key": "k", "type": "contains"}], "wait": False},
            )  # fmt: skip
            body = json.loads(created.calls[1].request.content)
            assert body["version_ids"] == ["support:v1", "support:v3"]
            assert body["dataset"] == "asset://dataset/other@latest"
            assert body["repeats"] == 5 and body["concurrency"] == 9
            assert body["evaluators"] == [{"key": "k", "type": "contains"}]


async def test_run_eval_without_dataset_anywhere(mock_server):
    server, _ = mock_server
    async with Client(server) as c:
        r = await c.call_tool("run_eval", {})
    assert r.is_error and "[eval]" in text(r) and "dataset" in text(r)


# ---------------------------------------------------------------- 会话 / 重放 / 对比（真实应用）


@pytest.fixture
async def two_versions(client, fake_openai):
    """v1 的回复是"旧回复"，v2（提示词含"资深"）的回复是"新回复"。"""
    api = Api(client)
    out = await api.agent(
        "support",
        spec={
            "kind": "config",
            "model": {"provider": "fake", "name": "fake-model"},
            "prompts": {"system": "你是客服。"},
        },
    )
    await api.publish(out["draft"])
    draft = await api.save(out["draft"], {**out["draft"]["spec"], "prompts": {"system": "你是资深客服。"}})
    await api.publish(draft["draft"])
    fake_openai.add_rule(reply=lambda req: "新回复" if "资深" in str(req.messages) else "旧回复")


async def test_session_replay_and_compare_tools(real_server, two_versions, fake_openai, client):
    async with Client(real_server) as c:
        out = text(await c.call_tool("chat", {"message": "你好", "version": "v1"}))
        assert "旧回复" in out
        sid = out.split("session_id: `")[1].split("`")[0]
        await c.call_tool("chat", {"message": "再见", "session_id": sid})

        out = text(await c.call_tool("get_session", {"session_id": sid}))
        assert f"`{sid}`" in out and "共 2 轮" in out and "第 0 轮" in out and "第 1 轮" in out
        assert "用户: 你好" in out and "agent: 旧回复" in out and "support" in out
        out = text(await c.call_tool("get_session", {"session_id": sid, "turn": 1, "detail": "full"}))
        assert "第 1 轮" in out and "第 0 轮" not in out and "trace:" in out and "`llm.request`" in out
        assert "再见" in out
        out = text(await c.call_tool("get_session", {"session_id": sid, "turn": 9}))
        assert "没有第 9 轮" in out
        r = await c.call_tool("get_session", {"session_id": "ses_nope"})
        assert r.is_error

        # 用 v2 重放（等待结束），摘要里应有回复差异
        out = text(await c.call_tool("replay_session", {"session_id": sid, "target_version": "v2"}))
        assert "已创建重放" in out and "会话对比" in out and "共 2 轮对齐，其中 2 轮有差异" in out
        assert "-旧回复" in out and "+新回复" in out
        assert "尚未完成" not in out
        new_sid = out.split("新会话: `")[1].split("`")[0]
        assert new_sid != sid

        out = text(await c.call_tool("compare_sessions", {"left": sid, "right": new_sid, "max_turns": 1}))
        assert "会话对比" in out and "2 轮有差异" in out and "还有 1 轮有差异未展开" in out
        assert "-旧回复" in out and "+新回复" in out

        # 同一个会话与自己比较：没有差异
        out = text(await c.call_tool("compare_sessions", {"left": sid, "right": sid}))
        assert "其中 0 轮有差异" in out

        # 不等待：只返回新会话 id
        out = text(
            await c.call_tool(
                "replay_session", {"session_id": sid, "target_version": "v2", "wait": False, "turns": [0]}
            )
        )
        assert "已创建重放" in out and "会话对比" not in out and "get_session" in out
        r = await c.call_tool("replay_session", {"session_id": sid, "target_version": "v9"})
        assert r.is_error  # 版本不存在
