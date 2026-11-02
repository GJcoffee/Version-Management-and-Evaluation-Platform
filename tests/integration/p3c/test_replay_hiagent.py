"""HiAgent 直接接入由上游维护会话历史：不接受外部历史，锚定重放必须被拒绝，自由重放逐轮串行可用。"""

import json

import httpx
import respx

from tests.integration.adapter_env import (
    configure_production,
    edit_test,
    hiagent_binding,
    make_credential,
)

from .conftest import API
from .test_replay_modes import _source

SSE = "".join(f"data: {json.dumps(f)}\n\n" for f in [
    {"event": "message_start", "id": "msg-1", "conversation_id": "conv-1"},
    {"event": "message", "answer": "收到"},
    {"event": "message_end", "id": "msg-1"},
])


async def _hiagent_version(cw):
    ref = await make_credential(cw.client, "key")
    await configure_production(cw.client, hiagent_binding(
        ref, base_url="https://hiagent.example", app_id="a", user_id="u"))
    return await cw.code_version({"transport": "adapter"})


async def test_anchored_replay_is_refused_for_a_hiagent_version(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    target = await _hiagent_version(cw)
    r = await cw.client.post(f"{API}/sessions/{src.id}/replay", json={
        "target_version_id": target["id"], "mode": "anchored"})
    assert r.status_code == 400, r.text
    assert "HiAgent" in r.json()["error"]["message"] and "自由重放" in r.json()["error"]["message"]


async def test_free_replay_still_runs_serially_on_a_hiagent_version(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    target = await _hiagent_version(cw)
    with respx.mock() as router:
        router.post("https://hiagent.example/api/v1/create_conversation").mock(
            return_value=httpx.Response(200, json={"Conversation": {"AppConversationID": "conv-1"}}))
        chat = router.post("https://hiagent.example/api/v1/chat_query_v2").mock(
            return_value=httpx.Response(200, text=SSE, headers={"Content-Type": "text/event-stream"}))
        r = await cw.client.post(f"{API}/sessions/{src.id}/replay", json={
            "target_version_id": target["id"], "mode": "free"})
        assert r.status_code == 201, r.text
        await cw.compare.wait_replay(r.json()["session_id"], 15)
        assert chat.call_count == 2


async def test_replay_uses_production_by_default_and_test_when_asked(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    target = await _hiagent_version(cw)
    production = (await cw.client.get(f"{API}/adapter-environments")).json()["production"]
    ref = production["binding"]["credential_ref"]
    await edit_test(cw.client, hiagent_binding(
        ref, base_url="https://hiagent.example", app_id="a", user_id="u", published_version="48"))
    with respx.mock() as router:
        router.post("https://hiagent.example/api/v1/create_conversation").mock(
            return_value=httpx.Response(200, json={"Conversation": {"AppConversationID": "conv-1"}}))
        router.post("https://hiagent.example/api/v1/chat_query_v2").mock(
            return_value=httpx.Response(200, text=SSE, headers={"Content-Type": "text/event-stream"}))
        snapshots = {}
        for environment in (None, "test"):
            body = {"target_version_id": target["id"], "mode": "free"}
            if environment:
                body["environment"] = environment
            r = await cw.client.post(f"{API}/sessions/{src.id}/replay", json=body)
            assert r.status_code == 201, r.text
            await cw.compare.wait_replay(r.json()["session_id"], 15)
            session = await cw.sessions.get_session(r.json()["session_id"])
            snapshots[environment] = session.adapter_snapshot
    assert snapshots[None]["environment"] == "production" and snapshots[None]["revision"] == 1
    assert snapshots["test"]["environment"] == "test" and snapshots["test"]["revision"] == 2
