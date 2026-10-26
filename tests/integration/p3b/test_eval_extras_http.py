"""评测契约之外的接口（rescore / human-scores / gate）的 HTTP 冒烟测试，前端的评测 UI 依赖它们。"""

from __future__ import annotations

import pytest

from .conftest import API, make_cases


async def test_rescore_only_recomputes_changed_evaluators(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    exact = {"key": "exact", "type": "exact_match"}
    kw = {"key": "kw", "type": "contains", "values": ["echo"], "gate": False}
    d, _ = await ev.run(v, "asset://dataset/ds@latest", [exact, kw])
    assert d.status == "completed"
    calls = ev.calls()

    # 没有变化：不重新评分
    r = await ev.client.post(f"{API}/evals/{d.id}/rescore", json={})
    assert r.status_code == 200 and r.json()["rescored_items"] == 0 and r.json()["evaluators_changed"] == []

    # 只改 kw：重新评分，evaluators_changed 只含 kw，且不再调用被测 agent
    kw2 = {**kw, "values": ["不存在的词"]}
    r = await ev.client.post(f"{API}/evals/{d.id}/rescore", json={"evaluators": [exact, kw2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rescored_items"] == 4 and body["evaluators_changed"] == ["kw"]
    d2 = await ev.evals.wait_finished(d.id, timeout=30, interval=0.05)
    assert d2.status == "completed" and d2.stats["evaluators"]["kw"]["pass_rate"] == 0.0
    assert d2.stats["evaluators"]["exact"]["pass_rate"] == 1.0
    assert ev.calls() == calls

    # 不存在
    r = await ev.client.post(f"{API}/evals/evr_01NOTEXISTNOTEXISTNOTEXI/rescore", json={})
    assert r.status_code == 404


async def test_human_scores_flow_and_gate(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(3))
    human = {"key": "human", "type": "human", "scale": [1, 5], "gate": True, "guideline": "看是否礼貌"}
    d, _ = await ev.run(v, "asset://dataset/ds@latest", [human])
    assert d.status == "completed"
    assert d.stats["evaluators"]["human"]["pending_human"] == 3

    pending = (await ev.client.get(f"{API}/evals/{d.id}/items", params={"class": "pending_human"})).json()
    assert len(pending["items"]) == 3
    assert pending["items"][0]["scores"][0]["status"] == "pending_human"

    # 门禁：尚无评分，通过率为空 + 要求 min_pass_rate -> 不通过
    g = (await ev.client.get(f"{API}/evals/{d.id}/gate", params={"min_pass_rate": 0.5})).json()
    assert g["passed"] is False and g["reasons"]

    entries = [
        {"item_id": it["id"], "evaluator_key": "human", "raw_score": 5 if i < 2 else 1, "reason": "ok"}
        for i, it in enumerate(pending["items"])
    ]
    r = await ev.client.post(f"{API}/evals/{d.id}/human-scores", json={"scores": entries})
    assert r.status_code == 200 and r.json() == {"updated": 3}
    run = (await ev.client.get(f"{API}/evals/{d.id}")).json()
    assert run["stats"]["evaluators"]["human"]["pending_human"] == 0
    assert run["stats"]["pass_rate"] == pytest.approx(2 / 3)
    g = (await ev.client.get(f"{API}/evals/{d.id}/gate", params={"min_pass_rate": 0.5})).json()
    assert g["passed"] is True and g["summary"]["pass_rate"] == pytest.approx(2 / 3)

    # 越界的分数：整个请求不生效
    bad = [{**entries[0], "raw_score": 9}]
    r = await ev.client.post(f"{API}/evals/{d.id}/human-scores", json={"scores": bad})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_gate_against_baseline_reports_regressions(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    base, _ = await ev.run(v, "asset://dataset/ds@latest")
    # 换成必然不通过的评估器，相对基线全部退化
    bad, _ = await ev.run(
        v,
        "asset://dataset/ds@latest",
        [{"key": "never", "type": "contains", "values": ["不可能出现"]}],
        cache_policy="refresh",
    )
    r = await ev.client.get(
        f"{API}/evals/{bad.id}/gate", params={"baseline": base.id, "max_regressions": 1, "min_pass_rate": 0.9}
    )
    assert r.status_code == 200
    g = r.json()
    assert g["passed"] is False and len(g["reasons"]) == 2
    assert g["summary"]["regressions"] == 4 and g["summary"]["baseline_run_id"] == base.id
    ok = (await ev.client.get(f"{API}/evals/{base.id}/gate", params={"baseline": base.id})).json()
    assert ok["passed"] is True and ok["reasons"] == []
