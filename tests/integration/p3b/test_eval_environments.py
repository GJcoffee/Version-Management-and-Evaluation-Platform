"""评测按环境固化接入：run 记下环境与存档号，不同配置不共用缓存，同一配置共用。"""

import respx
from evalp.store.models import EvalRun
from sqlalchemy import select

from tests.integration.adapter_env import configure_production, edit_test, hiagent_binding, make_credential

SSE = 'data: {"event":"message","answer":"ok"}\n\ndata: {"event":"message_end","id":"m"}\n\n'


def mock_hiagent(router, host):
    router.post(f"https://{host}/api/v1/create_conversation").respond(
        200, json={"Conversation": {"AppConversationID": "conv"}})
    return router.post(f"https://{host}/api/v1/chat_query_v2").respond(
        200, text=SSE, headers={"Content-Type": "text/event-stream"})


async def snapshot_of(ev, run_id):
    async with ev.ctx.db.read() as s:
        return (await s.execute(select(EvalRun.adapter_snapshot).where(EvalRun.id == run_id))).scalar_one()


async def test_run_records_environment_and_cache_follows_the_configuration(ev):
    ref = await make_credential(ev.client)
    await configure_production(ev.client, hiagent_binding(ref, base_url="https://prod.example"))
    await edit_test(ev.client, hiagent_binding(ref, base_url="https://test.example"))
    version = await ev.code_version({"transport": "adapter"})
    await ev.dataset("ds", [{"case_key": "c0", "input": "hi", "expected": "ok"}])
    with respx.mock() as router:
        prod_chat = mock_hiagent(router, "prod.example")
        test_chat = mock_hiagent(router, "test.example")
        first, created = await ev.run(version, "asset://dataset/ds@latest")
        assert first.progress["cached"] == 0 and prod_chat.call_count == 1
        in_test, tested = await ev.run(version, "asset://dataset/ds@latest", environment="test")
        assert in_test.progress["cached"] == 0 and test_chat.call_count == 1  # 配置不同，不共用缓存
        again, _ = await ev.run(version, "asset://dataset/ds@latest")
        assert again.progress["cached"] == 1 and prod_chat.call_count == 1  # 同一配置共用缓存
    prod_snapshot = await snapshot_of(ev, created.runs[0].id)
    test_snapshot = await snapshot_of(ev, tested.runs[0].id)
    assert (prod_snapshot["environment"], prod_snapshot["revision"]) == ("production", 1)
    assert (test_snapshot["environment"], test_snapshot["revision"]) == ("test", 2)
    assert prod_snapshot["fingerprint"] != test_snapshot["fingerprint"]
    items = await ev.items(tested.runs[0].id)
    session = await ev.sessions.get_session(items[0].session_id)
    assert session.adapter_snapshot["environment"] == "test"


async def test_eval_over_http_accepts_environment_and_rejects_unknown_one(ev):
    version = await ev.http_version("http://127.0.0.1:9")
    await ev.dataset("ds", [{"case_key": "c0", "input": "hi"}])
    body = {"version_ids": [version["id"]], "dataset": "asset://dataset/ds@latest",
            "evaluators": [{"type": "exact_match"}], "repeats": 1, "dry_run": True}
    ok = await ev.client.post("/api/v1/evals", json={**body, "environment": "test"})
    assert ok.status_code == 200, ok.text
    bad = await ev.client.post("/api/v1/evals", json={**body, "environment": "staging"})
    assert bad.status_code == 400
