"""历史接入迁移必须原子、可审计，且不能以当前内容冒充历史执行。"""

import copy
import json

import pytest
import respx
from evalp.client import EvalpClient
from evalp.errors import AgentError
from evalp.mcp.server import build_server
from evalp.runtime.adapters.base import build_adapter
from evalp.services.version import VersionService
from evalp.store.models import (
    AdapterBindingMigration,
    AdapterEnvironmentRevision,
    EvalRun,
    Event,
    Session,
    VersionAdapterRoute,
)
from mcp import Client
from sqlalchemy import func, select

from tests.integration.adapter_env import configure_production, hiagent_binding, make_credential
from tests.integration.p3a.test_hiagent_adapter import TRACE, llm_span, observe

API = "/api/v1"


async def setup(world, client):
    ref = await make_credential(client, {"api_key": "private-test-key", "observe_headers": {"Cookie": "c"}})
    await configure_production(client, hiagent_binding(ref, observe_base_url="https://observe.example"))
    version = await world.http_version(
        "http://retired.example", params={"hiagent_app": "original-app", "hiagent_version": "original-v"},
    )
    session = await world.session(version)
    body = {
        "agent": version["agent_id"], "first_seq": 1, "last_seq": 1,
        "production_revision": 1, "test_revision": 1, "reason": "统一迁移",
    }
    return version, session, body


