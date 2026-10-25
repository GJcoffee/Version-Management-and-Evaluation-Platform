"""评测契约（docs/contract-eval.md 第 1、2 节）的 HTTP 层测试：字段形状、对比分类、SSE、缓存、评估器。"""

from __future__ import annotations

import json

from .conftest import API, make_cases

DS = "asset://dataset/ds@latest"


async def _post_eval(ev, versions: list[dict], evaluators: list[dict], **kw):
    body = {"version_ids": [v["id"] for v in versions], "dataset": DS, "evaluators": evaluators, "repeats": 1}
    body.update(kw)
    r = await ev.client.post(f"{API}/evals", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _wait(ev, run_id: str) -> dict:
    await ev.evals.wait_finished(run_id, timeout=60, interval=0.05)
    return (await ev.client.get(f"{API}/evals/{run_id}")).json()


async def _all_items(ev, run_id: str, query: str = "") -> list[dict]:
    r = await ev.client.get(f"{API}/evals/{run_id}/items?limit=200{query}")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_end_to_end_stats_and_compare(ev):
    """数据集 -> 两个版本 -> 发起评测 -> 跑完 -> stats -> 对比分类。"""
    v1 = await ev.config_version({"prompts": {"system": "版本一"}}, name="bot")
    v2 = await ev.config_version({"prompts": {"system": "版本二"}}, name="bot2")
    await ev.dataset("ds", make_cases(4))
    # c001：v1 答错、v2 答对（改善）；c000：v1 答对、v2 答错（退化）；其余两侧都对
    ev.fake.add_rule(match=lambda r: "版本一" in r.system and "问题1" in r.last_user, reply="答错了")
    ev.fake.add_rule(match=lambda r: "版本二" in r.system and "问题0" in r.last_user, reply="答错了")

    evaluators = [
        {"key": "exact", "type": "exact_match", "gate": True, "normalize": ["strip", "lower"]},
        {"key": "kw", "type": "contains", "values": ["问题"], "mode": "all", "gate": False},
    ]
    created = await _post_eval(ev, [v1, v2], evaluators, name="v2 回归", concurrency=4)
    assert created["batch_id"].startswith("evb_")
    assert [r["version_id"] for r in created["runs"]] == [v1["id"], v2["id"]]
    assert all(r["id"].startswith("evr_") and r["status"] == "queued" for r in created["runs"])

    left_id, right_id = (r["id"] for r in created["runs"])
    left, right = await _wait(ev, left_id), await _wait(ev, right_id)

    for run, seq_name in ((left, "bot"), (right, "bot2")):
        assert run["name"] == "v2 回归" and run["batch_id"] == created["batch_id"]
        assert run["agent_name"] == seq_name and run["version_seq"] == 1
        assert (
            run["dataset_name"] == "ds"
            and run["dataset_rev"] == 1
            and run["dataset_rev_id"].startswith("arv_")
        )
        assert run["status"] == "completed" and run["evaluators"][0]["key"] == "exact"
        assert run["repeats"] == 1 and run["concurrency"] == 4 and run["cache_policy"] == "reuse"
        assert run["progress"]["total"] == 4 and run["progress"]["done"] == 4
        assert run["progress"]["failed"] == 0 and run["progress"]["cached"] == 0
        assert run["started_at"] and run["finished_at"]
        st = run["stats"]
        assert st["cases"] == 4 and st["items"] == 4 and st["done"] == 4 and st["failed"] == 0
        assert st["pass_rate"] == 0.75 and st["error_rate"] == 0
        assert st["evaluators"]["exact"]["mean"] == 0.75 and st["evaluators"]["exact"]["errors"] == 0
        assert st["latency_ms"]["p50"] is not None and st["tokens"]["prompt"] > 0 and st["cost"] > 0

    # item 的契约字段
    items = await _all_items(ev, left_id)
    first = items[0]
    assert first["case_key"] == "c000" and first["case_id"].startswith("cas_") and first["tags"] == ["even"]
    assert first["input_preview"] == "问题0" and first["expected"] == "echo: 问题0"
    assert first["output_preview"] == "echo: 问题0" and first["passed"] is True and first["cached"] is False
    assert first["status"] == "done" and first["session_id"].startswith("ses_") and first["attempts"] == 1
    assert first["usage"]["prompt_tokens"] > 0 and first["latency_ms"] is not None and first["cost"] > 0
    sc = {x["evaluator_key"]: x for x in first["scores"]}
    assert sc["exact"]["status"] == "done" and sc["exact"]["score"] == 1.0 and sc["exact"]["passed"] is True
    assert "reason" in sc["exact"] and "raw_score" in sc["exact"]
    one = (await ev.client.get(f"{API}/evals/{left_id}/items/{first['id']}")).json()
    assert one["id"] == first["id"] and one["case_key"] == "c000"

    # 过滤：passed / q（含输出）
    bad = await _all_items(ev, left_id, "&passed=false")
    assert [i["case_key"] for i in bad] == ["c001"] and bad[0]["output_preview"] == "答错了"
    assert len(await _all_items(ev, left_id, "&passed=true")) == 3
    assert [i["case_key"] for i in await _all_items(ev, left_id, "&q=答错")] == ["c001"]
    assert [i["case_key"] for i in await _all_items(ev, left_id, "&q=c002")] == ["c002"]

    # 列表过滤的契约参数名
    by_batch = (await ev.client.get(f"{API}/evals?batch_id={created['batch_id']}")).json()["items"]
    assert {r["id"] for r in by_batch} == {left_id, right_id}
    assert len((await ev.client.get(f"{API}/evals?agent_id=bot")).json()["items"]) == 1

    # 对比
    r = await ev.client.get(f"{API}/evals/compare?left={left_id}&right={right_id}&threshold=0.05")
    assert r.status_code == 200, r.text
    cmp = r.json()
    assert (
        cmp["left"]["id"] == left_id
        and cmp["left"]["version_seq"] == 1
        and cmp["left"]["stats"]["cases"] == 4
    )
    assert cmp["right"]["id"] == right_id and cmp["warnings"] == []
    sm = cmp["summary"]
    assert (sm["improved"], sm["regressed"], sm["unchanged"], sm["new"], sm["missing"]) == (1, 1, 2, 0, 0)
    assert abs(sm["mean_delta"]) < 1e-9
    assert set(sm["metric_deltas"]) >= {"pass_rate", "latency_p50_ms", "cost", "tokens_total"}
    assert sm["metric_deltas"]["pass_rate"] == 0
    rows = cmp["cases"]["items"]
    assert [x["case_key"] for x in rows][:2] == ["c000", "c001"]  # regressed 在前
    assert rows[0]["class"] == "regressed" and rows[0]["delta"] == -1.0 and rows[0]["tags"] == ["even"]
    assert rows[0]["left"]["score"] == 1.0 and rows[0]["right"]["score"] == 0.0
    assert rows[0]["left"]["pass_rate"] == 1.0 and rows[0]["right"]["pass_rate"] == 0.0
    assert len(rows[0]["left"]["item_ids"]) == 1 and len(rows[0]["right"]["item_ids"]) == 1
    assert rows[1]["class"] == "improved" and {x["class"] for x in rows[2:]} == {"unchanged"}
    assert cmp["cases"]["next_cursor"] is None
    only = (
        await ev.client.get(f"{API}/evals/compare?left={left_id}&right={right_id}&class=regressed")
    ).json()
    assert [x["case_key"] for x in only["cases"]["items"]] == ["c000"]
    # 阈值很大时全部 unchanged
    wide = (await ev.client.get(f"{API}/evals/compare?left={left_id}&right={right_id}&threshold=2")).json()
    assert wide["summary"]["unchanged"] == 4


async def test_compare_warns_on_different_dataset_revision_and_new_missing(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(3))
    a = await _post_eval(ev, [v], [{"type": "exact_match", "key": "e"}])
    await ev.evals.wait_finished(a["runs"][0]["id"], timeout=60, interval=0.05)
    await ev.dataset("ds", make_cases(2, prefix="d"))  # 新修订：用例键完全不同
    b = await _post_eval(ev, [v], [{"type": "exact_match", "key": "e"}])
    await ev.evals.wait_finished(b["runs"][0]["id"], timeout=60, interval=0.05)
    cmp = (
        await ev.client.get(f"{API}/evals/compare?left={a['runs'][0]['id']}&right={b['runs'][0]['id']}")
    ).json()
    assert len(cmp["warnings"]) == 1 and "rev 1 vs 2" in cmp["warnings"][0]
    assert (cmp["summary"]["new"], cmp["summary"]["missing"]) == (2, 3)


async def test_cache_reuse_sends_no_agent_requests_and_only_reruns_evaluators(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    first = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}], seed=7)
    await _wait(ev, first["runs"][0]["id"])
    after_first = ev.calls()
    assert after_first == 4

    # 完全相同的评测：fake 收到 0 个新请求，全部命中缓存，会话复用
    second = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}], seed=7)
    run2 = await _wait(ev, second["runs"][0]["id"])
    assert ev.calls() == after_first
    assert (
        run2["progress"]["cached"] == 4 and run2["stats"]["cached"] == 4 and run2["stats"]["pass_rate"] == 1.0
    )
    items1 = {i["case_key"]: i for i in await _all_items(ev, first["runs"][0]["id"])}
    items2 = await _all_items(ev, second["runs"][0]["id"])
    assert all(i["cached"] is True and i["session_id"] == items1[i["case_key"]]["session_id"] for i in items2)

    # 换了评估器（加 llm_judge）：agent 不重跑，只多出评审请求
    ev.fake.add_rule(match=lambda r: "评审专家" in r.system, reply='{"score": 4, "reason": "不错"}')
    judge = {"key": "j", "type": "llm_judge", "provider": "fake", "model": "fake-model", "pass_threshold": 4}
    third = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}, judge], seed=7)
    run3 = await _wait(ev, third["runs"][0]["id"])
    assert run3["progress"]["cached"] == 4
    assert ev.calls() - after_first == 4  # 只有 4 次评审调用
    assert all("评审专家" in r.system for r in ev.fake.chat_requests[after_first:])

    # cache_policy=refresh 或 seed 不同：重新执行
    fourth = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}], seed=7, cache_policy="refresh")
    await _wait(ev, fourth["runs"][0]["id"])
    assert ev.calls() - after_first == 8
    fifth = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}], seed=8)
    await _wait(ev, fifth["runs"][0]["id"])
    assert ev.calls() - after_first == 12


