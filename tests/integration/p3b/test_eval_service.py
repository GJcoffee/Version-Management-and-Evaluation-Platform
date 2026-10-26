"""评测服务：创建 / 预估 / 校验 / 执行单元（多轮、工具 mock）/ 生命周期（暂停、继续、取消、重试）。"""

from __future__ import annotations

import asyncio

import pytest
from evalp.errors import InvalidState, NotFound, ValidationFailed
from evalp.schemas.eval import Budget, EvalCreate
from evalp.store.models import EvalRun
from sqlalchemy import select

from tests.fixtures.fake_openai import FakeError

from .conftest import API, make_cases


async def wait_until(pred, timeout=10.0, interval=0.02):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        v = await pred()
        if v:
            return v
        if loop.time() > end:
            raise AssertionError("条件在限定时间内没有满足")
        await asyncio.sleep(interval)


async def test_one_run_per_version_in_one_batch(ev):
    v1, v2 = await ev.config_version(), await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    created = await ev.create([v1, v2], "asset://dataset/ds@latest", repeats=2, name="对比批次")
    assert [r.version_id for r in created.runs] == [v1["id"], v2["id"]]
    assert created.batch_id.startswith("evb_") and all(r.id.startswith("evr_") for r in created.runs)
    assert created.estimate["items"] == 16
    for r in created.runs:
        d = await ev.evals.wait_finished(r.id, timeout=60, interval=0.05)
        assert d.status == "completed" and d.batch_id == created.batch_id and d.batch_name == "对比批次"
        assert d.progress["total"] == 8 and d.progress["done"] == 8 and d.stats["pass_rate"] == 1.0
        assert {b["id"] for b in d.batch_runs} == {x.id for x in created.runs}
        assert d.agent_name and d.version_seq == 1 and d.dataset.name == "ds" and d.dataset.rev == 1
        assert d.started_at and d.finished_at and d.created_at <= d.started_at <= d.finished_at


async def test_dataset_by_revision_id_and_case_filter(ev):
    v = await ev.config_version()
    out = await ev.dataset("ds", make_cases(6))
    rev_id = out["revision"]["id"]
    d, _ = await ev.run(v, rev_id, case_filter={"tags": ["even"]})
    assert d.stats["cases"] == 3 and d.case_filter["tags"] == ["even"]
    d, _ = await ev.run(v, "asset://dataset/ds@1", case_filter={"case_keys": ["c001", "c002"]})
    assert d.stats["cases"] == 2
    with pytest.raises(ValidationFailed, match="没有符合"):
        await ev.create(v, rev_id, case_filter={"tags": ["nope"]})
    with pytest.raises(NotFound):
        await ev.create(v, "arv_01NOTEXISTNOTEXISTNOTEXIST")
    with pytest.raises(NotFound):
        await ev.create(v, "asset://dataset/missing@latest")


async def test_version_refs_and_duplicates(ev):
    v = await ev.config_version(name="bot")
    await ev.dataset("ds", make_cases(1))
    body = EvalCreate(
        version_ids=["bot:v1"], dataset="asset://dataset/ds@latest", evaluators=[{"type": "exact_match"}]
    )
    created = await ev.evals.create(body)
    assert created.runs[0].version_id == v["id"]
    dup = body.model_copy(update={"version_ids": ["bot:v1", v["id"]]})
    with pytest.raises(ValidationFailed, match="重复"):
        await ev.evals.create(dup)
    with pytest.raises(NotFound):
        await ev.evals.create(body.model_copy(update={"version_ids": ["ghost:v1"]}))


async def test_dry_run_returns_estimate_and_creates_nothing(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(5))
    out = await ev.create(v, "asset://dataset/ds@latest", repeats=3, dry_run=True, budget={"max_cost": 1e-9})
    assert out.dry_run and out.runs == [] and out.batch_id is None
    e = out.estimate
    assert e["items"] == 15 and e["cached_items"] == 0 and e["basis"] == "heuristic"
    assert e["tokens"] > 0 and e["cost"] > 0 and e["cost_known"] is True
    assert e["gross_cost"] == e["cost"] and e["exceeds_budget"] is True
    assert e["per_version"][0]["version_id"] == v["id"] and fake_calls(ev) == 0
    async with ev.ctx.db.read() as s:
        assert (await s.execute(select(EvalRun))).first() is None
    r = await ev.client.post(
        f"{API}/evals?dry_run=true",
        json={
            "version_ids": [v["id"]],
            "dataset": "asset://dataset/ds@latest",
            "evaluators": [{"type": "exact_match"}],
        },
    )
    assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["estimate"]["items"] == 15


