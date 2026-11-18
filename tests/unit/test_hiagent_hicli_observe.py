import asyncio
import json
from pathlib import Path

import pytest
from evalp.runtime.adapters.platforms.hiagent import hicli_observe as cli
from evalp.runtime.adapters.platforms.hiagent import trace as trace_module
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.mapper import HiAgentMapper
from pydantic import ValidationError


def config(**patch):
    return HiAgentConfig(**{
        "base_url": "https://web.example/api/proxy", "app_id": "app", "user_id": "u",
        "published_version": "1", "workspace_id": "workspace", "observe_transport": "hicli",
        "observe_base_url": "https://web.example", "observe_hicli_base_url": "https://top.example:30040",
        **patch,
    })


class Process:
    def __init__(self, payload, code=0):
        self.payload = payload
        self.returncode = code

    async def communicate(self):
        return json.dumps(self.payload).encode(), b"private diagnostic must never escape"


async def test_query_pins_endpoint_and_workspace_and_removes_request_file(monkeypatch):
    recorded = {}

    async def spawn(*args, **kwargs):
        recorded["args"] = args
        request = Path(args[-1])
        recorded["path"] = request
        assert json.loads(request.read_text()) == {"WorkspaceID": "workspace", "MessageID": "message"}
        assert kwargs == {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE}
        return Process({"TraceID": "trace", "EndTime": "done"})

    monkeypatch.setattr(cli, "_command", lambda: ["hicli"])
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", spawn)
    result = await cli.query_hicli_trace(config(), "GetTraceInfo", {"MessageID": "message"})
    assert result["TraceID"] == "trace"
    assert recorded["args"][1:4] == ("observe", "--base-url", "https://top.example:30040")
    assert not recorded["path"].exists()


async def test_saved_key_identity_is_isolated_and_never_written_to_argv_or_disk(monkeypatch):
    recorded = {}
    monkeypatch.setenv('HIAGENT_ACCESS_KEY_ID', 'wrong-global-key')
    monkeypatch.setenv('HIAGENT_OBSERVE_API_PATH', '/wrong-path')
    monkeypatch.setenv('HICLI_AUTH_FILE', '/wrong-auth-file')

    async def spawn(*args, **kwargs):
        recorded['env'] = kwargs['env']
        assert 'saved-ak' not in str(args) and 'saved-sk' not in str(args)
        auth = Path(args[args.index('--auth-file') + 1])
        recorded['auth'] = auth
        assert auth.read_text() == '{}'
        for path in auth.parent.iterdir():
            assert 'saved-ak' not in path.read_text() and 'saved-sk' not in path.read_text()
        return Process({'TraceID': 'trace'})

    monkeypatch.setattr(cli, '_command', lambda: ['hicli'])
    monkeypatch.setattr(cli.asyncio, 'create_subprocess_exec', spawn)
    await cli.query_hicli_trace(config(), 'GetTraceInfo', {}, identity={
        'access_key_id': 'saved-ak', 'secret_access_key': 'saved-sk',
    })
    assert recorded['env']['HIAGENT_ACCESS_KEY_ID'] == 'saved-ak'
    assert recorded['env']['HIAGENT_ACCESS_KEY_SECRET'] == 'saved-sk'
    assert 'HIAGENT_OBSERVE_API_PATH' not in recorded['env']
    assert 'HICLI_AUTH_FILE' not in recorded['env']
    assert not recorded['auth'].exists()


@pytest.mark.parametrize("action,body", [
    ("DeleteTrace", {}), ("GetTraceInfo", {"WorkspaceID": "another-workspace"}),
])
async def test_rejects_writes_and_cross_workspace_requests(action, body):
    with pytest.raises(cli.HicliObserveError):
        await cli.query_hicli_trace(config(), action, body)


@pytest.mark.parametrize("payload,code", [
    ({"private": "secret-value"}, 1), ([], 0), ({"data": []}, 0),
    ({"ResponseMetadata": {"Error": {"Message": "secret-value"}}}, 0),
])
async def test_failures_do_not_expose_cli_payloads_or_diagnostics(monkeypatch, payload, code):
    async def spawn(*args, **kwargs):
        return Process(payload, code)

    monkeypatch.setattr(cli, "_command", lambda: ["hicli"])
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(cli.HicliObserveError) as error:
        await cli.query_hicli_trace(config(), "GetTraceInfo", {})
    assert "secret-value" not in str(error.value)
    assert "private diagnostic" not in str(error.value)


@pytest.mark.parametrize("patch", [
    {"observe_hicli_base_url": None}, {"workspace_id": None},
    {"observe_account_ref": "browser-account"},
    {"observe_hicli_base_url": "https://top.example/api"},
])
def test_hicli_config_requires_explicit_target_and_single_authentication_method(patch):
    with pytest.raises(ValidationError):
        config(**patch)


async def test_collects_native_metrics_through_hicli_without_cookie_headers(monkeypatch):
    calls = []

    async def query(settings, action, body):
        calls.append((action, body))
        assert body["WorkspaceID"] == "workspace"
        if action == "GetTraceInfo":
            return {
                "TraceID": "trace", "MessageID": "message",
                "StartTime": "2026-01-01T00:00:00Z", "EndTime": "2026-01-01T00:00:01Z",
            }
        return {"Total": 1, "HasMore": False, "Items": [{
            "Context": {"TraceID": "trace", "SpanID": "model-span"}, "Name": "model",
            "StartTime": "2026-01-01T00:00:00Z", "EndTime": "2026-01-01T00:00:01Z",
            "Attributes": {"gen_ai.span_type": "core_llm", "app_id": "app",
                           "gen_ai.agent.version": "1", "message_id": "message",
                           "input_tokens": "2", "output_tokens": "3", "output": "answer"},
        }]}

    monkeypatch.setattr(trace_module, "query_hicli_trace", query)
    mapper = HiAgentMapper(config())
    mapper.message_id = "message"
    events = [event async for event in trace_module.collect_trace(config(), "published-api-key", mapper)]
    metrics = next(event.payload for event in events if event.type == "metrics")
    assert [call[0] for call in calls] == ["GetTraceInfo", "ListTraceSpans"]
    assert metrics["trace_status"] == "complete"
    assert metrics["usage"]["prompt_tokens"] == 2
    assert metrics["usage"]["completion_tokens"] == 3
    assert metrics["trace_auth"]["transport"] == "hicli"
