"""输出契约中间件：轮次结束后的校验、状态、快照与输入步骤组合。"""

import hashlib
import json

import httpx
import pytest
import respx
from evalp.errors import EvalpError
from evalp.runtime.adapters.base import AgentAdapter
from evalp.runtime.events import ev
from evalp.runtime.messages import Message, TurnOutput
from evalp.schemas.middleware import MiddlewareSelection
from evalp.store.models import Turn
from evalp.utils.time import now_iso

URL = "https://artifacts.example/story.json?signature=retained"
OUTPUT_URL = "https://artifacts.example/output.json"


def envelope(output_type="confirm", *, artifact=None, review=None, questions=None) -> dict:
    value = {
        "summary": "阶段说明",
        "outputType": output_type,
        "agentInfo": {"agentId": "creative-director"},
        "confirmInfo": {"questionInfoList": questions if questions is not None else [
            {"question": "继续？", "optionList": [{"optionCode": "approve"}]}
        ]},
        "artifactInfo": artifact,
        "agentInvocationChain": [],
    }
    if review is not None:
        value["examineInfo"] = review
    return value


def stage_config(stage: int, **rule) -> dict:
    return {
        "agent_id": "creative-director",
        "stages": {str(stage): {"output_type": "confirm", "approval_code": "approve", **rule}},
    }


def output_selection(stage: int = 1) -> MiddlewareSelection:
    return MiddlewareSelection(id="output_contract", version="1.0.0", config=stage_config(stage))


class FinalAdapter(AgentAdapter):
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.seen = None

    async def run_turn(self, ctx, history, user_input):
        self.calls += 1
        self.seen = user_input.text()
        yield ev("turn.start", input=user_input)
        yield ev("message.assistant", message=Message(role="assistant", content=self.text))


def install_adapter(monkeypatch, text: str) -> FinalAdapter:
    adapter = FinalAdapter(text)
    monkeypatch.setattr("evalp.services.session.build_adapter", lambda *_: adapter)
    return adapter


async def setup_source(world, text=URL):
    version = await world.config_version()
    session = await world.session(version)
    source_id = "trn_source"
    async with world.ctx.db.write() as db:
        db.add(Turn(id=source_id, session_id=session.id, idx=0, status="ok",
                    input=Message.user("生成").model_dump(),
                    output=TurnOutput(final=Message(role="assistant", content=text)).model_dump(),
                    started_at=now_iso(), finished_at=now_iso()))
    return session, source_id


async def test_success_updates_step_and_stores_envelope_blob(world, monkeypatch):
    session = await world.session(await world.config_version())
    final = json.dumps(envelope(), ensure_ascii=False)
    adapter = install_adapter(monkeypatch, final)

    turn = await world.sessions.run_turn(session.id, "开始", middlewares=[output_selection()])

    assert turn.status == "ok", turn.error
    assert adapter.calls == 1
    step = turn.middleware_snapshot["steps"][0]
    assert step["phase"] == "output" and step["id"] == "output_contract"
    assert step["status"] == "ok" and step["stage"] == 1
    assert step["outputType"] == "confirm" and step["artifactType"] is None
    assert step["sha256"] and step["size"] == len(final.encode("utf-8"))
    blob = step["result_blob_hash"]
    assert await world.ctx.blobs.refcount(blob) == 1
    assert json.loads(await world.ctx.blobs.get_bytes(blob)) == envelope()

    types = await world.types(session.id, turn_id=turn.id)
    assert types.count("turn.end") == 1 and types[-1] == "turn.end"
    assert "error" not in types
    start = (await world.events(session.id, turn_id=turn.id))[0]
    assert start.type == "turn.start"
    assert start.payload["middleware_snapshot"]["steps"][0]["status"] == "pending"