async def test_four_evaluator_types_over_http(ev):
    v = await ev.config_version()
    await ev.dataset(
        "ds",
        [
            {
                "case_key": "a",
                "input": "订单 A12 退货",
                "expected": "ECHO: 订单 A12 退货 ",
                "rubric": "要回显",
            },
            {"case_key": "b", "input": "你好", "expected": "不对的期望"},
        ],
    )
    ev.fake.add_rule(
        match=lambda r: "评审专家" in r.system and "订单" in r.system, reply='{"score": 5, "reason": "好"}'
    )
    ev.fake.add_rule(
        match=lambda r: "评审专家" in r.system, reply='```json\n{"score": 2, "reason": "差"}\n```'
    )
    evaluators = [
        {"key": "exact", "type": "exact_match", "normalize": ["strip", "lower"]},
        {"key": "kw", "type": "contains", "values": ["退货", "A12"], "mode": "all"},
        {"key": "neg", "type": "contains", "values": ["抱歉"], "negate": True},
        {"key": "re", "type": "regex", "pattern": "订单\\s*a\\d+", "flags": "i"},
        {"key": "judge", "type": "llm_judge", "provider": "fake", "model": "fake-model",
         "prompt": "asset://prompt/judge-default@latest", "scale": [1, 5], "pass_threshold": 4},
    ]  # fmt: skip
    created = await _post_eval(ev, [v], evaluators)
    run = await _wait(ev, created["runs"][0]["id"])
    assert run["status"] == "completed", run
    by_case = {
        i["case_key"]: {s["evaluator_key"]: s for s in i["scores"]} for i in await _all_items(ev, run["id"])
    }
    a, b = by_case["a"], by_case["b"]
    assert all(a[k]["status"] == "done" and a[k]["passed"] for k in ("exact", "kw", "neg", "re", "judge"))
    assert a["judge"]["raw_score"] == 5 and a["judge"]["score"] == 1.0 and a["judge"]["reason"] == "好"
    assert b["exact"]["passed"] is False and b["kw"]["passed"] is False
    assert b["neg"]["passed"] is True and b["re"]["passed"] is False
    assert b["judge"]["raw_score"] == 2 and b["judge"]["score"] == 0.25 and b["judge"]["passed"] is False
    # 评审请求和回复的摘要写进 detail
    detail = (await ev.client.get(f"{API}/evals/{run['id']}/items")).json()["items"]
    j = next(s for i in detail if i["case_key"] == "a" for s in i["scores"] if s["evaluator_key"] == "judge")
    sample = j["detail"]["samples"][0]
    assert "订单" in sample["request_preview"]["input"] and "score" in sample["reply_preview"]
    assert run["stats"]["evaluators"]["judge"]["mean"] == (1.0 + 0.25) / 2
    assert run["stats"]["pass_rate"] == 0.5


