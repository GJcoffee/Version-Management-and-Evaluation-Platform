"""CLI 冒烟测试：typer CliRunner + respx（评测接口按契约 mock）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from evalp.cli.main import app
from typer.testing import CliRunner

from tests.integration.p4 import mocks

runner = CliRunner()
API = "http://test/api/v1"


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> Path:
    """项目目录 + 数据目录（含 token）；cwd 切到项目目录。"""
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text("tok-123", encoding="utf-8")
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(project_dir)
    (project_dir / "evals.yaml").write_text(mocks.EVALUATORS_YAML, encoding="utf-8")
    return project_dir


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as m:
        yield m


def invoke(*args: str, input: str | None = None):
    return runner.invoke(app, list(args), input=input)


def draft_json(rev: int) -> dict:
    return {"id": "drf_1", "name": "main", "revision": rev, "spec": {}}


def mock_push(api, rev: int = 1) -> respx.Route:
    api.post("/drafts/drf_1/validate").mock(
        return_value=httpx.Response(200, json={"ok": True, "errors": [], "warnings": []})
    )
    return api.post("/workspace/push").mock(
        return_value=httpx.Response(
            200,
            json={
                "agent_id": "agt_1", "agent_created": rev == 1, "draft": draft_json(rev),
                "uploaded": [{"path": "prompts/system.md", "asset": "prompt/support-system", "rev": 1, "existed": False}],  # noqa: E501
                "skipped": [],
            },
        )
    )  # fmt: skip


# ---------------------------------------------------------------- init


def test_init_generates_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    r = invoke("init", "--name", "My-Bot")
    assert r.exit_code == 0, r.output
    toml = (tmp_path / ".evalp.toml").read_text(encoding="utf-8")
    assert 'name = "My-Bot"' in toml and "prompt/my-bot-system" in toml
    assert "file:prompts/system.md" in (tmp_path / "agent.yaml").read_text(encoding="utf-8")
    assert (tmp_path / "prompts" / "system.md").read_text(encoding="utf-8").strip()
    assert (tmp_path / "datasets" / "regression.jsonl").is_file() and (tmp_path / "evals.yaml").is_file()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["*.pyc", ".evalp/"]
    # 生成的配置可以被客户端解析
    from evalp.workspace_client import find_project

    project = find_project(tmp_path)
    assert project.agent_name == "My-Bot"
    # 模板带 [eval] 默认配置（数据集 / 评估器指向 init 生成的文件），[mcp] 与其余默认值以注释给出
    assert project.eval == {"dataset": "dataset/my-bot-regression", "evaluators": "evals.yaml"}
    assert project.mcp_autostart is True and "# autostart = false" in toml and "# repeats = 3" in toml
    assert invoke("init").exit_code == 1  # 已存在
    assert invoke("init", "--force").exit_code == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(".evalp/") == 1


def test_init_code_kind(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert invoke("init", "--kind", "code").exit_code == 0
    assert "kind: code" in (tmp_path / "agent.yaml").read_text(encoding="utf-8")
    assert not (tmp_path / "prompts").exists()


def test_help_lists_all_commands():
    out = invoke("--help").output
    for cmd in ("serve", "init", "push", "pull", "publish", "versions", "diff", "chat", "eval", "mcp"):
        assert cmd in out
    assert "report" in invoke("eval", "--help").output


# ---------------------------------------------------------------- 工作区


def test_push_prints_summary(cli_env, api):
    route = mock_push(api)
    r = invoke("push")
    assert r.exit_code == 0, r.output
    assert "已创建 agent 并推送" in r.output and "revision 1" in r.output and "草稿校验: 通过" in r.output
    body = json.loads(route.calls[0].request.content)
    assert body["agent_name"] == "support" and body["spec"]["prompts"]["system"] == "file:prompts/system.md"
    assert route.calls[0].request.headers["authorization"] == "Bearer tok-123"
    # 状态已保存：再次推送，本地没变化，服务端 revision 一致 -> 不再推送
    api.get("/drafts/drf_1").mock(return_value=httpx.Response(200, json=draft_json(1)))
    r = invoke("push")
    assert "没有变化" in r.output and route.call_count == 1


def test_push_conflict_shows_diff_and_hint(cli_env, api):
    err = {
        "error": {
            "code": "DRAFT_CONFLICT", "message": "草稿已被修改（当前 revision=8）",
            "details": {"current_revision": 8, "diff": mocks.VERSION_DIFF},
        }
    }  # fmt: skip
    api.post("/workspace/push").mock(return_value=httpx.Response(409, json=err))
    r = invoke("push")
    assert r.exit_code == 1
    assert "冲突" in r.output and "model.params.temperature" in r.output
    assert "evalp pull" in r.output and "--force" in r.output


def test_server_down_hint(cli_env, api):
    api.post("/workspace/push").mock(side_effect=httpx.ConnectError("refused"))
    r = invoke("push")
    assert r.exit_code == 1 and "请先运行 evalp serve" in r.output


def test_missing_token_hint(cli_env, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = invoke("push", "--data-dir", str(empty))
    assert r.exit_code == 1 and "token" in r.output and "evalp serve" in r.output


def test_api_error_is_translated(cli_env, api):
    err = {"error": {"code": "SPEC_INVALID", "message": "Spec 校验失败",
                     "details": {"errors": [{"path": "model.provider", "message": "Provider 不存在: deepseek"}]}}}  # fmt: skip  # noqa: E501
    api.post("/workspace/push").mock(return_value=httpx.Response(400, json=err))
    r = invoke("push")
    assert r.exit_code == 1 and "Spec 校验失败" in r.output and "Provider 不存在" in r.output


def test_publish_reports_version_and_existed(cli_env, api):
    mock_push(api)
    version = {
        "id": "ver_2",
        "agent_id": "agt_1",
        "seq": 2,
        "spec_hash": "c" * 64,
        "parent_version_id": "ver_1",
        "tags": [],
    }
    pub = api.post("/drafts/drf_1/publish").mock(
        return_value=httpx.Response(200, json={"version": version, "existed": False})
    )
    api.get("/versions/diff").mock(return_value=httpx.Response(200, json=mocks.VERSION_DIFF))
    r = invoke("publish", "-m", "调温度", "--tag", "baseline")
    assert r.exit_code == 0, r.output
    assert "已发布版本 v2" in r.output and "baseline" in r.output and "3 项" in r.output
    sent = json.loads(pub.calls[0].request.content)
    assert sent["notes"] == "调温度" and sent["expected_revision"] == 1 and sent["via"] == "cli"
    assert sent["tag"] == "baseline"  # 标签随发布请求一并提交（服务端原子完成），不再单独调用打标签接口

    pub.mock(return_value=httpx.Response(200, json={"version": version, "existed": True}))
    api.get("/drafts/drf_1").mock(return_value=httpx.Response(200, json=draft_json(1)))
    r = invoke("publish")
    assert "版本已存在" in r.output and "v2" in r.output


def test_versions_and_diff(cli_env, api):
    api.get("/agents/support/versions").mock(
        return_value=httpx.Response(
            200,
            json={"items": [
                {"id": "ver_2", "seq": 2, "tags": ["prod"], "created_at": "2026-10-25T08:00:00.000Z",
                 "created_via": "cli", "notes": "调温度", "spec_hash": "b" * 64},
                {"id": "ver_1", "seq": 1, "tags": [], "created_at": "2026-10-24T08:00:00.000Z",
                 "created_via": "ui", "notes": None, "spec_hash": "a" * 64},
            ], "next_cursor": None},
        )
    )  # fmt: skip
    r = invoke("versions")
    assert r.exit_code == 0 and "v2" in r.output and "prod" in r.output and "调温度" in r.output

    route = api.get("/versions/diff").mock(return_value=httpx.Response(200, json=mocks.VERSION_DIFF))
    r = invoke("diff", "v1", "baseline")
    assert r.exit_code == 0, r.output
    assert dict(route.calls[0].request.url.params) == {"left": "support:v1", "right": "support:baseline"}
    for text in ("model.params.temperature", "-简洁回答", "+详细回答", "tool/extra@1"):
        assert text in r.output


def test_diff_against_draft(cli_env, api):
    """草稿作为一侧：@main 补全为 agent:@main，标题显示草稿名和 revision。"""
    draft_diff = {
        **mocks.VERSION_DIFF,
        "right": {"id": "drf_1", "draft": True, "name": "main", "revision": 4, "spec_hash": "c" * 64},
    }
    route = api.get("/versions/diff").mock(return_value=httpx.Response(200, json=draft_diff))
    r = invoke("diff", "v1", "@main")
    assert r.exit_code == 0, r.output
    assert dict(route.calls[0].request.url.params) == {"left": "support:v1", "right": "support:@main"}
    assert "v1 -> 草稿 main(r4)" in r.output
    r = invoke("diff", "v1", "drf_1")
    assert dict(route.calls[1].request.url.params)["right"] == "drf_1"


# ---------------------------------------------------------------- chat


def sse(*frames: tuple[str, dict]) -> httpx.Response:
    body = "".join(f"event: {ev}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for ev, d in frames)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def test_chat_streams_reply_and_tool_calls(cli_env, api):
    api.post("/sessions").mock(
        return_value=httpx.Response(
            201, json={"session": {"id": "ses_1", "agent_name": "support", "version_seq": 3}}
        )
    )
    turn = api.post("/sessions/ses_1/turns").mock(
        return_value=httpx.Response(
            202,
            json={"turn_id": "trn_1", "stream_url": "/api/v1/sessions/ses_1/stream?after_seq=0", "turn": {}},
        )
    )
    t = {"turn_id": "trn_1"}
    api.get("/sessions/ses_1/stream").mock(
        return_value=sse(
            ("turn.start", {**t, "type": "turn.start", "payload": {}}),
            ("tool.call", {**t, "payload": {"name": "query_order", "arguments": {"order_id": "A001"}}}),
            ("tool.result", {**t, "payload": {"name": "query_order", "output": {"status": "已发货"}, "latency_ms": 12}}),  # noqa: E501
            ("llm.delta", {**t, "payload": {"text": "订单"}}),
            ("llm.delta", {**t, "payload": {"text": "已发货"}}),
            ("message.assistant", {**t, "payload": {"message": {"role": "assistant", "content": "订单已发货"}}}),  # noqa: E501
            ("turn.end", {**t, "payload": {"status": "ok", "latency_ms": 1500, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}}),  # noqa: E501
        )
    )  # fmt: skip
    r = invoke("chat", "--version", "v3", input="我的订单 A001 呢\n/exit\n")
    assert r.exit_code == 0, r.output
    assert "support v3" in r.output
    assert "调用工具 query_order" in r.output and "已发货" in r.output
    assert "订单已发货" in r.output and "1.5s" in r.output
    assert json.loads(turn.calls[0].request.content) == {"input": "我的订单 A001 呢"}
    sess = json.loads(api.calls[0].request.content)
    assert sess == {"version_id": "support:v3"}


def test_chat_shows_agent_error_frames(cli_env, api):
    """业务错误事件在 SSE 线上叫 agent.error（旧版服务端是 error，也兼容）。"""
    api.post("/sessions").mock(
        return_value=httpx.Response(
            201, json={"session": {"id": "ses_1", "agent_name": "support", "version_seq": 3}}
        )
    )
    api.post("/sessions/ses_1/turns").mock(
        return_value=httpx.Response(
            202,
            json={"turn_id": "trn_1", "stream_url": "/api/v1/sessions/ses_1/stream?after_seq=0", "turn": {}},
        )
    )
    t = {"turn_id": "trn_1"}
    for name in ("agent.error", "error"):
        api.get("/sessions/ses_1/stream").mock(
            return_value=sse(
                ("turn.start", {**t, "type": "turn.start", "payload": {}}),
                (name, {**t, "type": "error", "payload": {"code": "LLM_UNAVAILABLE", "message": "模型挂了"}}),
                ("turn.end", {**t, "payload": {"status": "error", "latency_ms": 10}}),
            )
        )
        r = invoke("chat", "--version", "v3", input="你好\n/exit\n")
        assert r.exit_code == 0, r.output
        assert "出错 LLM_UNAVAILABLE" in r.output and "模型挂了" in r.output


# ---------------------------------------------------------------- eval


def mock_eval_backend(api, polls: int = 2):
    created = api.post("/evals").mock(
        return_value=httpx.Response(
            201,
            json={"batch_id": "evb_1", "runs": [
                {"id": "evr_a", "version_id": "ver_1", "status": "queued"},
                {"id": "evr_b", "version_id": "ver_2", "status": "queued"},
            ]},
        )
    )  # fmt: skip
    states = {
        "evr_a": [mocks.run("evr_a", 1, "running", done=2)] * (polls - 1)
        + [mocks.run("evr_a", 1, pass_rate=0.75)],
        "evr_b": [mocks.run("evr_b", 2, "running", done=1)] * (polls - 1)
        + [mocks.run("evr_b", 2, pass_rate=0.5)],
    }
    for rid, seq in states.items():
        api.get(f"/evals/{rid}").mock(
            side_effect=[httpx.Response(200, json=s) for s in seq] + [httpx.Response(200, json=seq[-1])] * 5
        )
    api.get("/evals/compare").mock(return_value=httpx.Response(200, json=mocks.compare()))
    api.get("/evals/evr_b/items").mock(
        return_value=httpx.Response(
            200, json={"items": [mocks.item("refund-001", False, 0.33)], "next_cursor": None}
        )
    )
    api.get("/evals/evr_a/items").mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    api.get("/evals/evr_r1").mock(
        return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})
    )
    for iid in ("evi_l1", "evi_r1"):
        api.get(url__regex=rf".*/items/{iid}$").mock(
            return_value=httpx.Response(
                200, json=mocks.item("refund-001", iid == "evi_l1", 1.0 if iid == "evi_l1" else 0.33)
            )
        )
    return created


def test_eval_run_wait_prints_progress_and_summary(cli_env, api):
    created = mock_eval_backend(api)
    r = invoke(
        "eval", "run", "--dataset", "support-regression", "--versions", "v1,v2", "--repeats", "2",
        "--wait", "--poll-interval", "0",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    body = json.loads(created.calls[0].request.content)
    assert body["version_ids"] == ["support:v1", "support:v2"]
    assert body["dataset"] == "asset://dataset/support-regression@latest" and body["repeats"] == 2
    assert body["evaluators"][0]["key"] == "kw"
    assert "evr_a" in r.output and "4/4" in r.output
    assert "通过率" in r.output and "75.0%" in r.output
    # 多个版本：以第一个为基线给出对比摘要，退化用例突出
    assert "评测对比" in r.output and "退化 2" in r.output and "refund-001" in r.output


def test_eval_run_without_wait_returns_ids(cli_env, api):
    created = mock_eval_backend(api)
    r = invoke("eval", "run", "--dataset", "dataset/x@3", "--baseline", "baseline", "--versions", "v2")
    assert r.exit_code == 0, r.output
    body = json.loads(created.calls[0].request.content)
    assert body["version_ids"] == ["support:baseline", "support:v2"]
    assert body["dataset"] == "asset://dataset/x@3"
    assert "evr_a" in r.output and "eval report" in r.output


def test_eval_run_needs_evaluators(cli_env, api):
    (cli_env / "evals.yaml").unlink()
    r = invoke("eval", "run", "--dataset", "x")
    assert r.exit_code == 1 and "evals.yaml" in r.output


def test_eval_report_md_and_json(cli_env, api):
    api.get("/evals/evr_b").mock(return_value=httpx.Response(200, json=mocks.run("evr_b", 2, pass_rate=0.25)))
    items = api.get("/evals/evr_b/items").mock(
        return_value=httpx.Response(
            200, json={"items": [mocks.item("refund-001", False, 0.33), mocks.item("hours-001", False, 0.0)], "next_cursor": None}  # noqa: E501
        )
    )  # fmt: skip
    r = invoke("eval", "report", "evr_b")
    assert r.exit_code == 0, r.output
    assert "25.0%" in r.output and "refund-001" in r.output and "回复里没有提到 7 天" in r.output
    assert r.output.index("hours-001") < r.output.index("refund-001")  # 得分低的在前
    assert "passed" in items.calls[0].request.url.params
    r = invoke("eval", "report", "evr_b", "--format", "json")
    data = json.loads(r.output)
    assert data["run"]["id"] == "evr_b" and len(data["failed"]) == 2


def test_eval_compare_md_and_json(cli_env, api):
    api.get("/evals/compare").mock(return_value=httpx.Response(200, json=mocks.compare()))
    api.get(url__regex=r".*/evals/evr_[ab]/items/evi_[lr]1$").mock(
        return_value=httpx.Response(200, json=mocks.item("refund-001", False, 0.33))
    )
    r = invoke("eval", "compare", "evr_a", "evr_b")
    assert r.exit_code == 0, r.output
    assert (
        "退化 2" in r.output and "-0.25" in r.output and "refund-001" in r.output and "hours-001" in r.output
    )
    assert "数据集修订不同" in r.output
    r = invoke("eval", "compare", "evr_a", "evr_b", "--format", "json", "--class", "regressed")
    assert json.loads(r.output)["summary"]["regressed"] == 2
