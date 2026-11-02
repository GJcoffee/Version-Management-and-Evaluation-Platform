"""适配器接入配置只有生产 / 测试两套：测试编辑后推送生产，每次变化存档，运行固化所用环境与存档号。"""

import httpx
import respx
from evalp.services.version import VersionService

from tests.integration.adapter_env import (
    API,
    configure_production,
    edit_test,
    hiagent_binding,
    make_credential,
    push,
)


async def state(client):
    return (await client.get(f"{API}/adapter-environments")).json()


async def test_both_environments_start_unconfigured_and_session_needs_production(world, client):
    current = await state(client)
    assert current["production"]["configured"] is False and current["production"]["revision"] == 0
    assert current["test"]["configured"] is False and current["test_selectable"] is False
    version = await world.code_version({"transport": "adapter"})
    response = await client.post(f"{API}/sessions", json={"version_id": version["id"]})
    assert response.status_code == 400
    assert "生产环境尚未配置" in response.json()["error"]["message"]


async def test_edit_test_then_push_to_production_archives_each_change(world, client):
    ref = await make_credential(client)
    edited = await edit_test(client, hiagent_binding(ref), "首次配置")
    assert edited["applied"] is True and edited["target"]["revision"] == 1
    assert (await state(client))["production"]["configured"] is False

    pushed = await push(client, "验证通过")
    assert pushed["applied"] is True and pushed["target"]["revision"] == 1
    production = (await state(client))["production"]
    assert production["binding"]["credential_ref"] == ref
    assert production["source"] == "push" and "验证通过" in production["reason"]
    assert production["origin"] == {"environment": "test", "revision": 1}

    # 版本发布后无需任何绑定操作，直接使用生产；之后发布的新版本同样沿用。
    for _ in range(2):
        version = await world.code_version({"transport": "adapter"})
        session = await world.session(version)
        snapshot = session.adapter_snapshot
        assert snapshot["environment"] == "production" and snapshot["revision"] == 1
        assert snapshot["binding"]["id"] == "hiagent" and "requested_environment" not in snapshot