async def test_llm_judge_parse_failure_retries_once_then_errors(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(2))
    calls = {"n": 0}

    def judge_reply(req):
        calls["n"] += 1
        if "问题0" in req.system:
            return "我觉得不错" if calls["n"] <= 2 else "仍然不是 JSON"
        return '{"score": 5, "reason": "ok"}'

    ev.fake.add_rule(match=lambda r: "评审专家" in r.system, reply=judge_reply)
    judge = {"key": "j", "type": "llm_judge", "provider": "fake", "model": "fake-model"}
    created = await _post_eval(ev, [v], [{"key": "e", "type": "exact_match"}, judge])
    run = await _wait(ev, created["runs"][0]["id"])
    items = {
        i["case_key"]: {s["evaluator_key"]: s for s in i["scores"]} for i in await _all_items(ev, run["id"])
    }
    assert items["c000"]["j"]["status"] == "error" and items["c000"]["j"]["score"] is None
    assert items["c000"]["j"]["detail"]["samples"][0]["attempts"] == 2  # 首次 + 重试 1 次
    assert items["c001"]["j"]["status"] == "done"
    assert run["status"] == "completed" and run["stats"]["evaluators"]["j"]["errors"] == 1
    assert run["stats"]["pass_rate"] == 1.0  # 评审出错不参与判定


