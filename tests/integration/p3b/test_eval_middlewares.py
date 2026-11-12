"""原生评测逐轮接入：真实 JSON 续接、契约失败停止、来源隔离和缓存。"""

import json
from collections import defaultdict

import httpx
import pytest
import respx
from evalp.errors import AgentError, DatasetParseError
from evalp.runtime.adapters.base import AgentAdapter
from evalp.runtime.events import ev as event
from evalp.runtime.messages import Message
from evalp.services.dataset_parse import parse_jsonl

HOST = "artifacts.example"
URL = f"https://{HOST}/stage"
DATASET = "asset://dataset/pipeline@latest"


def envelope(stage):
    terminal = stage == 5
    data = {
        "summary": f"阶段{stage}",
        "outputType": "finish" if terminal else ("examine" if stage == 4 else "confirm_artifact"),
        "agentInfo": {"agentId": "creative-director"},
        "confirmInfo": {"questionInfoList": [] if terminal else [
            {"question": "确认继续", "optionList": [{"optionCode": f"approve.{stage}"}]},
        ]},
        "artifactInfo": None if terminal else {
            "artifactType": [1, 2, 4, 5][stage - 1], "artifactId": f"artifact-{stage}",
            "cardInfoList": [{"url": f"{URL}{stage}.json"}],
        },
        "agentInvocationChain": [],
    }
    if stage == 4:
        data["examineInfo"] = {"examineStatus": 2, "examineInfoList": [{"result": "pass"}]}
    return data


def plan():
    config = {
        "agent_id": "creative-director",
        "stages": {
            str(s): {
                "output_type": envelope(s)["outputType"],
                "artifact_type": None if s == 5 else [1, 2, 4, 5][s - 1],
                "approval_code": None if s == 5 else f"approve.{s}",
                "terminal": s == 5,
            } for s in range(1, 6)
        },
    }
    return {
        str(s): ([{"id": "json_artifact", "config": {}}] if s > 1 else []) +
        [{"id": "output_contract", "config": config}]
        for s in range(1, 6)
    }


class Pipeline(AgentAdapter):
    def __init__(self, fail_stage=None):
        self.seen = defaultdict(list)
        self.fail_stage = fail_stage
        self.transient = False

    async def run_turn(self, ctx, history, user_input):
        seen = self.seen[ctx.session_id]
        seen.append(user_input.text())
        stage = len(seen)
        if stage == 2 and self.transient:
            self.transient = False
            raise AgentError("临时失败", {"retriable": True}, code="TEMPORARY")
        value = {"outputType": "broken"} if stage == self.fail_stage else envelope(stage)
        yield event("turn.start", input=user_input)
        yield event("message.assistant", message=Message(
            role="assistant", content=json.dumps(value, ensure_ascii=False),
        ))


async def setup(ev, monkeypatch, fail_stage=None, case_plan=None):
    version = await ev.config_version()
    adapter = Pipeline(fail_stage)
    monkeypatch.setattr("evalp.services.session.build_adapter", lambda *_: adapter)
    ev.ctx.config.artifacts.allowed_hosts = HOST
    input_ = {"turns": [f"指令{s}" for s in range(1, 6)]}
    if case_plan is not None:
        input_["turn_middlewares"] = case_plan
    await ev.dataset("pipeline", [{"case_key": "five", "input": input_}])
    return version, adapter


def routes(router, broken=False):
    for stage in range(1, 5):
        # HTTP 返回的实际内容特意与链接 / 输出摘要区分，验证确实下载并组合。
        router.get(f"{URL}{stage}.json").mock(return_value=httpx.Response(
            200, content=b"not-json" if broken else json.dumps({"actual_json": stage}).encode(),
        ))


async def test_five_turn_pipeline_isolates_repeats_and_retains_snapshots(ev, monkeypatch):
    version, adapter = await setup(ev, monkeypatch)
    with respx.mock() as router:
        routes(router)
        run, _ = await ev.run(
            version, DATASET, [{"type": "contains", "values": ["finish"]}],
            turn_middlewares=plan(), repeats=2, concurrency=2,
        )
    assert run.status == "completed" and run.stats["done"] == 2
    assert run.turn_middlewares["2"][0]["id"] == "json_artifact"
    items = await ev.items(run.id)
    assert len({it.session_id for it in items}) == 2
    for item in items:
        detail = await ev.evals.get_item(run.id, item.id)
        assert len(detail.turns) == 5
        for stage, turn in enumerate(detail.turns, start=1):
            steps = turn["middleware_snapshot"]["steps"]
            assert all(s["status"] == "ok" and s["result_blob_hash"] for s in steps)
            if stage > 1:
                assert steps[0]["source_turn_id"] == detail.turns[stage - 2]["id"]
                actual = adapter.seen[item.session_id][stage - 1].split("JSON 内容：\n", 1)[1]
                assert json.loads(actual) == {"actual_json": stage - 1}
                assert f"指令{stage}" in adapter.seen[item.session_id][stage - 1]
                assert turn["effective_input"]
    assert sum(len(seen) for seen in adapter.seen.values()) == 10