async def test_failure_marks_error_and_keeps_streamed_text(world, monkeypatch):
    session = await world.session(await world.config_version())
    final = json.dumps({"outputType": "confirm"}, ensure_ascii=False)
    install_adapter(monkeypatch, final)

    turn = await world.sessions.run_turn(session.id, "开始", middlewares=[output_selection()])

    assert turn.status == "error"
    assert turn.error is not None and turn.error.code == "OUTPUT_CONTRACT_FAILED"
    assert turn.error.retriable is False
    step = turn.middleware_snapshot["steps"][0]
    assert step["status"] == "error" and step["error"]["code"] == "OUTPUT_CONTRACT_FAILED"
    assert "缺少" in step["error"]["message"]
    assert not step.get("result_blob_hash")
    assert turn.output is not None and turn.output.final is None
    assert turn.output.partial_text == final

    types = await world.types(session.id, turn_id=turn.id)
    assert types.count("turn.end") == 1
    assert types[-2:] == ["error", "turn.end"]
    assert not world.sessions.is_running(session.id)


async def test_json_url_is_fetched_with_allowlist(world, monkeypatch):
    session = await world.session(await world.config_version())
    world.ctx.config.artifacts.allowed_hosts = "artifacts.example"
    install_adapter(monkeypatch, OUTPUT_URL)
    raw = json.dumps(envelope(), ensure_ascii=False, separators=(",", ":")).encode()

    with respx.mock() as router:
        route = router.get(OUTPUT_URL).mock(
            return_value=httpx.Response(200, content=raw, headers={"Content-Type": "application/json"})
        )
        turn = await world.sessions.run_turn(session.id, "开始", middlewares=[output_selection()])

    assert route.called and turn.status == "ok", turn.error
    step = turn.middleware_snapshot["steps"][0]
    assert step["status"] == "ok" and step["outputType"] == "confirm"
    assert step["size"] == len(raw)
    assert step["sha256"] == hashlib.sha256(raw).hexdigest()


async def test_input_and_output_steps_share_snapshot(world, monkeypatch):
    session, source = await setup_source(world)
    world.ctx.config.artifacts.allowed_hosts = "artifacts.example"
    install_adapter(monkeypatch, json.dumps(envelope()))

    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, json={"scenes": [1]}))
        turn = await world.sessions.run_turn(session.id, "继续", middlewares=[
            MiddlewareSelection(source_turn_id=source),
            output_selection(2),
        ])

    assert turn.status == "ok", turn.error
    steps = turn.middleware_snapshot["steps"]
    assert [step["phase"] for step in steps] == ["input", "output"]
    assert [step["status"] for step in steps] == ["ok", "ok"]
    assert steps[1]["stage"] == 2


async def test_http_contract_selection_and_catalog_phase(world, monkeypatch):
    session = await world.session(await world.config_version())
    install_adapter(monkeypatch, json.dumps(envelope()))

    catalog = await world.client.get("/api/v1/middlewares")
    items = {item["id"]: item for item in catalog.json()["middlewares"]}
    assert items["output_contract"]["phase"] == "output"
    assert items["json_artifact"]["phase"] == "input"

    response = await world.client.post(f"/api/v1/sessions/{session.id}/turns", json={
        "input": "开始", "wait": True,
        "middlewares": [{
            "id": "output_contract", "version": "1.0.0", "config": stage_config(1),
        }],
    })
    body = response.json()
    assert response.status_code == 200 and body["turn"]["status"] == "ok", response.text
    step = body["turn"]["middleware_snapshot"]["steps"][0]
    assert step["phase"] == "output" and step["status"] == "ok"
    assert step["outputType"] == "confirm" and step["stage"] == 1


async def test_duplicate_phase_and_missing_stage_are_rejected(world, monkeypatch):
    session = await world.session(await world.config_version())
    with pytest.raises(EvalpError) as exc:
        await world.sessions.run_turn(session.id, "开始", middlewares=[
            MiddlewareSelection(), MiddlewareSelection(),
        ])
    assert exc.value.code == "VALIDATION_ERROR" and not world.sessions.is_running(session.id)

    install_adapter(monkeypatch, json.dumps(envelope()))
    turn = await world.sessions.run_turn(session.id, "开始", middlewares=[
        output_selection(2),
    ])
    assert turn.status == "error" and turn.error.code == "OUTPUT_CONTRACT_FAILED"
    step = turn.middleware_snapshot["steps"][0]
    assert step["status"] == "error" and "未配置输出契约" in step["error"]["message"]
    assert (await world.types(session.id, turn_id=turn.id))[-1] == "turn.end"
