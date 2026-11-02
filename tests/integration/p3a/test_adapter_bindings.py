"""覆盖不改业务版本；旧运行固定快照；失败批次逐项留原绑定。"""

from copy import deepcopy

import httpx
import respx
from evalp.services.adapter import AdapterService, BindingUpdate
from evalp.services.version import VersionService
from evalp.store.models import EvalRun


async def credential(client):
    response = await client.post("/api/v1/adapters/credentials", json={
        "name": "test", "secret": "private-test-key", "adapter_id": "hiagent",
    })
    assert response.status_code == 201, response.text
    assert "private-test-key" not in response.text
    return response.json()["ref"]


def binding(ref, version="1.0.0"):
    return {"id": "hiagent", "version": version, "credential_ref": ref, "config": {
        "base_url": "https://upstream.example/api/proxy",
        "app_id": "app-test", "user_id": "user-test", "published_version": "47",
    }}


async def test_override_keeps_spec_and_pins_session(world, client):
    version = await world.http_version("http://127.0.0.1:9")
    original = deepcopy(version["spec"])
    old = await world.session(version)
    ref = await credential(client)
    body = {"binding": binding(ref), "expected_revision": 0, "reason": "覆盖",
            "acknowledge_transport_change": True}
    response = await client.put(f"/api/v1/versions/{version['id']}/adapter-binding", json=body)
    assert response.status_code == 200, response.text
    state = await client.get(f"/api/v1/versions/{version['id']}")
    assert state.json()["spec"] == original
    assert state.json()["spec_hash"] == version["spec_hash"]
    new = await world.session(version)
    assert old.adapter_snapshot["binding"] is None
    assert (await world.sessions.get_session(old.id)).adapter_snapshot["binding"] is None
    assert new.adapter_snapshot["revision"] == 1
    assert new.adapter_snapshot["binding"]["credential_ref"] == ref
    # 再次覆盖不影响旧会话后续轮所固定的接入。
    response = await client.put(f"/api/v1/versions/{version['id']}/adapter-binding", json=body)
    assert response.status_code == 409
    rollback = await client.post(f"/api/v1/versions/{version['id']}/adapter-binding/rollback", json={
        "revision": 0, "expected_revision": 1, "reason": "恢复",
    })
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["target"]["revision"] == 2
    assert (await world.sessions.get_session(new.id)).adapter_snapshot["revision"] == 1
    assert (await world.session(version)).adapter_snapshot["binding"] is None
    history = (await client.get(f"/api/v1/versions/{version['id']}/adapter-binding")).json()["history"]
    assert [h["revision"] for h in history] == [2, 1, 0]


async def test_saving_identical_binding_does_not_add_revision(world, client):
    version = await world.http_version("http://127.0.0.1:9")
    ref = await credential(client)
    url = f"/api/v1/versions/{version['id']}/adapter-binding"
    body = {"binding": binding(ref), "expected_revision": 0, "reason": "覆盖",
            "acknowledge_transport_change": True}
    assert (await client.put(url, json=body)).json()["target"]["revision"] == 1

    same = {**body, "expected_revision": 1, "reason": "重复保存"}
    result = (await client.put(url, json=same)).json()
    assert result["applied"] is False and result["message"] == "内容未变化"
    assert result["target"]["revision"] == 1
    preview = (await client.put(url, json={**same, "dry_run": True})).json()
    assert preview["applied"] is False and preview["message"] == "内容未变化"
    batch = (await client.post("/api/v1/adapters/bindings/batch", json={
        **same, "version_ids": [version["id"]], "expected_revisions": {version["id"]: 1},
    })).json()
    assert batch[0]["applied"] is False and batch[0]["message"] == "内容未变化"
    state = (await client.get(url)).json()
    assert state["current"]["revision"] == 1 and [h["revision"] for h in state["history"]] == [1, 0]
    assert (await world.session(version)).adapter_snapshot["revision"] == 1

    other = binding(ref)
    other["config"]["published_version"] = "48"
    changed = {**same, "binding": other}
    result = (await client.put(url, json=changed)).json()
    assert result["applied"] is True and result["target"]["revision"] == 2


async def test_batch_preview_partial_conflict_and_unknown_adapter(world, client):
    a = await world.http_version("http://127.0.0.1:9")
    b = await world.http_version("http://127.0.0.1:9")
    ref = await credential(client)
    body = {"binding": binding(ref), "version_ids": [a["id"], b["id"]],
            "expected_revision": 0, "expected_revisions": {a["id"]: 0, b["id"]: 7},
            "reason": "批量", "acknowledge_transport_change": True, "dry_run": True}
    result = (await client.post("/api/v1/adapters/bindings/batch", json=body)).json()
    assert result[0]["applied"] is False and result[1]["error"]["code"] == "CONFLICT"
    assert (await world.session(a)).adapter_snapshot["revision"] == 0
    body["dry_run"] = False
    result = (await client.post("/api/v1/adapters/bindings/batch", json=body)).json()
    assert result[0]["applied"] is True and result[1]["applied"] is False
    assert (await world.session(b)).adapter_snapshot["revision"] == 0
    body["binding"]["version"] = "latest"
    result = (await client.post("/api/v1/adapters/bindings/batch", json=body)).json()
    assert all(r.get("error") for r in result)


async def test_new_adapter_version_publish_and_credential_reference(world, client):
    ref = await credential(client)
    v = await world.code_version({"transport": "adapter", "adapter": binding(ref)})
    session = await world.session(v)
    assert session.adapter_snapshot["binding"]["id"] == "hiagent"
    assert session.adapter_snapshot["revision"] == 0
    refs = (await client.get("/api/v1/adapters/credentials")).json()
    assert refs == [{"ref": ref, "name": "test", "adapter_id": "hiagent"}]
    assert "private-test-key" not in str(v)