async def test_builtin_judge_prompt_asset_exists(ev):
    """应用启动（非测试注入 ctx）时自动创建；这里直接调用启动逻辑，重复调用幂等。"""
    from evalp.services.judge import JudgeService

    await ev.ctx.svc(JudgeService).ensure_default_prompt()
    await ev.ctx.svc(JudgeService).ensure_default_prompt()
    r = await ev.client.get(f"{API}/assets?kind=prompt")
    assets = [a for a in r.json()["items"] if a["name"] == "judge-default"]
    assert len(assets) == 1 and assets[0]["latest_rev"] == 1
    _, text = await ev.ctx.svc(JudgeService).prompt_text("asset://prompt/judge-default@latest")
    for var in ("input", "output", "expected", "rubric"):
        assert "{{ " + var + " }}" in text
    assert '"score"' in text and '"reason"' in text


async def test_stream_for_finished_run_is_status_then_idle(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(2))
    run = await _wait(ev, (await _post_eval(ev, [v], [{"type": "exact_match"}]))["runs"][0]["id"])
    r = await ev.client.get(f"{API}/evals/{run['id']}/stream")
    assert r.status_code == 200
    events = [ln[len("event: ") :] for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert events == ["eval.status", "idle"]
    data = next(ln for ln in r.text.splitlines() if ln.startswith("data: "))
    payload = json.loads(data[len("data: ") :])
    assert (
        payload["run_id"] == run["id"] and payload["status"] == "completed" and payload["stats"]["items"] == 2
    )


async def test_stream_live_run_emits_item_done_and_final_status(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(6))
    ev.fake.latency = 0.05
    created = await _post_eval(ev, [v], [{"type": "exact_match"}], concurrency=2)
    rid = created["runs"][0]["id"]
    r = await ev.client.get(f"{API}/evals/{rid}/stream")
    frames: list[tuple[str, dict]] = []
    event = None
    for ln in r.text.splitlines():
        if ln.startswith("event: "):
            event = ln[len("event: ") :]
        elif ln.startswith("data: "):
            frames.append((event or "", json.loads(ln[len("data: ") :])))
    names = [n for n, _ in frames]
    assert names[-1] == "idle" and names.count("eval.item_done") >= 1
    done = [d for n, d in frames if n == "eval.item_done"]
    assert all(
        d["run_id"] == rid and d["item"]["id"].startswith("evi_") and d["item"]["case_key"] for d in done
    )
    last_status = [d for n, d in frames if n == "eval.status"][-1]
    assert last_status["status"] == "completed" and last_status["stats"]["done"] == 6
    assert all("progress" in d and d["run_id"] == rid for n, d in frames if n == "eval.progress")


async def test_cancel_returns_run_and_marks_items(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(20))
    ev.fake.latency = 0.2
    created = await _post_eval(ev, [v], [{"type": "exact_match"}], concurrency=2)
    rid = created["runs"][0]["id"]
    r = await ev.client.post(f"{API}/evals/{rid}/cancel")
    assert r.status_code == 200 and r.json()["id"] == rid
    run = await _wait(ev, rid)
    assert run["status"] == "cancelled"
    statuses = {i["status"] for i in await _all_items(ev, rid)}
    assert "cancelled" in statuses and statuses <= {"cancelled", "done", "failed"}


async def test_invalid_evaluator_config_is_validation_error(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(1))
    for bad in (
        [{"type": "exact_match", "normalize": ["nope"]}],
        [{"type": "regex", "pattern": "("}],
        [{"type": "contains", "mode": "some"}],
        [{"key": "x", "type": "exact_match"}, {"key": "x", "type": "contains"}],
    ):
        r = await ev.client.post(
            f"{API}/evals", json={"version_ids": [v["id"]], "dataset": DS, "evaluators": bad}
        )
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR", (bad, r.text)
