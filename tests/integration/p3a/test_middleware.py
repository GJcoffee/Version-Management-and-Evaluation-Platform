"""轮间准备与 Agent 执行的边界、历史、Blob 和失败终态。"""

import asyncio
import json

import httpx
import pytest
import respx
from evalp.errors import EvalpError
from evalp.runtime.adapters.base import AgentAdapter
from evalp.runtime.events import ev
from evalp.runtime.messages import Message, TurnOutput
from evalp.schemas.middleware import MiddlewareSelection
from evalp.services.compare import CompareService
from evalp.services.middleware import MiddlewareService
from evalp.store.gc import run_gc
from evalp.store.models import Event, Session, Turn
from evalp.utils.time import iso_ago, now_iso

URL = "https://artifacts.example/story.json?signature=retained"


async def setup_source(world, text=URL, spec=None, version=None):
    version = version or await world.config_version(spec_over=spec)
    session = await world.session(version)
    # 直接保存上一轮最终回复，避免源轮次依赖模型调用。
    source_id = "trn_source"
    async with world.ctx.db.write() as db:
        db.add(Turn(id=source_id, session_id=session.id, idx=0, status="ok",
                    input=Message.user("生成").model_dump(),
                    output=TurnOutput(final=Message(role="assistant", content=text)).model_dump(),
                    started_at=now_iso(), finished_at=now_iso()))
    return version, session, source_id


class RecordingAdapter(AgentAdapter):
    def __init__(self):
        self.calls = []

    async def run_turn(self, ctx, history, user_input):
        self.calls.append((history, user_input))
        async with ctx.app.db.read() as db:
            saved = await db.get(Turn, ctx.turn_id)
            assert saved.effective_input == user_input.model_dump(exclude_none=True)
            assert saved.middleware_snapshot["steps"][0]["status"] == "ok"
        yield ev("turn.start", input=user_input)
        yield ev("message.assistant", message=Message(role="assistant", content="已处理"))


def install_adapter(monkeypatch):
    adapter = RecordingAdapter()
    monkeypatch.setattr("evalp.services.session.build_adapter", lambda *_: adapter)
    return adapter


async def test_success_saved_before_adapter_and_effective_history(world, client, monkeypatch):
    _, session, source = await setup_source(world)
    world.ctx.config.artifacts.allowed_hosts = "artifacts.example"
    adapter = install_adapter(monkeypatch)
    catalog = await client.get("/api/v1/middlewares")
    assert catalog.json()["middlewares"][0]["version"] == "1.0.0"
    with respx.mock() as router:
        route = router.get(URL).mock(return_value=httpx.Response(200, json={"scenes": [1], "other": 2}))
        preview = await client.get(f"/api/v1/sessions/{session.id}/turns/{source}/artifact-candidates")
        assert preview.status_code == 200 and len(preview.json()["candidates"]) == 1
        assert not route.called
        response = await client.post(f"/api/v1/sessions/{session.id}/turns", json={
            "input": "不添加对白", "wait": True, "middlewares": [{"source_turn_id": source,
            "expected_previous_turn_id": source, "config": {"content_pointer": "/scenes"}}],
        })
    turn = response.json()["turn"]
    assert response.status_code == 200 and turn["status"] == "ok", response.text
    assert len(adapter.calls) == 1 and adapter.calls[0][1].text().endswith("[1]")
    assert turn["input"]["content"] == "不添加对白" and turn["effective_input"]
    events = await world.events(session.id, turn_id=turn["id"], resolve_blobs=True)
    assert [e.type for e in events].count("turn.start") == 1
    assert events[0].payload["input"]["content"] == "不添加对白"
    assert events[0].payload["middleware_snapshot"]["steps"][0]["status"] == "pending"
    assert events[-1].type == "turn.end" and events[-1].payload["usage"]["middleware_ms"] >= 0
    assert turn["usage"]["llm_calls"] == turn["usage"]["tool_calls"] == 0
    assert (await world.sessions.get_history(session.id))[2].text() == turn["effective_input"]["content"]
    blob = turn["middleware_snapshot"]["steps"][0]["result_blob_hash"]
    assert await world.ctx.blobs.refcount(blob) == 1
    content = await client.get(f"/api/v1/blobs/{blob}", params={"session_id": session.id})
    assert content.status_code == 200 and content.json()["selected"] == [1]
    other = await world.session(await world.config_version())
    assert (await client.get(f"/api/v1/blobs/{blob}", params={"session_id": other.id})).status_code == 404
    saved = (await client.get(f"/api/v1/turns/{turn['id']}")).json()
    assert saved["effective_input"] == turn["effective_input"]