async def test_eval_snapshot_is_frozen_before_later_session_creation(world, client):
    # 此测试只验证任务快照和缓存身份，不启动评测或上游 LLM。
    version = await world.http_version("http://127.0.0.1:9")
    resolved = await world.ctx.svc(VersionService).get_resolved(version["id"])
    run = EvalRun(adapter_snapshot=resolved.adapter_snapshot)
    ref = await credential(client)
    await world.ctx.svc(AdapterService).update(version["id"], BindingUpdate(
        binding=binding(ref), expected_revision=0, reason="覆盖", acknowledge_transport_change=True,
    ))
    old = await world.sessions.create(
        version_id=version["id"], mode="eval", adapter_snapshot=run.adapter_snapshot,
    )
    assert old.adapter_snapshot["binding"] is None
    assert (await world.session(version)).adapter_snapshot["binding"]["id"] == "hiagent"


async def test_managed_adapter_runs_multiple_turns_without_external_process(world, client):
    ref = await credential(client)
    version = await world.code_version({"transport": "adapter", "adapter": binding(ref)})
    session = await world.session(version)
    with respx.mock(assert_all_called=True) as upstream:
        create = upstream.post("https://upstream.example/api/proxy/api/v1/create_conversation").mock(
            return_value=httpx.Response(200, json={"Conversation": {"AppConversationID": "conv-real"}}),
        )
        chat = upstream.post("https://upstream.example/api/proxy/api/v1/chat_query_v2").mock(
            return_value=httpx.Response(200, text=(
                'data: {"event":"message_start","id":"msg1","conversation_id":"conv-real"}\n\n'
                'data: {"event":"think_message_output_start","id":"thinking"}\n\n'
                'data: {"event":"think_message","answer":"reason"}\n\n'
                'data: {"event":"agent_thought","id":"tool1","tool":"fetch","tool_input":"{}"}\n\n'
                'data: {"event":"agent_thought_end","id":"tool1","observation":"ok"}\n\n'
                'data: {"event":"message","answer":"hello"}\n\n'
                'data: {"event":"message_end","id":"msg1","input_tokens":10,"output_tokens":3}\n\n'
            ), headers={"Content-Type": "text/event-stream"}),
        )
        for _ in range(2):
            turn = await world.sessions.run_turn(session.id, "hi", wait=True)
            assert turn.status == "ok", turn.error
            assert turn.output.final.content == "hello"
            assert turn.usage["prompt_tokens"] == 10 and turn.usage["tool_calls"] == 1
            assert turn.usage["llm_calls"] == 0  # SSE 阶段没有被冒充为已确认模型调用
        assert create.call_count == 1 and chat.call_count == 2
    events = await world.events(session.id)
    assert len([e for e in events if e.type == "llm.reasoning"]) == 2
    assert any(e.type == "source.raw" for e in events)
    pinned = (await world.sessions.get_session(session.id)).adapter_snapshot
    assert pinned["upstream"]["conversation_id"] == "conv-real"


async def test_stream_disconnect_is_unknown_and_never_automatically_resubmitted(world, client):
    ref = await credential(client)
    version = await world.code_version({"transport": "adapter", "adapter": binding(ref)})
    session = await world.session(version)
    with respx.mock() as upstream:
        upstream.post("https://upstream.example/api/proxy/api/v1/create_conversation").respond(
            200, json={"Conversation": {"AppConversationID": "conv"}},
        )
        chat = upstream.post("https://upstream.example/api/proxy/api/v1/chat_query_v2").respond(
            200, text='data: {"event":"message","answer":"partial"}\n\n',
        )
        turn = await world.sessions.run_turn(session.id, "hi", wait=True)
        assert turn.error.code == "UPSTREAM_RESULT_UNKNOWN"
        assert turn.usage["execution_status"] == "unknown"
        assert chat.call_count == 1


async def test_business_bridge_preserves_params_and_rejects_other_spec(world, client):
    version = await world.code_version({
        "transport": "http", "http": {"base_url": "http://original.example"},
        "params": {"effective_config_sha256": "fixed-config", "source_mode": "live"},
    })
    ref = await credential(client)
    target = binding(ref)
    target["config"]["business_bridge"] = {
        "base_url": "http://fixed-release.example", "spec_hash": version["spec_hash"],
    }
    await world.ctx.svc(AdapterService).update(version["id"], BindingUpdate(
        binding=target, expected_revision=0, reason="保留业务桥接", acknowledge_transport_change=True,
    ))
    session = await world.session(version)
    with respx.mock(assert_all_called=True) as upstream:
        route = upstream.post("http://fixed-release.example/evalp/run").respond(
            200, json={"output": {"role": "assistant", "content": "validated-business-output"}},
        )
        turn = await world.sessions.run_turn(session.id, "actual-input", wait=True)
        assert turn.status == "ok", turn.error
        assert turn.output.final.content == "validated-business-output"
        import json

        request = json.loads(route.calls[0].request.content)
        assert request["params"] == version["spec"]["params"]
        assert request["input"]["content"] == "actual-input"
    target["config"]["business_bridge"]["spec_hash"] = "0" * 64
    await world.ctx.svc(AdapterService).update(version["id"], BindingUpdate(
        binding=target, expected_revision=1, reason="检查版本门禁",
    ))
    rejected = await world.session(version)
    with respx.mock(assert_all_called=False) as upstream:
        route = upstream.post("http://fixed-release.example/evalp/run")
        turn = await world.sessions.run_turn(rejected.id, "hi", wait=True)
        assert turn.status == "error"
        assert "spec hash" in turn.error.message
        assert route.call_count == 0
