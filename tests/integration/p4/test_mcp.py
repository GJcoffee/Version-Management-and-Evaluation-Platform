"""MCP Server：工具注册与调用（SDK 内存会话）。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from evalp.client import EvalpClient
from evalp.mcp.server import build_server
from mcp import Client

from tests.integration.p2a.conftest import API
from tests.integration.p4 import mocks

TOOLS = {
    "push_draft", "pull_draft", "publish_version", "list_versions", "diff_versions",
    "chat", "run_eval", "get_eval_report", "get_eval_compare",
    "list_agents", "get_draft", "update_draft", "list_assets", "upsert_asset", "import_dataset",
    "get_eval_status", "get_session", "replay_session", "compare_sessions", "get_conventions",
}  # fmt: skip
MOCK_API = "http://test/api/v1"


def text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


@pytest.fixture
def mock_server(project_dir):
    """工具调用打到 respx mock 上（评测接口按契约回放）。"""
    (project_dir / "evals.yaml").write_text(mocks.EVALUATORS_YAML, encoding="utf-8")
    server = build_server(
        project_dir=project_dir,
        client_factory=lambda project: EvalpClient("http://test", "tok"),
        poll_interval=0,
    )
    with respx.mock(base_url=MOCK_API, assert_all_called=False) as m:
        yield server, m


async def test_registers_exactly_the_twenty_tools(project_dir):
    server = build_server(project_dir=project_dir)
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.description and t.input_schema["type"] == "object"
    run_eval = next(t for t in tools if t.name == "run_eval")
    assert not run_eval.input_schema.get("required")  # 全部参数都可以省略（dataset 等取 [eval] 默认值）
    update = next(t for t in tools if t.name == "update_draft")
    assert update.input_schema["required"] == ["patch", "expected_revision"]


async def test_push_and_list_versions_against_real_app(project_dir, client, provider):
    """push_draft / publish_version / list_versions / diff_versions 打到进程内的真实应用。"""
    server = build_server(project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as c:
        out = text(await c.call_tool("push_draft", {}))
        assert "已推送草稿" in out and "revision: 1" in out and "prompts/system.md" in out and "通过" in out
        out = text(await c.call_tool("push_draft", {}))
        assert "没有变化" in out

        out = text(await c.call_tool("publish_version", {"notes": "初版", "tag": "baseline"}))
        assert "已发布版本 v1" in out and "baseline" in out
        # 改 prompt 再发布一版
        (project_dir / "prompts" / "system.md").write_text("你是资深客服。\n", encoding="utf-8")
        out = text(await c.call_tool("publish_version", {"notes": "改 prompt"}))
        assert "已发布版本 v2" in out and "prompts.system" in out
        out = text(await c.call_tool("publish_version", {}))
        assert "版本已存在：v2" in out

        out = text(await c.call_tool("list_versions", {}))
        assert "| v2 |" in out and "| v1 | baseline" in out and "改 prompt" in out
        out = text(await c.call_tool("diff_versions", {"left": "baseline", "right": "v2"}))
        assert "v1 -> v2" in out and "你是资深客服" in out and "```diff" in out

        out = text(await c.call_tool("pull_draft", {}))
        assert "已拉取草稿" in out


async def test_push_conflict_is_a_tool_error(project_dir, client, provider):
    server = build_server(project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as c:
        await c.call_tool("push_draft", {})
        (project_dir / "prompts" / "system.md").write_text("改了\n", encoding="utf-8")
        # 别处改了草稿
        drafts = (await client.get(f"{API}/agents/support/drafts")).json()
        d = drafts[0]
        spec = {**d["spec"], "model": {"provider": "deepseek", "name": "other"}}
        assert (
            await client.put(f"{API}/drafts/{d['id']}", json={"spec": spec, "expected_revision": 1})
        ).status_code == 200
        r = await c.call_tool("push_draft", {})
        assert r.is_error
        msg = text(r)
        assert "草稿冲突" in msg and "model.name" in msg and "force=true" in msg
        assert not (await c.call_tool("push_draft", {"force": True})).is_error


async def test_error_without_project(tmp_path):
    server = build_server(project_dir=tmp_path)
    async with Client(server) as c:
        r = await c.call_tool("push_draft", {})
    assert r.is_error and "evalp init" in text(r)


async def test_server_down_message(mock_server):
    server, api = mock_server
    api.get("/agents/support/versions").mock(side_effect=httpx.ConnectError("refused"))
    async with Client(server) as c:
        r = await c.call_tool("list_versions", {})
    assert r.is_error and "请先运行 evalp serve" in text(r)


async def test_run_eval_waits_and_summarises(mock_server):
    server, api = mock_server
    created = api.post("/evals").mock(
        return_value=httpx.Response(
            201,
            json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "ver_1"}, {"id": "evr_b", "version_id": "ver_2"}]},  # noqa: E501
        )
    )  # fmt: skip
    api.get("/evals/evr_a").mock(
        side_effect=[httpx.Response(200, json=mocks.run("evr_a", 1, "running", done=1)), httpx.Response(200, json=mocks.run("evr_a", 1, pass_rate=0.75))]  # noqa: E501
    )  # fmt: skip
    api.get("/evals/evr_b").mock(return_value=httpx.Response(200, json=mocks.run("evr_b", 2, pass_rate=0.5)))
    api.get("/evals/evr_b/items").mock(
        return_value=httpx.Response(
            200, json={"items": [mocks.item("refund-001", False, 0.33)], "next_cursor": None}
        )
    )
    api.get("/evals/compare").mock(return_value=httpx.Response(200, json=mocks.compare()))
    api.get(url__regex=r".*/items/evi_[lr]1$").mock(
        return_value=httpx.Response(200, json=mocks.item("refund-001", False, 0.33))
    )
    async with Client(server) as c:
        r = await c.call_tool(
            "run_eval", {"dataset": "support-regression", "versions": ["v2"], "baseline": "baseline"}
        )
    out = text(r)
    assert not r.is_error, out
    body = json.loads(created.calls[0].request.content)
    assert body["version_ids"] == ["support:baseline", "support:v2"] and body["via"] == "mcp"
    assert body["evaluators"][0]["key"] == "kw"  # 默认读取项目根目录的 evals.yaml
    assert "评测已发起" in out and "http://test/evals/evr_a" in out
    assert "未通过的用例" in out and "refund-001" in out and "回复里没有提到 7 天" in out
    assert "评测对比" in out and "退化 2" in out and "http://test/evals/compare?left=evr_a&right=evr_b" in out


async def test_run_eval_no_wait(mock_server):
    server, api = mock_server
    api.post("/evals").mock(
        return_value=httpx.Response(
            201, json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "ver_1"}]}
        )
    )
    api.get("/evals/evr_a").mock(
        return_value=httpx.Response(200, json=mocks.run("evr_a", 1, "running", done=1))
    )
    async with Client(server) as c:
        out = text(await c.call_tool("run_eval", {"dataset": "d", "wait": False}))
        assert "get_eval_report" in out and "evr_a" in out


async def test_run_eval_wait_timeout_reports_progress(project_dir):
    (project_dir / "evals.yaml").write_text(mocks.EVALUATORS_YAML, encoding="utf-8")
    server = build_server(
        project_dir=project_dir,
        client_factory=lambda p: EvalpClient("http://test", "tok"),
        poll_interval=0.01,
        wait_timeout=0.05,
    )
    with respx.mock(base_url=MOCK_API, assert_all_called=False) as api:
        api.post("/evals").mock(
            return_value=httpx.Response(
                201, json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "ver_1"}]}
            )
        )
        api.get("/evals/evr_a").mock(
            return_value=httpx.Response(200, json=mocks.run("evr_a", 1, "running", done=1))
        )
        async with Client(server) as c:
            out = text(await c.call_tool("run_eval", {"dataset": "d"}))
    assert "仍在运行" in out and "1/4" in out and "get_eval_report" in out


async def test_get_eval_report_and_compare(mock_server):
    server, api = mock_server
    api.get("/evals/evr_b").mock(return_value=httpx.Response(200, json=mocks.run("evr_b", 2, pass_rate=0.25)))
    items = [mocks.item(f"case-{i}", False, 0.1 * i) for i in range(1, 6)]
    api.get("/evals/evr_b/items").mock(
        return_value=httpx.Response(200, json={"items": items, "next_cursor": None})
    )
    api.get("/evals/compare").mock(return_value=httpx.Response(200, json=mocks.compare()))
    api.get(url__regex=r".*/items/evi_[lr]1$").mock(
        return_value=httpx.Response(200, json=mocks.item("refund-001", False, 0.33))
    )
    async with Client(server) as c:
        out = text(await c.call_tool("get_eval_report", {"run_id": "evr_b", "limit": 3}))
        assert (
            "前 3 条" in out
            and "case-1" in out
            and "case-3" in out
            and "case-4" not in out
            and "已截断" in out
        )
        out = text(
            await c.call_tool(
                "get_eval_compare", {"left": "evr_a", "right": "evr_b", "case_class": "regressed"}
            )
        )
        assert "退化的用例" in out and "refund-001" in out and "Δ -0.67" in out
        assert "改善的用例" not in out


async def test_chat_tool(mock_server):
    server, api = mock_server
    sess = api.post("/sessions").mock(
        return_value=httpx.Response(
            201, json={"session": {"id": "ses_1", "version_seq": 2, "agent_name": "support"}}
        )
    )
    turns = api.post("/sessions/ses_1/turns").mock(
        return_value=httpx.Response(
            200,
            json={"turn_id": "trn_1", "stream_url": "", "turn": {
                "id": "trn_1", "status": "ok", "latency_ms": 900, "usage": {"prompt_tokens": 12, "completion_tokens": 3},  # noqa: E501
                "output": {"final": {"role": "assistant", "content": "订单已发货"}, "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "name": "query_order", "arguments": {"order_id": "A001"}}]},  # noqa: E501
                    {"role": "tool", "tool_call_id": "c1", "content": "{\"status\": \"已发货\"}"},
                    {"role": "assistant", "content": "订单已发货"},
                ]},
            }},
        )
    )  # fmt: skip
    async with Client(server) as c:
        out = text(await c.call_tool("chat", {"message": "A001 呢", "version": "v2"}))
        assert (
            "订单已发货" in out
            and "query_order" in out
            and "ses_1" in out
            and "http://test/sessions/ses_1" in out
        )
        assert json.loads(sess.calls[0].request.content) == {"version_id": "support:v2"}
        assert json.loads(turns.calls[0].request.content) == {"input": "A001 呢", "wait": True}
        out = text(await c.call_tool("chat", {"message": "再问一句", "session_id": "ses_1"}))
        assert sess.call_count == 1 and "ses_1" in out


async def test_evalp_mcp_command_speaks_stdio(project_dir, tmp_path):
    """`evalp mcp` 作为子进程通过 stdio 对话（stdout 只能有协议内容）。"""
    import sys

    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "evalp", "mcp"], cwd=str(project_dir),
        env={"EVALP_DATA_DIR": str(tmp_path / "nodata"), "PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS
        r = await c.call_tool("list_versions", {})  # 服务没启动：给出可操作的错误，而不是崩溃
        assert r.is_error and "evalp serve" in text(r)