@pytest.mark.parametrize("text,response,step_config,code", [
    ("没有链接", None, {}, "ARTIFACT_NOT_FOUND"),
    (URL + "\nhttps://artifacts.example/other.json", None, {}, "ARTIFACT_AMBIGUOUS"),
    (URL, httpx.Response(403), {}, "ARTIFACT_FETCH_FAILED"),
    (URL, httpx.Response(200, content=b"not-json"), {}, "ARTIFACT_INVALID_JSON"),
    (URL, httpx.Response(200, json={"a": 1}), {"content_pointer": "/missing"}, "ARTIFACT_FIELD_NOT_FOUND"),
])
async def test_failure_never_calls_adapter(world, monkeypatch, text, response, step_config, code):
    _, session, source = await setup_source(world, text)
    world.ctx.config.artifacts.allowed_hosts = "artifacts.example"
    adapter = install_adapter(monkeypatch)
    with respx.mock() as router:
        if response is not None:
            router.get(URL).mock(return_value=response)
        turn = await world.sessions.run_turn(session.id, "不添加对白", middlewares=[
            MiddlewareSelection(source_turn_id=source, config=step_config)])
    assert turn.status == "error" and turn.error.code == code
    assert not adapter.calls and turn.effective_input is None
    assert turn.middleware_snapshot["steps"][0]["status"] == "error"
    types = await world.types(session.id, turn_id=turn.id)
    assert types[0] == "turn.start" and types[-2:] == ["error", "turn.end"]
    assert not world.sessions.is_running(session.id)
    assert len(await world.sessions.get_history(session.id)) == 2


async def test_source_validation_and_stale_previous(world, client):
    _, session, source = await setup_source(world)
    for selection, code in [
        ({"source_turn_id": "not-exist"}, "ARTIFACT_SOURCE_INVALID"),
        ({"expected_previous_turn_id": "stale"}, "ARTIFACT_SOURCE_CHANGED"),
        ({"id": "unknown"}, "VALIDATION_ERROR"),
        ({"version": "latest"}, "VALIDATION_ERROR"),
    ]:
        response = await client.post(f"/api/v1/sessions/{session.id}/turns",
                                     json={"input": "第二轮", "middlewares": [selection]})
        assert response.json()["error"]["code"] == code
        assert not world.sessions.is_running(session.id)
    other = await world.session(await world.config_version())
    preview = await client.get(f"/api/v1/sessions/{other.id}/turns/{source}/artifact-candidates")
    assert preview.status_code == 400
    bad = await client.get(f"/api/v1/sessions/{session.id}/turns/{source}/artifact-candidates",
                           params={"url_pointer": "not-a-pointer"})
    assert bad.status_code == 400


