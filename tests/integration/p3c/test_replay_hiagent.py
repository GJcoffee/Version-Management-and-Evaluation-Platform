"""HiAgent 直接接入由上游维护会话历史：不接受外部历史，锚定重放必须被拒绝，自由重放逐轮串行可用。"""

import json

import httpx
import respx

from .conftest import API
from .test_replay_modes import _source

SSE = "".join(f"data: {json.dumps(f)}\n\n" for f in [
    {"event": "message_start", "id": "msg-1", "conversation_id": "conv-1"},
    {"event": "message", "answer": "收到"},
    {"event": "message_end", "id": "msg-1"},
])


async def _hiagent_version(cw):
    ref = (await cw.client.post(f"{API}/adapters/credentials", json={
        "name": "t", "secret": "key", "adapter_id": "hiagent"})).json()["ref"]
    return await cw.code_version({"transport": "adapter", "adapter": {
        "id": "hiagent", "version": "1.0.0", "credential_ref": ref, "config": {
            "base_url": "https://hiagent.example", "app_id": "a", "user_id": "u", "published_version": "47"},
    }})


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