async def test_identical_content_adds_no_archive(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    again = await client.put(f"{API}/adapter-environments/test", json={
        "binding": hiagent_binding(ref), "expected_revision": 1, "reason": "重复保存",
    })
    assert again.json()["applied"] is False and again.json()["message"] == "内容未变化"
    assert again.json()["target"]["revision"] == 1
    pushed = await push(client)
    assert pushed["applied"] is False and pushed["message"] == "内容未变化"
    for name in ("test", "production"):
        history = (await client.get(f"{API}/adapter-environments/{name}/revisions")).json()
        assert [r["revision"] for r in history] == [1]


async def test_test_edit_needs_current_revision_and_valid_binding(world, client):
    ref = await make_credential(client)
    await edit_test(client, hiagent_binding(ref))
    stale = await client.put(f"{API}/adapter-environments/test", json={
        "binding": hiagent_binding(ref, published_version="48"), "expected_revision": 0, "reason": "过期",
    })
    assert stale.status_code == 409
    for binding in (
        hiagent_binding(ref, "latest"),
        {**hiagent_binding("adapter:missing")},
        hiagent_binding(ref, business_bridge={"base_url": "http://bridge.example", "spec_hash": "0" * 64}),
    ):
        response = await client.put(f"{API}/adapter-environments/test", json={
            "binding": binding, "expected_revision": 1, "reason": "非法",
        })
        assert response.status_code == 400, response.text
    stale_push = await client.post(f"{API}/adapter-environments/production/push", json={"test_revision": 9})
    assert stale_push.status_code == 409


async def test_default_is_production_and_test_is_selectable_only_when_different(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    version = await world.code_version({"transport": "adapter"})
    assert (await state(client))["test_selectable"] is False

    # 测试与生产相同：选测试也落到生产，并留下请求的环境。
    same = await world.session(version, environment="test")
    assert same.adapter_snapshot["environment"] == "production"
    assert same.adapter_snapshot["requested_environment"] == "test"

    await edit_test(client, hiagent_binding(ref, published_version="48"))
    assert (await state(client))["test_selectable"] is True
    default = await world.session(version)
    tested = await world.session(version, environment="test")
    assert default.adapter_snapshot["environment"] == "production"
    assert default.adapter_snapshot["binding"]["config"]["published_version"] == "47"
    assert tested.adapter_snapshot["environment"] == "test" and tested.adapter_snapshot["revision"] == 2
    assert tested.adapter_snapshot["binding"]["config"]["published_version"] == "48"
    assert "requested_environment" not in tested.adapter_snapshot

    response = await client.post(f"{API}/sessions", json={"version_id": version["id"], "environment": "test"})
    assert response.status_code == 201
    assert response.json()["session"]["adapter_snapshot"]["environment"] == "test"
    assert (await client.post(f"{API}/sessions", json={
        "version_id": version["id"], "environment": "staging"})).status_code == 400


async def test_non_adapter_versions_ignore_environments(world, client):
    version = await world.http_version("http://127.0.0.1:9")
    session = await world.session(version, environment="test")
    assert session.adapter_snapshot is None


async def test_rollback_production_appends_an_archive_and_keeps_history(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    await edit_test(client, hiagent_binding(ref, published_version="48"))
    await push(client)
    assert (await state(client))["production"]["revision"] == 2

    rolled = await client.post(f"{API}/adapter-environments/production/rollback", json={
        "revision": 1, "reason": "新版本有问题"})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["applied"] is True and rolled.json()["target"]["revision"] == 3
    production = (await state(client))["production"]
    assert production["binding"]["config"]["published_version"] == "47"
    assert production["source"] == "rollback" and production["origin"] == {
        "environment": "production", "revision": 1}

    history = (await client.get(f"{API}/adapter-environments/production/revisions")).json()
    assert [(h["revision"], h["source"], h["current"]) for h in history] == [
        (3, "rollback", True), (2, "push", False), (1, "push", False)]
    # 回到与当前内容相同的存档不产生新存档；不存在的存档报 404。
    same = await client.post(f"{API}/adapter-environments/production/rollback", json={"revision": 3})
    assert same.json()["applied"] is False and same.json()["message"] == "内容未变化"
    assert (await client.post(f"{API}/adapter-environments/production/rollback", json={
        "revision": 9})).status_code == 404


async def test_old_sessions_keep_their_environment_and_revision(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    version = await world.code_version({"transport": "adapter"})
    old = await world.session(version)
    await edit_test(client, hiagent_binding(ref, published_version="48"))
    await push(client)
    new = await world.session(version)
    assert (await world.sessions.get_session(old.id)).adapter_snapshot["revision"] == 1
    assert new.adapter_snapshot["revision"] == 2
    # 评测等已固化快照的调用方沿用原快照，不取当前环境。
    reused = await world.sessions.create(
        version_id=version["id"], mode="eval", adapter_snapshot=old.adapter_snapshot)
    assert reused.adapter_snapshot["revision"] == 1


async def test_resolved_snapshot_is_per_environment_and_fingerprint_differs(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    await edit_test(client, hiagent_binding(ref, published_version="48"))
    version = await world.code_version({"transport": "adapter"})
    versions = world.ctx.svc(VersionService)
    prod = await versions.get_resolved(version["id"], environment="production")
    test = await versions.get_resolved(version["id"], environment="test")
    assert prod.adapter_snapshot["fingerprint"] != test.adapter_snapshot["fingerprint"]
    assert (await versions.get_resolved(version["id"])).adapter_snapshot is None


async def test_spec_adapter_field_is_rejected_but_adapter_transport_needs_none(world, client):
    ref = await make_credential(client)
    from tests.integration.p2a.conftest import Api

    out = await Api(client).agent("old-style", kind="code", spec={
        "kind": "code", "transport": "adapter", "adapter": hiagent_binding(ref),
        "source": {"capture_git": False},
    })
    validation = (await client.post(f"{API}/drafts/{out['draft']['id']}/validate")).json()
    assert any(e["path"] == "adapter" for e in validation["errors"]), validation
    version = await world.code_version({"transport": "adapter"})
    assert "adapter" not in version["spec"]


async def test_compare_live_uses_the_requested_environment(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    await edit_test(client, hiagent_binding(ref, published_version="48"))
    a = await world.code_version({"transport": "adapter"})
    b = await world.code_version({"transport": "adapter"})
    group = await client.post(f"{API}/compare", json={
        "mode": "live", "left": {"version_id": a["id"]}, "right": {"version_id": b["id"]},
        "environment": "test",
    })
    assert group.status_code == 201, group.text
    for side in ("left", "right"):
        session = await world.sessions.get_session(group.json()[side]["session_id"])
        assert session.adapter_snapshot["environment"] == "test"


async def test_managed_adapter_runs_multiple_turns_without_external_process(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    version = await world.code_version({"transport": "adapter"})
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
    # 上游会话 ID 写回会话快照，环境与存档号保持不变。
    pinned = (await world.sessions.get_session(session.id)).adapter_snapshot
    assert pinned["upstream"]["conversation_id"] == "conv-real"
    assert pinned["environment"] == "production" and pinned["revision"] == 1


async def test_test_environment_session_calls_the_test_configuration(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    await edit_test(client, hiagent_binding(ref, base_url="https://test-upstream.example"))
    version = await world.code_version({"transport": "adapter"})
    session = await world.session(version, environment="test")
    sse = 'data: {"event":"message","answer":"ok"}\n\ndata: {"event":"message_end","id":"m"}\n\n'
    with respx.mock(assert_all_called=True) as upstream:
        upstream.post("https://test-upstream.example/api/v1/create_conversation").respond(
            200, json={"Conversation": {"AppConversationID": "conv-test"}})
        upstream.post("https://test-upstream.example/api/v1/chat_query_v2").respond(
            200, text=sse, headers={"Content-Type": "text/event-stream"})
        turn = await world.sessions.run_turn(session.id, "hi", wait=True)
    assert turn.status == "ok", turn.error


async def test_stream_disconnect_is_unknown_and_never_automatically_resubmitted(world, client):
    ref = await make_credential(client)
    await configure_production(client, hiagent_binding(ref))
    version = await world.code_version({"transport": "adapter"})
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


async def test_legacy_business_bridge_snapshot_keeps_running_old_sessions(world, client):
    """旧会话固化的业务桥接快照继续按原方式执行；新建会话的环境配置不支持桥接。"""
    import json

    version = await world.code_version({
        "transport": "http", "http": {"base_url": "http://original.example"},
        "params": {"effective_config_sha256": "fixed-config", "source_mode": "live"},
    })
    ref = await make_credential(client)
    target = hiagent_binding(ref)
    target["config"]["business_bridge"] = {
        "base_url": "http://fixed-release.example", "spec_hash": version["spec_hash"],
    }
    snapshot = {"revision": 1, "binding": target, "source": "override", "fingerprint": "legacy"}
    session = await world.session(version, adapter_snapshot=snapshot)
    with respx.mock(assert_all_called=True) as upstream:
        route = upstream.post("http://fixed-release.example/evalp/run").respond(
            200, json={"output": {"role": "assistant", "content": "validated-business-output"}},
        )
        turn = await world.sessions.run_turn(session.id, "actual-input", wait=True)
        assert turn.status == "ok", turn.error
        assert turn.output.final.content == "validated-business-output"
        request = json.loads(route.calls[0].request.content)
        assert request["params"] == version["spec"]["params"]
        assert request["input"]["content"] == "actual-input"
    target["config"]["business_bridge"]["spec_hash"] = "0" * 64
    rejected = await world.session(version, adapter_snapshot={**snapshot, "binding": target})
    with respx.mock(assert_all_called=False) as upstream:
        route = upstream.post("http://fixed-release.example/evalp/run")
        turn = await world.sessions.run_turn(rejected.id, "hi", wait=True)
        assert turn.status == "error"
        assert "spec hash" in turn.error.message
        assert route.call_count == 0