@pytest.mark.parametrize("timeout", [False, True])
async def test_cancel_and_turn_deadline_include_preparation(world, monkeypatch, timeout):
    _, session, source = await setup_source(world, spec={"runtime": {"turn_timeout_s": 1}})
    adapter = install_adapter(monkeypatch)
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def slow_fetch(*_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr("evalp.services.middleware.fetch_artifact", slow_fetch)
    accepted = await world.sessions.run_turn(session.id, "不添加对白", wait=False,
                                            middlewares=[MiddlewareSelection(source_turn_id=source)])
    await asyncio.wait_for(entered.wait(), 2)
    if not timeout:
        await world.sessions.cancel_turn(accepted.id)
    turn = await world.sessions.wait_turn(accepted.id)
    assert closed.is_set() and not adapter.calls and turn.effective_input is None
    assert turn.status == ("timeout" if timeout else "cancelled")
    assert turn.middleware_snapshot["steps"][0]["status"] == turn.status
    assert (await world.types(session.id))[-1] == "turn.end"
    assert not world.sessions.is_running(session.id)


async def test_blob_storage_failure_stops_before_adapter(world, monkeypatch):
    _, session, source = await setup_source(world)
    adapter = install_adapter(monkeypatch)

    async def fetched(*_):
        return b'{"ok":true}'

    async def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("evalp.services.middleware.fetch_artifact", fetched)
    monkeypatch.setattr(world.ctx.blobs, "put_bytes", broken)
    turn = await world.sessions.run_turn(session.id, "不添加对白",
                                        middlewares=[MiddlewareSelection(source_turn_id=source)])
    assert turn.status == "error" and not adapter.calls and turn.effective_input is None
    assert not turn.middleware_snapshot["steps"][0].get("result_blob_hash")
    assert not world.sessions.is_running(session.id)


async def test_replay_rejects_middleware_in_selected_or_necessary_history(world):
    version, session, source = await setup_source(world)
    async with world.ctx.db.write() as db:
        row = await db.get(Turn, source)
        row.middleware_snapshot = {"schema_version": 1, "steps": [{"status": "ok"}]}
        db.add(Turn(id="trn_next", session_id=session.id, idx=1, status="ok",
                    input=Message.user("下一轮").model_dump(),
                    output=TurnOutput(final=Message(role="assistant", content="完成")).model_dump(),
                    started_at=now_iso()))
    compare = world.ctx.svc(CompareService)
    for mode, turns in [("anchored", [1]), ("free", [0])]:
        with pytest.raises(EvalpError) as exc:
            await compare.replay(session.id, version["id"], turns, mode)
        assert exc.value.code == "MIDDLEWARE_REPLAY_UNSUPPORTED"


async def test_gc_releases_independent_snapshot_and_event_refs(world):
    _, session, source = await setup_source(world)
    blob = await world.ctx.blobs.put_bytes(b'{"saved":"artifact"}')
    async with world.ctx.db.write() as db:
        row = await db.get(Turn, source)
        row.middleware_snapshot = {"schema_version": 1, "steps": [{"result_blob_hash": blob}]}
        sess = await db.get(Session, session.id)
        sess.mode, sess.updated_at = "eval", iso_ago(hours=96)
        db.add(Event(session_id=session.id, seq=1, turn_id=source, ts=now_iso(),
                     type="span.end", payload={"$blob": blob}))
        await world.ctx.blobs.incref(blob, 2, session=db)
    assert await world.ctx.blobs.refcount(blob) == 2
    result = await run_gc(world.ctx.db, world.ctx.blobs, eval_sessions_days=1)
    assert result.eval_sessions_deleted == 1
    assert await world.ctx.blobs.refcount(blob) == 0


async def test_effective_input_transaction_failure_never_calls_adapter(world, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, session, source = await setup_source(world)
    adapter = install_adapter(monkeypatch)
    commit = AsyncSession.commit

    async def fetched(*_):
        return b'{"scenes":[1]}'

    async def fail_prepared_commit(db):
        if any(isinstance(row, Turn) and row.effective_input for row in db.dirty):
            raise OSError("prepared input commit failed")
        await commit(db)
    monkeypatch.setattr("evalp.services.middleware.fetch_artifact", fetched)
    monkeypatch.setattr(AsyncSession, "commit", fail_prepared_commit)
    turn = await world.sessions.run_turn(session.id, "不添加对白",
                                        middlewares=[MiddlewareSelection(source_turn_id=source)])
    assert turn.status == "error" and not adapter.calls and turn.effective_input is None
    assert turn.middleware_snapshot["steps"][0]["status"] == "error"
    assert not turn.middleware_snapshot["steps"][0].get("result_blob_hash")
    assert (await world.types(session.id))[-1] == "turn.end"


async def test_failed_previous_not_silently_skipped_and_retry_source_is_explicit(world, monkeypatch):
    _, session, source = await setup_source(world)
    async with world.ctx.db.write() as db:
        db.add(Turn(id="failed", session_id=session.id, idx=1, status="error",
                    input=Message.user("失败").model_dump(), started_at=now_iso()))
    with pytest.raises(EvalpError) as exc:
        await world.sessions.run_turn(session.id, "下一轮", middlewares=[MiddlewareSelection()])
    assert exc.value.code == "ARTIFACT_SOURCE_INVALID"
    frozen = await world.ctx.svc(MiddlewareService).freeze(
        await _session_row(world, session.id), Message.user("重试"),
        [MiddlewareSelection(source_turn_id=source)])
    assert frozen["steps"][0]["source_turn_id"] == source


async def _session_row(world, session_id):
    async with world.ctx.db.read() as db:
        return await db.get(Session, session_id)


async def test_config_adapter_receives_json_and_subsequent_effective_history(world, fake_openai, monkeypatch):
    _, session, source = await setup_source(world)

    async def fetched(*_):
        return b'{"scenes":[1,2]}'
    monkeypatch.setattr("evalp.services.middleware.fetch_artifact", fetched)
    fake_openai.enqueue("完成", "继续")
    turn = await world.sessions.run_turn(session.id, "不添加对白",
                                        middlewares=[MiddlewareSelection(source_turn_id=source)])
    assert turn.status == "ok", turn.error
    assert fake_openai.chat_requests[0].messages[-1]["content"] == turn.effective_input.text()
    await world.sessions.run_turn(session.id, "第三轮")
    assert fake_openai.chat_requests[1].messages[-3]["content"] == turn.effective_input.text()


@pytest.mark.parametrize("transport", ["http", "subprocess"])
async def test_code_adapters_receive_combined_input(world, monkeypatch, transport):
    from tests.fixtures import sdk_agents

    async def fetched(*_):
        return b'{"scenes":[1,2]}'
    monkeypatch.setattr("evalp.services.middleware.fetch_artifact", fetched)
    server = sdk_agents.handle.start_http(0) if transport == "http" else None
    try:
        version = (await world.http_version(server.url)) if server else await world.subprocess_version()
        _, session, source = await setup_source(world, version=version)
        turn = await world.sessions.run_turn(session.id, "不添加对白",
                                            middlewares=[MiddlewareSelection(source_turn_id=source)])
        assert turn.status == "ok", turn.error
        assert "不添加对白" in turn.output.final.text() and '{"scenes":[1,2]}' in turn.output.final.text()
    finally:
        if server:
            server.close()


async def test_direct_hiagent_query_uses_saved_input_and_reuses_conversation(world, client):
    secret = await client.post("/api/v1/adapters/credentials", json={
        "name": "test", "secret": "agent-key", "adapter_id": "hiagent",
    })
    version = await world.code_version({"transport": "adapter", "adapter": {
        "id": "hiagent", "version": "1.0.0", "credential_ref": secret.json()["ref"], "config": {
            "base_url": "https://hiagent.example", "app_id": "app",
            "user_id": "user", "published_version": "47",
        },
    }})
    _, session, source = await setup_source(world, version=version)
    world.ctx.config.artifacts.allowed_hosts = "artifacts.example"
    frames = [
        {"event": "message_start", "id": "message-1"},
        {"event": "message", "answer": "完成"},
        {"event": "message_end", "id": "message-1"},
    ]
    sse = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    with respx.mock() as router:
        artifact = router.get(URL).mock(return_value=httpx.Response(200, json={"scenes": [1]}))
        create = router.post("https://hiagent.example/api/v1/create_conversation").mock(
            return_value=httpx.Response(200, json={"Conversation": {"AppConversationID": "conv-1"}}))
        query = router.post("https://hiagent.example/api/v1/chat_query_v2").mock(
            return_value=httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"}))
        turn = await world.sessions.run_turn(session.id, "不添加对白",
                                            middlewares=[MiddlewareSelection(source_turn_id=source)])
        assert turn.status == "ok", turn.error
        request = json.loads(query.calls[0].request.content)
        assert request["Query"] == turn.effective_input.text() and request["AppConversationID"] == "conv-1"
        assert "history" not in request and "apikey" not in artifact.calls[0].request.headers
        await world.sessions.run_turn(session.id, "第三轮")
        assert create.call_count == 1 and query.call_count == 2
        assert json.loads(query.calls[1].request.content)["AppConversationID"] == "conv-1"


async def test_business_bridge_and_multimodal_input_are_rejected(world):
    _, session, source = await setup_source(world)
    row = await _session_row(world, session.id)
    service = world.ctx.svc(MiddlewareService)
    with pytest.raises(EvalpError, match="文本输入"):
        await service.freeze(row, Message(role="user", content=[{"type": "image_url", "image_url": {}}]),
                             [MiddlewareSelection(source_turn_id=source)])
    row.adapter_snapshot = {"binding": {"config": {"business_bridge": {"base_url": "http://business"}}}}
    with pytest.raises(EvalpError, match="业务桥接"):
        await service.freeze(row, Message.user("不添加对白"), [MiddlewareSelection(source_turn_id=source)])
    # 旧会话 NULL 快照也必须按固定 Spec 原接入拦截桥接。
    legacy_binding = row.adapter_snapshot
    row.adapter_snapshot = None
    with pytest.raises(EvalpError, match="业务桥接"):
        await service.freeze(row, Message.user("不添加对白"), [MiddlewareSelection(source_turn_id=source)],
                             adapter_snapshot=legacy_binding)
