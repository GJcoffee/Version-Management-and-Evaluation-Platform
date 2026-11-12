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
    "chat", "run_eval", "cancel_eval", "get_eval_report", "get_eval_compare",
    "list_agents", "get_draft", "update_draft", "list_assets", "upsert_asset", "import_dataset",
    "get_eval_status", "get_session", "refresh_trace", "replay_session", "compare_sessions",
    "get_conventions",
    "list_adapters", "get_adapter_environments", "check_adapter_connection", "update_test_adapter",
    "push_adapter_to_production", "rollback_production_adapter", "list_adapter_credentials",
    "create_adapter_credential", "get_version", "set_milestone", "unarchive_version",
    "list_middlewares", "get_artifact_candidates",
    "migrate_adapter_bindings", "get_adapter_binding_migration",
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


async def test_registers_all_platform_tools(project_dir):
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
    unarchive = next(t for t in tools if t.name == "unarchive_version")
    assert unarchive.input_schema["required"] == ["version"]
    assert unarchive.input_schema["properties"]["agent"]["default"] is None


async def test_trace_refresh_tool_passes_explicit_force_and_original_configuration(mock_server):
    server, routes = mock_server
    route = routes.post("/turns/trn_existing/trace/refresh").mock(
        return_value=httpx.Response(200, json={"id": "trn_existing", "usage": {"prompt_tokens": 10}})
    )
    async with Client(server) as c:
        result = await c.call_tool("refresh_trace", {
            "turn_id": "trn_existing", "config_source": "original", "force": True,
        })
    assert not result.is_error
    assert json.loads(text(result))["usage"]["prompt_tokens"] == 10
    assert json.loads(route.calls.last.request.content) == {
        "config_source": "original", "environment": None, "credential_ref": None, "force": True,
    }


async def test_adapter_tools_edit_test_push_and_rollback_production(project_dir, client, provider):
    """通过 MCP 创建凭据、检查、编辑测试、推送生产并回滚，读回两个环境。"""
    server = build_server(project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as c:
        registry = json.loads(text(await c.call_tool("list_adapters", {})))
        assert registry[0]["id"] == "hiagent"
        credential = json.loads(text(await c.call_tool("create_adapter_credential", {
            "name": "mcp-test", "secret": "test-private-key",
        })))
        binding = {"id": "hiagent", "version": "1.0.0", "credential_ref": credential["ref"], "config": {
            "base_url": "https://example.com", "app_id": "a", "user_id": "u", "published_version": "47",
        }}
        with respx.mock() as upstream:
            upstream.post("https://example.com/api/v1/query_run_app_process").mock(
                return_value=httpx.Response(200, json={"status": "failed"}))
            check = json.loads(text(await c.call_tool("check_adapter_connection", {"binding": binding})))
        assert check["ok"] and check["details"]["llm_requested"] is False
        edited = json.loads(text(await c.call_tool("update_test_adapter", {
            "binding": binding, "expected_revision": 0, "reason": "MCP配置",
        })))
        assert edited["applied"] and edited["target"]["revision"] == 1
        pushed = json.loads(text(await c.call_tool("push_adapter_to_production", {"test_revision": 1})))
        assert pushed["applied"] and pushed["target"]["source"] == "push"
        state = json.loads(text(await c.call_tool("get_adapter_environments", {})))
        assert state["production"]["revision"] == 1 and state["test_selectable"] is False
        assert "test-private-key" not in str(state)
        binding["config"]["published_version"] = "48"
        await c.call_tool("update_test_adapter", {
            "binding": binding, "expected_revision": 1, "reason": "升级"})
        await c.call_tool("push_adapter_to_production", {"test_revision": 2})
        restored = json.loads(text(await c.call_tool("rollback_production_adapter", {"revision": 1})))
        assert restored["target"]["revision"] == 3
        assert restored["target"]["binding"]["config"]["published_version"] == "47"


async def test_chat_with_test_environment_is_sent_to_the_server(mock_server):
    server, api = mock_server
    sess = api.post("/sessions").mock(return_value=httpx.Response(
        201, json={"session": {"id": "ses_1", "version_seq": 2, "agent_name": "support"}}))
    api.post("/sessions/ses_1/turns").mock(return_value=httpx.Response(
        200, json={"turn_id": "t", "stream_url": "", "turn": {"id": "t", "status": "ok", "output": {
            "final": {"role": "assistant", "content": "好"}, "messages": []}}}))
    async with Client(server) as c:
        await c.call_tool("chat", {"message": "hi", "version": "v2", "environment": "test"})
    assert json.loads(sess.calls[0].request.content) == {"version_id": "support:v2", "environment": "test"}


async def test_chat_and_run_eval_accept_environment(project_dir):
    schemas = {t.name: t.input_schema["properties"] for t in (await _tools(project_dir))}
    for name in ("chat", "run_eval", "replay_session"):
        assert schemas[name]["environment"]["default"] == "production"
        assert schemas[name]["environment"]["enum"] == ["production", "test"]


async def _tools(project_dir):
    async with Client(build_server(project_dir=project_dir)) as c:
        return (await c.list_tools()).tools


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
    post = api.post("/evals").mock(
        return_value=httpx.Response(
            201, json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "ver_1"}]}
        )
    )
    api.get("/evals/evr_a").mock(
        return_value=httpx.Response(200, json=mocks.run("evr_a", 1, "running", done=1))
    )
    async with Client(server) as c:
        out = text(await c.call_tool("run_eval", {
            "dataset": "d", "wait": False,
            "turn_middlewares": {"2": [{"id": "json_artifact", "config": {}}]},
        }))
        assert "get_eval_report" in out and "evr_a" in out
    assert json.loads(post.calls.last.request.content)["turn_middlewares"]["2"][0]["id"] == "json_artifact"


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
    from pathlib import Path

    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "evalp", "mcp"], cwd=str(project_dir),
        env={"EVALP_DATA_DIR": str(tmp_path / "nodata"), "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "backend")},
    )  # fmt: skip
    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS
        r = await c.call_tool("list_versions", {})  # 服务没启动：给出可操作的错误，而不是崩溃
        assert r.is_error and "evalp serve" in text(r)
