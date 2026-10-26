"""CLI 其余命令：eval gate / status、asset、dataset import、replay、adapter check、gc，以及 [eval] 默认值。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from tests.integration.p4 import mocks
from tests.integration.p4.test_cli import api, cli_env, invoke  # noqa: F401 - 复用 fixture

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "评测不存在: evr_x"}}


def gate_json(passed: bool = True, **summary) -> dict:
    s = {
        "run_id": "evr_b", "status": "completed", "pass_rate": 0.9, "failed_items": 0,
        "baseline_run_id": "evr_a", "max_regressions": 0, "min_pass_rate": None,
        "baseline_status": "completed", "baseline_pass_rate": 0.95,
        "regressions": 0, "improved": 1, "unchanged": 3, "mean_delta": 0.1, "ci95": [-0.05, 0.25],
        "regressed_cases": [],
    }  # fmt: skip
    s.update(summary)
    reasons = [] if passed else ["相对基线有 2 个用例退化，超过允许的 0 个"]
    return {"passed": passed, "reasons": reasons, "summary": s}


# ---------------------------------------------------------------- eval gate


def test_gate_passes_with_exit_code_0(cli_env, api):  # noqa: F811
    route = api.get("/evals/evr_b/gate").mock(return_value=httpx.Response(200, json=gate_json(True)))
    r = invoke(
        "eval", "gate", "evr_b", "--baseline", "evr_a", "--max-regressions", "2",
        "--min-pass-rate", "0.8", "--threshold", "0.05",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert dict(route.calls[0].request.url.params) == {
        "baseline": "evr_a", "max_regressions": "2", "min_pass_rate": "0.8", "threshold": "0.05",
    }  # fmt: skip
    assert "门禁通过" in r.output and "90.0%" in r.output and "退化 0" in r.output


def test_gate_failure_exits_1_with_reasons(cli_env, api):  # noqa: F811
    bad = gate_json(
        False, regressions=2, regressed_cases=[{"case_key": "refund-001", "delta": -0.67, "item_ids": []}]
    )
    route = api.get("/evals/evr_b/gate").mock(return_value=httpx.Response(200, json=bad))
    r = invoke("eval", "gate", "--run", "evr_b", "--baseline", "evr_a")
    assert r.exit_code == 1, r.output
    assert "门禁未通过" in r.output and "退化 2" in r.output and "refund-001" in r.output
    assert "超过允许的 0 个" in r.output
    assert dict(route.calls[0].request.url.params) == {"baseline": "evr_a", "max_regressions": "0"}


def test_gate_json_output_is_machine_readable(cli_env, api):  # noqa: F811
    api.get("/evals/evr_b/gate").mock(return_value=httpx.Response(200, json=gate_json(False)))
    r = invoke("eval", "gate", "evr_b", "--json")
    assert r.exit_code == 1
    data = json.loads(r.stdout)  # stdout 只有 JSON
    assert data["passed"] is False and data["reasons"] and data["summary"]["run_id"] == "evr_b"
    api.get("/evals/evr_b/gate").mock(return_value=httpx.Response(200, json=gate_json(True)))
    r = invoke("eval", "gate", "evr_b", "--format", "json")
    assert r.exit_code == 0 and json.loads(r.stdout)["passed"] is True


def test_gate_argument_errors(cli_env, api):  # noqa: F811
    r = invoke("eval", "gate")
    assert r.exit_code == 2 and "Run id" in r.output
    r = invoke("eval", "gate", "evr_a", "--run", "evr_b")
    assert r.exit_code == 2 and "不同" in r.output
    assert invoke("eval", "gate", "evr_a", "--min-pass-rate", "1.5").exit_code == 2
    assert invoke("eval", "gate", "evr_a", "--max-regressions", "-1").exit_code == 2
    assert invoke("eval", "gate", "evr_a", "--run", "evr_a", "--help").exit_code == 0


def test_gate_api_and_connection_errors_are_not_silent_passes(cli_env, api):  # noqa: F811
    api.get("/evals/evr_x/gate").mock(return_value=httpx.Response(404, json=NOT_FOUND))
    r = invoke("eval", "gate", "evr_x")
    assert r.exit_code == 1 and "评测不存在" in r.output
    api.get("/evals/evr_b/gate").mock(side_effect=httpx.ConnectError("refused"))
    r = invoke("eval", "gate", "evr_b")
    assert r.exit_code == 1 and "请先运行 evalp serve" in r.output


def test_gate_wait_polls_run_and_baseline_first(cli_env, api):  # noqa: F811
    polls = {}
    for rid, seq in (("evr_a", 1), ("evr_b", 2)):
        polls[rid] = api.get(f"/evals/{rid}").mock(
            side_effect=[
                httpx.Response(200, json=mocks.run(rid, seq, "running", done=1)),
                httpx.Response(200, json=mocks.run(rid, seq)),
            ]
        )
    gate = api.get("/evals/evr_b/gate").mock(return_value=httpx.Response(200, json=gate_json(True)))
    r = invoke("eval", "gate", "evr_b", "--baseline", "evr_a", "--wait", "--poll-interval", "0")
    assert r.exit_code == 0, r.output
    # 先轮询两个 Run 直到都结束，再取门禁
    assert polls["evr_a"].call_count == 2 and polls["evr_b"].call_count == 2 and gate.call_count == 1


# ---------------------------------------------------------------- eval status / [eval] 默认值


def test_eval_status_shows_progress_and_eta(cli_env, api):  # noqa: F811
    started = (datetime.now(UTC) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    run = {**mocks.run("evr_a", 1, "running", done=2), "started_at": started}
    api.get("/evals/evr_a").mock(return_value=httpx.Response(200, json=run))
    r = invoke("eval", "status", "evr_a")
    assert r.exit_code == 0, r.output
    assert "运行中" in r.output and "2/4" in r.output and "预计剩余" in r.output and "1 分" in r.output
    r = invoke("eval", "status", "evr_a", "--format", "json")
    data = json.loads(r.stdout)
    assert data["run"]["id"] == "evr_a" and 55 < data["eta_seconds"] < 65
    api.get("/evals/evr_a").mock(return_value=httpx.Response(200, json=mocks.run("evr_a", 1)))
    r = invoke("eval", "status", "evr_a")
    assert "已完成" in r.output and "预计剩余" not in r.output


def test_eval_run_reads_eval_section_and_cli_overrides(cli_env, api):  # noqa: F811
    toml = cli_env / ".evalp.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[eval]\ndataset = "dataset/support-regression"\nevaluators = "mine.yaml"\n'
        'repeats = 2\nbaseline = "baseline"\nconcurrency = 4\n',
        encoding="utf-8",
    )
    (cli_env / "mine.yaml").write_text("- key: mine\n  type: exact_match\n", encoding="utf-8")
    created = api.post("/evals").mock(
        return_value=httpx.Response(
            201, json={"batch_id": "evb_1", "runs": [{"id": "evr_a", "version_id": "v"}, {"id": "evr_b", "version_id": "v"}]}  # noqa: E501
        )
    )  # fmt: skip
    r = invoke("eval", "run", "--versions", "v2")  # 没有 --dataset 也能跑
    assert r.exit_code == 0, r.output
    body = json.loads(created.calls[0].request.content)
    assert body["dataset"] == "asset://dataset/support-regression@latest"
    assert body["repeats"] == 2 and body["concurrency"] == 4
    assert body["evaluators"][0]["key"] == "mine"
    assert body["version_ids"] == ["support:baseline", "support:v2"]
    # 命令行参数优先
    r = invoke(
        "eval", "run", "--dataset", "other", "--repeats", "7", "--concurrency", "9",
        "--evaluators", "evals.yaml", "--baseline", "v1", "--versions", "v3",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    body = json.loads(created.calls[1].request.content)
    assert body["dataset"] == "asset://dataset/other@latest"
    assert body["repeats"] == 7 and body["concurrency"] == 9 and body["evaluators"][0]["key"] == "kw"
    assert body["version_ids"] == ["support:v1", "support:v3"]


def test_eval_run_without_dataset_says_where_to_set_it(cli_env, api):  # noqa: F811
    r = invoke("eval", "run")
    assert r.exit_code == 1 and "[eval]" in r.output and "--dataset" in r.output


def test_bad_eval_section_is_reported(cli_env, api):  # noqa: F811
    toml = cli_env / ".evalp.toml"
    base = toml.read_text(encoding="utf-8")
    for extra, expect in (
        ("[eval]\nrepeats = 0\n", "[eval].repeats"),
        ("[eval]\ndataset = 3\n", "[eval].dataset"),
        ('[eval]\ndatasets = "x"\n', "未知配置: datasets"),
        ('[mcp]\nautostart = "no"\n', "[mcp].autostart"),
    ):
        toml.write_text(base + "\n" + extra, encoding="utf-8")
        r = invoke("versions")
        assert r.exit_code == 1 and expect in r.output, (extra, r.output)


# ---------------------------------------------------------------- asset


def asset_json(kind="prompt", name="cs-system", rev=2, **kw) -> dict:
    return {
        "id": "ast_1", "kind": kind, "name": name, "description": "客服提示词", "latest_rev": rev,
        "deleted_at": None, "created_at": "2026-10-25T08:00:00.000Z",
        "latest": {"id": f"arv_{rev}", "rev": rev, "size": 2048, "content_type": "text/markdown"}, **kw,
    }  # fmt: skip


def revision_json(rev: int, content: str | None = "你是客服。\n") -> dict:
    return {
        "id": f"arv_{rev}", "asset_id": "ast_1", "rev": rev, "blob_hash": "h" * 64,
        "content_type": "text/markdown", "size": 12, "meta": {}, "message": "改称呼",
        "created_at": "2026-10-25T08:00:00.000Z", "kind": "prompt", "name": "cs-system",
        "content": content, "content_available": content is not None,
    }  # fmt: skip


def test_asset_ls(cli_env, api):  # noqa: F811
    route = api.get("/assets").mock(
        return_value=httpx.Response(200, json={"items": [asset_json(), asset_json("dataset", "reg", 1)], "next_cursor": None})  # noqa: E501
    )  # fmt: skip
    r = invoke("asset", "ls", "--kind", "prompt", "--q", "cs")
    assert r.exit_code == 0, r.output
    assert "prompt/cs-system" in r.output and "rev 2" in r.output and "2.0 KB" in r.output
    assert dict(route.calls[0].request.url.params) == {"kind": "prompt", "q": "cs", "limit": "50"}
    assert invoke("asset", "ls", "--kind", "bogus").exit_code == 2
    route.mock(return_value=httpx.Response(200, json={"items": [], "next_cursor": None}))
    assert "没有匹配的资产" in invoke("asset", "ls").output


def test_asset_show_prints_content_to_stdout(cli_env, api):  # noqa: F811
    api.get("/assets").mock(
        return_value=httpx.Response(200, json={"items": [asset_json()], "next_cursor": None})
    )
    rev = api.get("/assets/ast_1/revisions/latest").mock(
        return_value=httpx.Response(200, json=revision_json(2))
    )
    r = invoke("asset", "show", "prompt/cs-system")
    assert r.exit_code == 0, r.output
    assert r.stdout == "你是客服。\n"  # 内容可直接重定向到文件
    assert "prompt/cs-system" in r.stderr and "rev 2/2" in r.stderr and "改称呼" in r.stderr
    api.get("/assets/ast_1/revisions/1").mock(
        return_value=httpx.Response(200, json=revision_json(1, "旧内容"))
    )
    assert invoke("asset", "show", "asset://prompt/cs-system@1").stdout == "旧内容\n"
    assert invoke("asset", "show", "prompt/cs-system", "--rev", "1").stdout == "旧内容\n"
    assert rev.call_count == 1
    api.get("/assets/ast_1/revisions/latest").mock(
        return_value=httpx.Response(200, json=revision_json(2, None))
    )
    r = invoke("asset", "show", "prompt/cs-system")
    assert r.exit_code == 1 and "无法直接显示" in r.output
    r = invoke("asset", "show", "prompt/missing")
    assert r.exit_code == 1 and "资产不存在" in r.output
    r = invoke("asset", "show", "nokind")
    assert r.exit_code == 1 and "kind/name" in r.output


def test_asset_add_creates_or_appends(cli_env, api):  # noqa: F811
    route = api.post("/assets").mock(
        return_value=httpx.Response(
            201,
            json={
                "asset": asset_json(rev=1),
                "revision": revision_json(1),
                "existed": False,
                "created": True,
            },
        )
    )
    r = invoke("asset", "add", "prompt/cs-system", "prompts/system.md", "-m", "初版", "--description", "d")
    assert r.exit_code == 0, r.output
    assert "已创建" in r.output and "asset://prompt/cs-system@latest" in r.output
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "kind": "prompt",
        "name": "cs-system",
        "message": "初版",
        "description": "d",
        "content": "你是客服助手。\n",
    }  # noqa: E501
    route.mock(
        return_value=httpx.Response(
            200, json={"asset": asset_json(), "revision": revision_json(2), "existed": True, "created": False}
        )
    )
    assert "内容未变化" in invoke("asset", "add", "prompt/cs-system", "prompts/system.md").output
    route.mock(
        return_value=httpx.Response(
            201,
            json={"asset": asset_json(), "revision": revision_json(3), "existed": False, "created": False},
        )
    )
    assert "已新增修订" in invoke("asset", "add", "prompt/cs-system", "prompts/system.md").output
    r = invoke("asset", "add", "prompt/cs-system", "nope.md")
    assert r.exit_code == 1 and "找不到文件" in r.output
    r = invoke("asset", "add", "prompt/cs-system@2", "prompts/system.md")
    assert r.exit_code == 1 and "不带修订号" in r.output


def test_asset_add_binary_goes_through_upload(cli_env, api):  # noqa: F811
    (cli_env / "logo.bin").write_bytes(b"\xff\xfe\x00")
    up = api.post("/assets/upload").mock(
        return_value=httpx.Response(200, json={"blob_upload_id": "b" * 64, "size": 3})
    )
    route = api.post("/assets").mock(
        return_value=httpx.Response(
            201,
            json={
                "asset": asset_json("file", "logo", 1),
                "revision": revision_json(1),
                "existed": False,
                "created": True,
            },  # noqa: E501
        )
    )
    r = invoke("asset", "add", "file/logo", "logo.bin")
    assert r.exit_code == 0, r.output
    assert b"multipart/form-data" in up.calls[0].request.headers["content-type"].encode()
    assert json.loads(route.calls[0].request.content)["blob_upload_id"] == "b" * 64


def test_asset_diff_text_and_dataset(cli_env, api):  # noqa: F811
    api.get("/assets").mock(
        return_value=httpx.Response(200, json={"items": [asset_json()], "next_cursor": None})
    )
    api.get("/assets/ast_1/revisions/1").mock(return_value=httpx.Response(200, json=revision_json(1)))
    api.get("/assets/ast_1/revisions/2").mock(return_value=httpx.Response(200, json=revision_json(2)))
    head = {
        "kind": "prompt",
        "left": {"name": "cs-system", "rev": 1},
        "right": {"name": "cs-system", "rev": 2},
    }
    route = api.get("/assets/diff").mock(
        return_value=httpx.Response(
            200, json={**head, "identical": False, "type": "text",
                       "content_diff": {"format": "unified", "text": "@@ -1 +1 @@\n-你是客服\n+你是资深客服"}},  # noqa: E501
        )
    )  # fmt: skip
    r = invoke("asset", "diff", "prompt/cs-system")
    assert r.exit_code == 0, r.output
    assert dict(route.calls[0].request.url.params) == {
        "left": "arv_1",
        "right": "arv_2",
    }  # 默认：上一个 -> 最新
    assert (
        "cs-system@1 -> cs-system@2" in r.output and "-你是客服" in r.output and "+你是资深客服" in r.output
    )
    r = invoke("asset", "diff", "prompt/cs-system", "--from", "1", "--to", "2")
    assert r.exit_code == 0 and route.call_count == 2
    ds = {
        "summary": {
            "added": 1,
            "removed": 0,
            "modified": 1,
            "unchanged": 5,
            "left_count": 6,
            "right_count": 7,
        },
        "added": [{"case_key": "new-1"}],
        "removed": [],
        "modified": [{"case_key": "old-1", "changed_fields": ["expected"]}],
        "truncated": False,
    }  # noqa: E501, fmt: skip
    route.mock(
        return_value=httpx.Response(
            200, json={**head, "kind": "dataset", "identical": False, "type": "dataset", **ds}
        )
    )  # noqa: E501
    r = invoke("asset", "diff", "prompt/cs-system")
    assert "新增 1" in r.output and "+ new-1" in r.output and "~ old-1" in r.output and "expected" in r.output
    # 只有一个修订时没有可比较的
    api.get("/assets").mock(
        return_value=httpx.Response(200, json={"items": [asset_json(rev=1)], "next_cursor": None})
    )
    r = invoke("asset", "diff", "prompt/cs-system")
    assert r.exit_code == 1 and "只有 1 个修订" in r.output


# ---------------------------------------------------------------- dataset import / replay / adapter / gc


def test_dataset_import(cli_env, api):  # noqa: F811
    (cli_env / "q.csv").write_text("问题,答案\n你好,您好\n", encoding="utf-8")
    out = {
        "asset": asset_json("dataset", "qa", 1), "revision": revision_json(1), "case_count": 1, "existed": False,  # noqa: E501
        "created": True, "warnings": [{"line": 3, "message": "tags 不是数组，已忽略"}],
    }  # fmt: skip
    route = api.post("/datasets/import").mock(return_value=httpx.Response(201, json=out))
    r = invoke(
        "dataset",
        "import",
        "q.csv",
        "--name",
        "dataset/qa",
        "--map",
        "input=问题,expected=答案",
        "-m",
        "导入",
    )
    assert r.exit_code == 0, r.output
    assert "已创建数据集" in r.output and "1 个用例" in r.output and "dataset/qa@1" in r.output
    assert "第 3 行" in r.output and "tags 不是数组" in r.output
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "qa" and body["format"] == "csv" and body["message"] == "导入"
    assert body["column_map"] == {"input": "问题", "expected": "答案"} and "你好" in body["content"]
    # JSONL：按扩展名判断，用已有的示例数据集
    r = invoke("dataset", "import", "datasets/regression.jsonl", "--name", "reg")
    assert r.exit_code == 0 and json.loads(route.calls[1].request.content)["format"] == "jsonl"
    route.mock(
        return_value=httpx.Response(200, json={**out, "created": False, "existed": True, "warnings": []})
    )
    assert "内容未变化" in invoke("dataset", "import", "datasets/regression.jsonl", "--name", "reg").output
    assert invoke("dataset", "import", "nope.jsonl", "--name", "x").exit_code == 1
    r = invoke("dataset", "import", "q.csv", "--name", "x", "--map", "bad")
    assert r.exit_code == 1 and "字段=列名" in r.output
    assert invoke("dataset", "import", "q.csv", "--name", "x", "--format", "xml").exit_code == 2
    err = {"error": {"code": "DATASET_PARSE_ERROR", "message": "数据集解析失败",
                     "details": {"errors": [{"line": 1, "message": "找不到 input 列"}]}}}  # fmt: skip
    route.mock(return_value=httpx.Response(422, json=err))
    r = invoke("dataset", "import", "q.csv", "--name", "x")
    assert r.exit_code == 1 and "数据集解析失败" in r.output and "找不到 input 列" in r.output


def alignment(done: bool = True) -> dict:
    right_status = "ok" if done else "running"
    return {"turns": [{
        "pair": 0, "left_turn_id": "trn_l", "right_turn_id": "trn_r",
        "steps": [{"op": "equal", "left": "a", "right": "b", "sig": "llm"}, {"op": "insert", "right": "c", "sig": "tool:query"}],  # noqa: E501
        "tool_args_diff": [], "metrics": {"left": {"latency_ms": 1000, "tokens": 10}, "right": {"latency_ms": 1500, "tokens": 14}},  # noqa: E501
        "final_text": {"left": "旧回复", "right": "新回复"}, "status": {"left": "ok", "right": right_status},
        "input": {"left": "你好", "right": "你好"},
    }]}  # fmt: skip


GROUP = {
    "id": "cmp_1", "mode": "replay", "created_at": "x",
    "left": {"session_id": "ses_1", "version_id": "ver_1", "version_seq": 1, "agent_name": "support"},
    "right": {"session_id": "ses_2", "version_id": "ver_2", "version_seq": 2, "agent_name": "support"},
}  # fmt: skip


def test_replay_cli(cli_env, api):  # noqa: F811
    api.get("/sessions/ses_1").mock(
        return_value=httpx.Response(200, json={"session": {"id": "ses_1", "agent_name": "bot"}, "turns": []})
    )
    route = api.post("/sessions/ses_1/replay").mock(
        return_value=httpx.Response(201, json={"session_id": "ses_2", "compare_id": "cmp_1"})
    )
    r = invoke("replay", "ses_1", "--to", "v2", "--turns", "0, 2")
    assert r.exit_code == 0, r.output
    assert "已创建重放" in r.output and "ses_2" in r.output and "/compare/cmp_1" in r.output
    # --to 写 v2 时，agent 取项目里的（support），不需要先查会话
    assert json.loads(route.calls[0].request.content) == {
        "target_version_id": "support:v2", "mode": "anchored", "turns": [0, 2],
    }  # fmt: skip
    assert "会话对比" not in r.output

    align = api.get("/compare/cmp_1/alignment").mock(
        side_effect=[httpx.Response(200, json=alignment(False)), httpx.Response(200, json=alignment(True))]
    )
    api.get("/compare/cmp_1").mock(return_value=httpx.Response(200, json=GROUP))
    r = invoke("replay", "ses_1", "--to", "ver_9", "--wait", "--poll-interval", "0")
    assert r.exit_code == 0, r.output
    assert align.call_count == 2  # 第一次还在 running，等到完成
    assert "共 1 轮对齐，其中 1 轮有差异" in r.output and "-旧回复" in r.output and "+新回复" in r.output
    assert json.loads(route.calls[1].request.content)["target_version_id"] == "ver_9"
    assert invoke("replay", "ses_1", "--to", "v2", "--mode", "free").exit_code == 2
    r = invoke("replay", "ses_1", "--to", "v2", "--turns", "a")
    assert r.exit_code == 1 and "逗号分隔" in r.output


def test_replay_without_project_takes_agent_from_session(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text("tok", encoding="utf-8")
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    with respx.mock(base_url="http://127.0.0.1:7860/api/v1") as mock:
        mock.get("/sessions/ses_1").mock(
            return_value=httpx.Response(
                200, json={"session": {"id": "ses_1", "agent_name": "bot"}, "turns": []}
            )
        )
        route = mock.post("/sessions/ses_1/replay").mock(
            return_value=httpx.Response(201, json={"session_id": "ses_2", "compare_id": "cmp_1"})
        )
        assert invoke("replay", "ses_1", "--to", "v3").exit_code == 0
    assert json.loads(route.calls[0].request.content)["target_version_id"] == "bot:v3"


def test_adapter_check(cli_env, api):  # noqa: F811
    report = {
        "version_id": "ver_1", "agent_name": "support", "kind": "code", "transport": "http", "ok": True,
        "summary": {"pass": 2, "warn": 1}, "duration_ms": 1200,
        "items": [
            {"name": "health", "title": "健康检查", "status": "pass", "message": "", "duration_ms": 5},
            {"name": "single", "title": "单轮对话", "status": "pass", "message": "", "duration_ms": 50},
            {"name": "cancel", "title": "取消", "status": "warn", "message": "取消后 6 秒才结束", "duration_ms": 900},  # noqa: E501
        ],
    }  # fmt: skip
    route = api.post("/versions/support:v3/adapter-check").mock(return_value=httpx.Response(200, json=report))
    r = invoke("adapter", "check", "--version", "3")
    assert r.exit_code == 0, r.output
    assert "适配器自检：通过" in r.output and "[警告] 取消" in r.output and "取消后 6 秒才结束" in r.output
    assert route.call_count == 1
    bad = {**report, "ok": False, "summary": {"pass": 1, "fail": 1},
           "items": [{"name": "health", "title": "健康检查", "status": "fail", "message": "连接被拒绝"}]}  # fmt: skip  # noqa: E501
    api.post("/versions/support:latest/adapter-check").mock(return_value=httpx.Response(200, json=bad))
    r = invoke("adapter", "check")
    assert r.exit_code == 1 and "未通过" in r.output and "[失败] 健康检查" in r.output
    r = invoke("adapter", "check", "--format", "json")
    assert r.exit_code == 1 and json.loads(r.stdout)["ok"] is False


def test_gc(cli_env, api):  # noqa: F811
    api.post("/system/gc").mock(
        return_value=httpx.Response(
            200, json={"blobs_deleted": 3, "bytes_freed": 2048, "eval_sessions_deleted": 5}
        )
    )
    r = invoke("gc")
    assert r.exit_code == 0, r.output
    assert "删除 3 个 blob" in r.output and "2.0 KB" in r.output and "5 个超过保留期的评测会话" in r.output


def test_help_lists_new_commands():
    out = invoke("--help").output
    for cmd in ("asset", "dataset", "replay", "adapter", "gc"):
        assert cmd in out
    for sub in ("status", "gate"):
        assert sub in invoke("eval", "--help").output
    for sub in ("ls", "show", "add", "diff"):
        assert sub in invoke("asset", "--help").output
    assert "--no-autostart" in invoke("mcp", "--help").output