async def test_dry_run_atomic_apply_and_idempotence(world, client):
    version, session, body = await setup(world, client)
    with respx.mock(assert_all_called=False) as requests:
        preview = await client.post(f"{API}/adapters/binding-migrations", json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()["versions"] == preview.json()["sessions"] == preview.json()["drafts"] == 1
        assert preview.json()["applied"] is False
        assert (await world.sessions.get_session(session.id)).adapter_snapshot is None
        applied = await client.post(f"{API}/adapters/binding-migrations", json={**body, "dry_run": False})
        assert applied.status_code == 200, applied.text
        result = applied.json()
        assert result["applied"] is True
        assert not requests.calls
    migrated = await world.sessions.get_session(session.id)
    binding = migrated.adapter_snapshot["binding"]
    assert binding["adapter"] == "hiagent" and binding["major"] == 1
    assert "id" not in binding and "business_bridge" not in binding["config"]
    assert binding["config"]["app_id"] == "original-app"
    assert migrated.adapter_snapshot["execution_blocked_reason"]
    current = (await client.get(f"{API}/versions/{version['id']}")).json()
    for key in version:
        if key != "runtime_binding":
            assert current[key] == version[key]
    assert current["runtime_binding"]["transport"] == "adapter"
    async with world.ctx.db.read() as s:
        audit = await s.get(AdapterBindingMigration, result["id"])
        assert audit.before["sessions"][session.id] is None
        assert len(audit.before["drafts"]) == 1
        assert (await s.execute(select(func.count()).select_from(VersionAdapterRoute))).scalar() == 1
    again = (await client.post(
        f"{API}/adapters/binding-migrations", json={**body, "dry_run": False},
    )).json()
    assert not again["applied"] and again["already_migrated"] == 1
    assert again["sessions"] == again["drafts"] == again["environments"] == 0
    report = await client.get(f"{API}/adapters/binding-migrations/{result['id']}")
    assert report.json()["versions"] == 1
    assert "private-test-key" not in report.text and "before" not in report.json()


async def test_rejects_stale_environment_missing_versions_and_running_session(world, client):
    _, session, body = await setup(world, client)
    for over in ({"production_revision": 2}, {"last_seq": 2}):
        response = await client.post(
            f"{API}/adapters/binding-migrations", json={**body, **over, "dry_run": False},
        )
        assert response.status_code in {400, 409}
    async with world.ctx.db.write() as s:
        (await s.get(Session, session.id)).status = "running"
    response = await client.post(
        f"{API}/adapters/binding-migrations", json={**body, "dry_run": False},
    )
    assert response.status_code == 409
    async with world.ctx.db.read() as s:
        assert (await s.execute(select(func.count()).select_from(VersionAdapterRoute))).scalar() == 0
        assert (await s.execute(select(func.count()).select_from(AdapterBindingMigration))).scalar() == 0
        assert (await s.get(Session, session.id)).adapter_snapshot is None


async def test_migrated_runtime_requires_native_content_and_draft_roundtrip_stays_native(world, client):
    version, _, body = await setup(world, client)
    await client.post(f"{API}/adapters/binding-migrations", json={**body, "dry_run": False})
    resolved = await world.ctx.svc(VersionService).get_resolved(version["id"])
    with pytest.raises(AgentError, match="历史业务内容") as error:
        build_adapter(resolved, world.ctx)
    assert error.value.code == "NATIVE_DEPLOYMENT_REQUIRED"
    for name in ("production", "test"):
        session = await world.session(version, environment=name)
        assert session.adapter_snapshot["binding"]["adapter"] == "hiagent"
        with respx.mock(assert_all_called=False) as requests:
            turn = await world.sessions.run_turn(session.id, "不要运行当前版本", wait=True)
            assert turn.error.code == "NATIVE_DEPLOYMENT_REQUIRED"
            assert not requests.calls
    draft = (await client.post(
        f"{API}/versions/{version['id']}/to-draft", json={"draft_name": "restored"},
    )).json()["draft"]
    assert draft["spec"]["transport"] == "adapter" and draft["spec"]["native_deployment_required"]
    assert draft["spec"]["http"] is None and draft["spec"]["params"] == version["spec"]["params"]
    created = await client.post(f"{API}/agents/{version['agent_id']}/drafts", json={
        "name": "copied", "from_version_id": version["id"],
    })
    assert created.status_code == 201, created.text
    assert created.json()["draft"]["spec"]["transport"] == "adapter"


async def test_bridge_snapshots_and_retired_environment_keep_original_in_audit(world, client):
    version, _, body = await setup(world, client)
    ref = (await client.get(f"{API}/adapter-environments")).json()["production"]["binding"]["credential_ref"]
    bridge = hiagent_binding(ref, business_bridge={
        "base_url": "http://old-bridge.example", "spec_hash": version["spec_hash"],
    })
    legacy = {"id": bridge["adapter"], "version": "1.2.3", "config": bridge["config"],
              "credential_ref": ref}
    snapshot = {"binding": legacy, "upstream": {"conversation_id": "original-conv"}, "revision": 0}
    session = await world.session(version, adapter_snapshot=copy.deepcopy(snapshot))
    async with world.ctx.db.write() as s:
        s.add(AdapterEnvironmentRevision(
            environment="production", revision=2, binding=legacy, source="migration",
            reason="旧桥", created_at="2026-01-01T00:00:00Z",
        ))
    result = (await client.post(
        f"{API}/adapters/binding-migrations", json={**body, "dry_run": False},
    )).json()
    migrated = (await world.sessions.get_session(session.id)).adapter_snapshot
    assert migrated["upstream"] == snapshot["upstream"]
    assert "business_bridge" not in migrated["binding"]["config"]
    async with world.ctx.db.read() as s:
        audit = await s.get(AdapterBindingMigration, result["id"])
        assert audit.before["sessions"][session.id] == snapshot
        assert audit.before["environments"]["production:2"]["binding"] == legacy
        env = await s.get(AdapterEnvironmentRevision, ("production", 2))
        assert env.origin["retired_bridge"] is True
    response = await client.post(f"{API}/adapter-environments/production/rollback", json={"revision": 2})
    assert response.status_code == 400 and "停用" in response.text


async def test_migrated_legacy_turn_refreshes_by_original_span_message_id(world, client):
    version, session, body = await setup(world, client)
    with respx.mock() as router:
        router.post("http://retired.example/evalp/run").respond(
            200, json={"output": {"role": "assistant", "content": "历史回复"}},
        )
        turn = await world.sessions.run_turn(session.id, "历史输入", wait=True)
    async with world.ctx.db.write() as s:
        sess = await s.get(Session, session.id)
        sess.last_seq += 1
        s.add(Event(
            session_id=session.id, seq=sess.last_seq, turn_id=turn.id, type="span.start",
            ts="2026-01-01T00:00:00Z", payload={"name": "旧远端", "attrs": {"message_id": "msg-1"}},
        ))
    await client.post(f"{API}/adapters/binding-migrations", json={**body, "dry_run": False})
    with respx.mock() as router:
        route = router.post("https://observe.example/api/observe").mock(
            side_effect=observe(TRACE, [llm_span("a", input_tokens=4, output_tokens=1)]),
        )
        refreshed = await world.sessions.refresh_trace(turn.id)
        assert route.call_count == 2
        assert json.loads(route.calls[0].request.content) == {"MessageID": "msg-1"}
    assert refreshed.status == turn.status and refreshed.output == turn.output
    assert refreshed.usage["trace_refresh"]["message_id"] == "msg-1"
    assert refreshed.usage["trace_status"] == "complete"


async def test_mcp_exposes_dry_run_apply_and_report(world, client):
    version, _, body = await setup(world, client)
    server = build_server(client_factory=lambda _: EvalpClient(http=client))
    async with Client(server) as mcp:
        preview = await mcp.call_tool("migrate_adapter_bindings", body)
        assert not preview.is_error
        applied = await mcp.call_tool("migrate_adapter_bindings", {**body, "dry_run": False})
        assert not applied.is_error
        result = json.loads(applied.content[0].text)
        report = await mcp.call_tool("get_adapter_binding_migration", {"migration_id": result["id"]})
        assert not report.is_error and version["agent_id"] in report.content[0].text


async def test_completed_eval_bindings_migrate_without_changing_results(world, client):
    version, _, body = await setup(world, client)
    asset = await world.api.asset("file", "dataset-reference", "历史评测集修订")
    async with world.ctx.db.write() as s:
        from evalp.store.models import AssetRevision, EvalBatch

        revision = (await s.execute(select(AssetRevision).where(
            AssetRevision.asset_id == asset["asset"]["id"],
        ))).scalar_one()
        s.add(EvalBatch(id="evb-test", created_via="api", created_at="t"))
        await s.flush()
        s.add(EvalRun(
            id="evr-test", batch_id="evb-test", agent_id=version["agent_id"], version_id=version["id"],
            dataset_rev_id=revision.id, evaluators=[], evaluators_hash="fixed", status="completed",
            stats={"score": 0.75}, progress={"done": 7}, created_at="t",
        ))
    result = (await client.post(
        f"{API}/adapters/binding-migrations", json={**body, "dry_run": False},
    )).json()
    assert result["eval_runs"] == 1
    async with world.ctx.db.read() as s:
        run = await s.get(EvalRun, "evr-test")
        assert run.adapter_snapshot["binding"]["adapter"] == "hiagent"
        assert run.adapter_snapshot["execution_blocked_reason"]
        assert run.status == "completed" and run.stats == {"score": 0.75} and run.progress == {"done": 7}
        audit = await s.get(AdapterBindingMigration, result["id"])
        assert audit.before["eval_runs"]["evr-test"] is None