async def test_contract_failure_stops_and_manual_retry_starts_new_session(ev, monkeypatch):
    version, adapter = await setup(ev, monkeypatch, fail_stage=3)
    with respx.mock() as router:
        routes(router)
        run, _ = await ev.run(version, DATASET, turn_middlewares=plan())
        item = (await ev.items(run.id))[0]
        assert run.status == "completed_with_errors" and item.status == "failed"
        assert item.attempts == 1 and item.error["code"] == "OUTPUT_CONTRACT_FAILED"
        assert "第 3 轮" in item.error["message"] and "停止" in item.error["message"]
        detail = await ev.evals.get_item(run.id, item.id)
        assert len(detail.turns) == 3 and detail.turns[-1]["status"] == "error"
        assert len(adapter.seen[item.session_id]) == 3
        assert not item.scores
        original_session = item.session_id
        adapter.fail_stage = None
        await ev.evals.retry_failed(run.id)
        completed = await ev.evals.wait_finished(run.id, timeout=10, interval=0.02)
    assert completed.status == "completed"
    retried = (await ev.items(run.id))[0]
    assert retried.session_id != original_session
    assert len(adapter.seen[retried.session_id]) == 5
    assert retried.usage["failed_attempts"][0]["session_id"] == original_session


async def test_bad_json_stops_before_second_agent_call(ev, monkeypatch):
    version, adapter = await setup(ev, monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        routes(router, broken=True)
        run, _ = await ev.run(version, DATASET, turn_middlewares=plan())
    item = (await ev.items(run.id))[0]
    assert item.status == "failed" and len(adapter.seen[item.session_id]) == 1
    detail = await ev.evals.get_item(run.id, item.id)
    assert len(detail.turns) == 2 and detail.turns[1]["effective_input"] is None
    assert detail.turns[1]["middleware_snapshot"]["steps"][0]["status"] == "error"


async def test_automatic_retry_uses_only_new_attempt_sources(ev, monkeypatch):
    version, adapter = await setup(ev, monkeypatch)
    adapter.transient = True
    with respx.mock() as router:
        routes(router)
        run, _ = await ev.run(version, DATASET, turn_middlewares=plan())
    item = (await ev.items(run.id))[0]
    assert item.status == "done" and item.attempts == 2
    failed = item.usage["failed_attempts"][0]["session_id"]
    assert failed != item.session_id and len(adapter.seen[failed]) == 2
    detail = await ev.evals.get_item(run.id, item.id)
    assert detail.turns[1]["middleware_snapshot"]["steps"][0]["source_turn_id"] == detail.turns[0]["id"]


async def test_cache_tracks_plan_and_case_override(ev, monkeypatch):
    version, adapter = await setup(ev, monkeypatch, case_plan={"2": []})
    with respx.mock(assert_all_called=False) as router:
        routes(router)
        run, _ = await ev.run(version, DATASET, turn_middlewares=plan())
        item = (await ev.items(run.id))[0]
        assert adapter.seen[item.session_id][1] == "指令2"
        reused, _ = await ev.run(version, DATASET, turn_middlewares=plan())
        assert (await ev.items(reused.id))[0].cached
        changed = plan()
        changed["3"] = []
        fresh, _ = await ev.run(version, DATASET, turn_middlewares=changed)
        assert not (await ev.items(fresh.id))[0].cached
        plain, _ = await ev.run(version, DATASET)
        assert not (await ev.items(plain.id))[0].cached
    assert run.status == "completed"


@pytest.mark.parametrize("bad", [
    {"1": [{"id": "json_artifact"}]},
    {"2": [{"id": "json_artifact", "source_turn_id": "old"}]},
    {"2": [{"id": "json_artifact", "config": {"selected_candidate_id": "old"}}]},
    {"2": [{"id": "unknown"}]},
    {"2": [{"id": "json_artifact"}, {"id": "json_artifact"}]},
    {"2": [{"id": "output_contract", "config": {"stages": {"1": {"output_type": "confirm"}}}}]},
    {"02": []},
    {"6": [{"id": "json_artifact"}]},
])
async def test_invalid_plan_rejected_before_scheduling_or_import(ev, monkeypatch, bad):
    version, adapter = await setup(ev, monkeypatch)
    response = await ev.client.post("/api/v1/evals", json={
        "version_ids": [version["id"]], "dataset": "asset://dataset/pipeline@latest",
        "evaluators": [{"type": "exact_match"}], "turn_middlewares": bad,
    })
    assert response.status_code == 400, response.text
    assert not adapter.seen
    with pytest.raises(DatasetParseError, match="数据集解析失败"):
        parse_jsonl(json.dumps({"input": {"turns": ["x"] * 5, "turn_middlewares": bad}}))