def fake_calls(ev):
    return len(ev.fake.chat_requests)


async def test_estimate_uses_history_and_nets_out_cache(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    first, _ = await ev.run(v, "asset://dataset/ds@latest", repeats=2)
    per_item = first.stats["cost"] / 8
    out = await ev.create(v, "asset://dataset/ds@latest", repeats=3, dry_run=True)
    e = out.estimate
    assert e["basis"] == "history" and e["items"] == 12 and e["cached_items"] == 8
    assert e["cost"] == pytest.approx(per_item * 4, rel=1e-3)  # 只有未命中缓存的 4 条要花钱
    assert e["gross_cost"] == pytest.approx(per_item * 12, rel=1e-3)


@pytest.mark.parametrize(
    ("evaluators", "path"),
    [
        ([], "evaluators"),
        ([{"type": "regex", "pattern": "("}], "evaluators[0].pattern"),
        ([{"type": "llm_judge", "provider": "fake", "model": "nope"}], "evaluators[0]"),
        ([{"type": "llm_judge", "provider": "ghost", "model": "m"}], "evaluators[0]"),
        ([{"type": "llm_judge", "provider": "fake", "model": "fake-model", "prompt": "asset://prompt/nope@latest"}],
         "evaluators[0].prompt"),
    ],
)  # fmt: skip
async def test_bad_evaluator_config_is_validation_error(ev, evaluators, path):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(1))
    r = await ev.client.post(
        f"{API}/evals",
        json={"version_ids": [v["id"]], "dataset": "asset://dataset/ds@latest", "evaluators": evaluators},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert any(e["path"].startswith(path) for e in err["details"]["errors"]), err


async def test_judge_prompt_with_unknown_variable_is_rejected(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(1))
    await ev.api.asset("prompt", "bad-judge", "评分：{{ nonsense }} {{ output }}")
    r = await ev.client.post(
        f"{API}/evals",
        json={
            "version_ids": [v["id"]],
            "dataset": "asset://dataset/ds@latest",
            "evaluators": [
                {
                    "type": "llm_judge",
                    "provider": "fake",
                    "model": "fake-model",
                    "prompt": "asset://prompt/bad-judge@latest",
                }
            ],
        },
    )
    assert r.status_code == 400 and "nonsense" in r.text


async def test_multi_turn_case_runs_in_one_session_with_context(ev):
    v = await ev.config_version()
    await ev.dataset(
        "mt", [{"case_key": "m1", "input": ["我叫小明", "我叫什么"], "expected": "echo: 我叫什么"}]
    )
    d, _ = await ev.run(v, "asset://dataset/mt@latest", repeats=1)
    assert d.status == "completed" and d.stats["pass_rate"] == 1.0
    (item,) = await ev.items(d.id)
    detail = await ev.evals.get_item(d.id, item.id)
    assert [t["input"] for t in detail.turns] == ["我叫小明", "我叫什么"]
    assert detail.usage["turns"] == 2 and detail.session_id
    second = ev.fake.chat_requests[-1]
    roles = [m["role"] for m in second.messages]
    assert roles == ["system", "user", "assistant", "user"]  # 第二轮带着第一轮的历史
    sess = await ev.sessions.get(detail.session_id, include_turns=True)
    assert sess.session.mode == "eval" and len(sess.turns) == 2


async def test_case_variables_and_tool_mocks(ev):
    await ev.api.asset(
        "tool",
        "q",
        "name: query_order\ndescription: 查订单\n"
        "parameters: {type: object, properties: {order_id: {type: string}}}\n"
        "impl: {type: mock, responses: [{default: {status: 真实结果}}]}\n",
    )
    v = await ev.config_version(
        {
            "tools": ["asset://tool/q@latest"],
            "prompts": {"system": "你是{{ company }}客服", "variables": {"company": ""}},
        }
    )
    ev.fake.add_rule(match={"user": "订单", "has_tool": False}, reply=[("query_order", {"order_id": "A1"})])
    ev.fake.add_rule(match={"has_tool": True}, reply=lambda req: f"工具说：{req.last_tool_content}")
    await ev.dataset(
        "tm",
        [
            {
                "case_key": "t1",
                "input": "查订单",
                "expected": "mock",
                "metadata": {
                    "variables": {"company": "星辰"},
                    "tool_mocks": {"query_order": [{"output": {"status": "mock 结果"}}]},
                },
            }
        ],
    )
    d, _ = await ev.run(v, "asset://dataset/tm@latest", [{"type": "contains"}])
    assert d.status == "completed" and d.stats["pass_rate"] == 1.0
    assert "星辰" in ev.fake.chat_requests[0].system
    (item,) = await ev.items(d.id)
    detail = await ev.evals.get_item(d.id, item.id)
    assert "mock 结果" in detail.output and detail.usage["tool_calls"] == 1


async def test_per_run_concurrency_limit(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(12))
    ev.fake.latency = 0.05
    await ev.sched.set_workers(10)
    d, _ = await ev.run(v, "asset://dataset/ds@latest", concurrency=3)
    assert d.concurrency == 3 and d.progress["done"] == 12
    assert ev.fake.max_inflight == 3


async def test_pause_resume_and_cancel(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(20))
    ev.fake.latency = 0.05
    created = await ev.create(v, "asset://dataset/ds@latest", concurrency=2)
    rid = created.runs[0].id

    async def some_done():
        return (await ev.evals.progress(rid))["done"] >= 2

    await wait_until(some_done)
    d = await ev.evals.pause(rid)
    assert d.status == "paused" and d.pause_reason == "manual"
    with pytest.raises(InvalidState):
        await ev.evals.pause(rid)
    # 在跑的（含评分中的）做完，之后不再有新的 Item 开始；满负载下评分可能较慢，不能用固定时长等待
    async def drained():
        p = await ev.evals.progress(rid)
        return p["running"] == 0 and p["scoring"] == 0

    await wait_until(drained)
    p1 = await ev.evals.progress(rid)
    await asyncio.sleep(0.4)
    p2 = await ev.evals.progress(rid)
    assert p1 == p2 and p1["pending"] > 0 and p1["running"] == 0 and p1["pause_reason"] == "manual"
    d = await ev.evals.resume(rid)
    assert d.status == "running" and d.pause_reason is None
    await wait_until(lambda: some_done())
    d = await ev.evals.cancel(rid)
    assert d.status == "cancelled" and d.finished_at and d.stats["cancelled"] > 0
    await ev.sched.wait_idle(timeout=10)
    items = await ev.items(rid)
    assert {i.status for i in items} <= {"done", "cancelled"} and any(i.status == "cancelled" for i in items)
    assert d.progress["cancelled"] == sum(1 for i in items if i.status == "cancelled")
    with pytest.raises(InvalidState):
        await ev.evals.cancel(rid)
    with pytest.raises(InvalidState):
        await ev.evals.resume(rid)
    assert await ev.sched.pending_count(rid) == 0


async def test_failed_items_complete_with_errors_and_retry_failed(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(4))
    ev.fake.add_rule(match="问题2", reply=FakeError(400, message="坏请求"))
    d, _ = await ev.run(v, "asset://dataset/ds@latest")
    assert d.status == "completed_with_errors" and d.stats["failed"] == 1
    assert d.stats["pass_rate"] == pytest.approx(3 / 4)  # 执行失败算未通过
    failed = await ev.items(d.id, status="failed")
    assert len(failed) == 1 and failed[0].error["retriable"] is False and failed[0].score == 0.0
    assert failed[0].attempts == 1  # 不可重试的错误不会重试
    ev.fake.reset()
    out = await ev.evals.retry_failed(d.id)
    assert out == {"retried": 1}
    d2 = await ev.evals.wait_finished(d.id, timeout=30, interval=0.05)
    assert d2.status == "completed" and d2.stats["failed"] == 0 and d2.stats["pass_rate"] == 1.0
    assert (await ev.evals.retry_failed(d.id)) == {"retried": 0}


async def test_unknown_run_is_404(ev):
    r = await ev.client.get(f"{API}/evals/evr_01NOTEXISTNOTEXISTNOTEXI")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert (await ev.client.post(f"{API}/evals/evr_x/pause")).status_code == 404


async def test_hidden_judge_agent_is_not_listed(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(1))
    ev.fake.add_rule(match=lambda r: "评审专家" in r.system, reply='{"score": 5, "reason": "好"}')
    judge = {"type": "llm_judge", "provider": "fake", "model": "fake-model", "gate": True}
    d, _ = await ev.run(v, "asset://dataset/ds@latest", [judge])
    assert d.status == "completed"
    names = [a["name"] for a in (await ev.client.get(f"{API}/agents")).json()["items"]]
    assert "evalp-judge" not in names
    hidden = [a["name"] for a in (await ev.client.get(f"{API}/agents?include_hidden=true")).json()["items"]]
    assert "evalp-judge" in hidden
    r = await ev.client.post(f"{API}/agents", json={"name": "evalp-judge", "kind": "config"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_filters_and_pagination_over_http(ev):
    v1, v2 = await ev.config_version(name="alpha"), await ev.config_version(name="beta")
    await ev.dataset("ds", make_cases(6))
    c1 = await ev.create([v1, v2], "asset://dataset/ds@latest", name="b1")
    for r in c1.runs:
        await ev.evals.wait_finished(r.id, timeout=60, interval=0.05)
    c2 = await ev.create(v1, "asset://dataset/ds@latest", name="b2", cache_policy="refresh")
    await ev.evals.wait_finished(c2.runs[0].id, timeout=60, interval=0.05)

    page = (await ev.client.get(f"{API}/evals?limit=2")).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert page["items"][0]["id"] == c2.runs[0].id  # 新的在前
    rest = (await ev.client.get(f"{API}/evals?limit=2&cursor={page['next_cursor']}")).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    by_agent = (await ev.client.get(f"{API}/evals?agent=alpha")).json()["items"]
    assert {x["id"] for x in by_agent} == {c1.runs[0].id, c2.runs[0].id}
    by_batch = (await ev.client.get(f"{API}/evals?batch={c1.batch_id}")).json()["items"]
    assert {x["id"] for x in by_batch} == {r.id for r in c1.runs}
    assert (await ev.client.get(f"{API}/evals?status=cancelled")).json()["items"] == []
    both = (await ev.client.get(f"{API}/evals?status=completed,cancelled")).json()["items"]
    assert len(both) == 3
    # 条目分页与过滤
    rid = c1.runs[0].id
    items = (await ev.client.get(f"{API}/evals/{rid}/items?limit=4")).json()
    assert len(items["items"]) == 4 and items["next_cursor"]
    assert [i["case"]["case_key"] for i in items["items"]] == ["c000", "c001", "c002", "c003"]
    nxt = (await ev.client.get(f"{API}/evals/{rid}/items?limit=4&cursor={items['next_cursor']}")).json()
    assert [i["case"]["case_key"] for i in nxt["items"]] == ["c004", "c005"] and nxt["next_cursor"] is None
    odd = (await ev.client.get(f"{API}/evals/{rid}/items?tag=odd")).json()["items"]
    assert [i["case"]["case_key"] for i in odd] == ["c001", "c003", "c005"]
    q = (await ev.client.get(f"{API}/evals/{rid}/items?q=c00_")).json()["items"]
    assert q == []  # 下划线是字面量，不是通配符
    q = (await ev.client.get(f"{API}/evals/{rid}/items?q=问题3")).json()["items"]
    assert [i["case"]["case_key"] for i in q] == ["c003"]
    assert len((await ev.client.get(f"{API}/evals/{rid}/items?class=passed")).json()["items"]) == 6
    assert (await ev.client.get(f"{API}/evals/{rid}/items?class=failed")).json()["items"] == []
    assert (await ev.client.get(f"{API}/evals/{rid}/items?class=bogus")).status_code == 400
    one = (await ev.client.get(f"{API}/evals/{rid}/items/{items['items'][0]['id']}")).json()
    assert one["output"] == "echo: 问题0" and one["case_detail"]["expected"] == "echo: 问题0"
    assert one["scores"][0]["evaluator_key"] == "exact_match" and one["scores"][0]["passed"] is True
    assert (
        await ev.client.get(f"{API}/evals/{c2.runs[0].id}/items/{items['items'][0]['id']}")
    ).status_code == 404


async def test_budget_in_body_and_validation(ev):
    v = await ev.config_version()
    await ev.dataset("ds", make_cases(1))
    r = await ev.client.post(
        f"{API}/evals",
        json={"version_ids": [v["id"]], "dataset": "asset://dataset/ds@latest",
              "evaluators": [{"type": "exact_match"}], "budget": {"max_cost": -1}},
    )  # fmt: skip
    assert r.status_code == 400
    assert Budget(max_cost=1.5).model_dump(exclude_none=True) == {"max_cost": 1.5}
