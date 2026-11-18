"""覆盖密钥保存、轮换、隔离、禁用与链路查询的实际服务边界。"""

import json

import pytest
from evalp.runtime.adapters.platforms.hiagent import hicli_observe as cli
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.trace import TraceOnlyTarget, collect_trace
from evalp.runtime.trace_credentials import KeyConnectionService
from evalp.schemas.key_connection import KeyConnectionCreate
from pydantic import ValidationError

AK, SK = "test-private-access-id", "test-private-access-secret"
BODY = {"name": "观测密钥", "origin": "https://web.example", "top_url": "https://top.example:30040",
        "workspace_id": "workspace", "access_key_id": AK, "access_key_secret": SK}


@pytest.fixture
def queries(monkeypatch):
    calls = []

    async def query(config, action, body, *, identity=None):
        calls.append((config, action, body, identity))
        if action == "GetTraceInfo":
            return {"TraceID": "tid", "MessageID": "message", "EndTime": "2026-01-01T00:00:01Z"}
        return {"Items": [], "Total": 0, "HasMore": False}

    monkeypatch.setattr(cli, "query_hicli_trace", query)
    return calls


def config(row, **patch):
    return HiAgentConfig(
        base_url=row["origin"], app_id="a", user_id="u", published_version="1",
        **{"observe_transport": "hicli", "observe_key_ref": row["id"],
           "observe_base_url": row["origin"], "observe_hicli_base_url": row["top_url"],
           "workspace_id": row["workspace_id"], **patch},
    )


async def test_api_encrypts_keys_and_never_returns_them(client, ctx, queries):
    created = await client.post('/api/v1/adapters/key-connections', json=BODY)
    assert created.status_code == 201
    row = created.json()
    assert row['status'] == 'active' and row['revision'] == 1
    listed = await client.get('/api/v1/adapters/key-connections')
    assert listed.json() == [row]
    for response in [created, listed]:
        assert all(s not in response.text for s in [AK, SK, 'secret_ref', 'access_key_secret'])
    stored = await ctx.svc(KeyConnectionService).get(row['id'])
    secret = json.loads(await ctx.secrets.get(stored.secret_ref))
    assert secret == {'access_key_id': AK, 'secret_access_key': SK}
    assert queries[0][3] == secret
    for path in [ctx.config.db_path, ctx.config.secrets_path]:
        assert AK.encode() not in path.read_bytes() and SK.encode() not in path.read_bytes()


async def test_rotation_preservation_stale_writes_and_disable(client, ctx, queries):
    row = (await client.post('/api/v1/adapters/key-connections', json=BODY)).json()
    route = '/api/v1/adapters/key-connections/' + row['id']
    manager = ctx.svc(KeyConnectionService)
    before_ref = (await manager.get(row['id'])).secret_ref
    response = await client.put(route, json={'expected_revision': 1, 'name': '新名称'})
    assert response.json()['revision'] == 2
    assert (await manager.get(row['id'])).secret_ref == before_ref
    partial = await client.put(route, json={'expected_revision': 2, 'access_key_id': 'new-ak'})
    assert partial.status_code == 400
    changed = await client.put(route, json={
        'expected_revision': 2, 'access_key_id': 'new-ak', 'access_key_secret': 'new-sk',
    })
    assert changed.json()['revision'] == 3
    assert queries[-1][3] == {'access_key_id': 'new-ak', 'secret_access_key': 'new-sk'}
    assert (await client.put(route, json={'expected_revision': 2, 'name': 'stale'})).status_code == 409
    disabled = await client.put(route, json={'expected_revision': 3, 'enabled': False})
    assert disabled.json()['status'] == 'disabled'
    count = len(queries)
    with pytest.raises(cli.HicliObserveError, match='停用'):
        await manager.query(row['id'], config(row), 'GetTraceInfo', {})
    assert len(queries) == count


@pytest.mark.parametrize('patch', [
    {'workspace_id': 'other'}, {'observe_base_url': 'https://other.example'},
    {'observe_hicli_base_url': 'https://other.example:30040'},
])
async def test_wrong_targets_are_rejected_before_subprocess(ctx, queries, patch):
    manager = ctx.svc(KeyConnectionService)
    row = await manager.create(KeyConnectionCreate(**BODY))
    count = len(queries)
    with pytest.raises(cli.HicliObserveError, match='不匹配'):
        await manager.query(row['id'], config(row, **patch), 'GetTraceInfo', {})
    assert len(queries) == count


async def test_probe_failure_is_visible_and_does_not_reflect_cli_secrets(client, monkeypatch):
    async def failure(*args, **kwargs):
        raise cli.HicliObserveError(SK)

    monkeypatch.setattr(cli, 'query_hicli_trace', failure)
    created = await client.post('/api/v1/adapters/key-connections', json=BODY)
    assert created.status_code == 201
    assert created.json()['status'] == 'check_failed' and SK not in created.text


async def test_local_auth_connection_passes_no_copied_identity_and_traces_audit_reference(ctx, queries):
    manager = ctx.svc(KeyConnectionService)
    row = await manager.create(KeyConnectionCreate(**{
        k: v for k, v in BODY.items() if not k.startswith('access_key')
    }, credential_source='hicli'))
    assert (await manager.get(row['id'])).secret_ref is None
    assert queries[0][3] is None
    events = [e async for e in collect_trace(
        config(row), 'published-key', TraceOnlyTarget('message'), key_connections=manager,
    )]
    metrics = next(e.payload for e in events if e.type == 'metrics')
    assert metrics['trace_auth']['key_connection_ref'] == row['id']
    assert metrics['trace_auth']['credential_source'] == 'hicli'
    assert metrics['trace_auth']['config_revision'] == 1


@pytest.mark.parametrize('patch', [
    {'origin': 'https://web.example/path'}, {'top_url': 'https://top.example/?key=private'},
    {'workspace_id': '  '}, {'access_key_secret': '  '}, {'credential_source': 'hicli'},
])
def test_rejects_invalid_identity_and_target_config(patch):
    with pytest.raises(ValidationError):
        KeyConnectionCreate(**{**BODY, **patch})
