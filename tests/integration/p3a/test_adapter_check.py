"""Adapter 自检：正常 agent 全部通过；有问题的 agent 给出可读的报告而不崩溃。"""

import time

import pytest
from evalp.services.adapter_check import CheckReport, run_adapter_check

from tests.fixtures import sdk_agents
from tests.fixtures.agent_servers import RawAgentServer, json_response, ndjson_response

API = "/api/v1"


def by_name(report: CheckReport) -> dict:
    return {i.name: i for i in report.items}


def good_ndjson(h, body):
    n = len(body["history"])
    ndjson_response(
        h,
        [
            {"type": "turn.start", "payload": {"history_len": n}},
            {
                "type": "message.assistant",
                "payload": {"message": {"role": "assistant", "content": f"好[{n}]"}},
            },
        ],
    )


@pytest.fixture
def sdk_server():
    handle = sdk_agents.handle.start_http(0)
    yield handle
    handle.close()


async def test_sdk_http_agent_passes(world, ctx, sdk_server):
    v = await world.http_version(sdk_server.url)
    report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert report.ok, report.model_dump_json(indent=1)
    assert [i.name for i in report.items] == ["health", "single", "multi_turn", "timeout", "cancel", "events"]
    assert items["health"].status == items["single"].status == items["multi_turn"].status == "pass"
    assert items["events"].status == "pass" and items["events"].details["events"] > 5
    assert items["timeout"].status in ("pass", "skip") and items["cancel"].status in ("pass", "skip")
    assert report.transport == "http" and report.kind == "code" and report.agent_name
    assert sum(report.summary.values()) == len(report.items)
    # 自检不留下会话
    assert (await world.sessions.list_sessions(mode=["chat", "eval", "judge", "replay"])).items == []


async def test_slow_subprocess_agent_timeout_and_cancel_verified(world, ctx):
    v = await world.subprocess_version(env={"AGENT_SLOW": "0.8"})
    t0 = time.monotonic()
    report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert report.ok, report.model_dump_json(indent=1)
    assert items["timeout"].status == "pass" and items["cancel"].status == "pass"
    assert items["timeout"].details["pid"] and items["cancel"].details["pid"]
    assert time.monotonic() - t0 < 30


async def test_config_agent_with_llm(world, ctx, fake_openai):
    v = await world.config_version()
    fake_openai.slow(0.5)
    fake_openai.add_rule(match=None, reply="好的")
    report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert report.ok, report.model_dump_json(indent=1)
    assert items["multi_turn"].status == "pass" and "2 条历史" in items["multi_turn"].message
    assert items["timeout"].status == "pass" and items["cancel"].status == "pass"
    assert items["events"].status == "pass"


async def test_illegal_events_reported_readably(world, ctx):
    def bad(h, body):
        ndjson_response(h, [{"type": "bogus.event", "payload": {}}])

    with RawAgentServer(bad) as srv:
        v = await world.http_version(srv.url)
        report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert not report.ok
    assert items["single"].status == "fail" and "AGENT_PROTOCOL_ERROR" in items["single"].message
    assert "bogus.event" in items["single"].message
    assert [items[n].status for n in ("multi_turn", "timeout", "cancel")] == ["skip"] * 3
    assert items["events"].status == "fail" and items["events"].details["problems"]


async def test_bad_field_types_do_not_crash(world, ctx):
    def bad(h, body):
        ndjson_response(
            h,
            [
                {"type": "llm.response", "payload": {"usage": "不是对象"}},
                "这不是 JSON",
            ],
        )

    with RawAgentServer(bad) as srv:
        v = await world.http_version(srv.url)
        report = await run_adapter_check(ctx, v["id"])
    assert not report.ok and by_name(report)["single"].status == "fail"
    assert by_name(report)["single"].message


async def test_http_error_and_unhealthy(world, ctx):
    with RawAgentServer(lambda h, b: json_response(h, {"error": "boom"}, 500), health=503) as srv:
        v = await world.http_version(srv.url)
        report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert items["health"].status == "fail" and items["single"].status == "fail"
    assert not report.ok


async def test_history_not_used_is_detected(world, ctx):
    def forgetful(h, body):
        ndjson_response(
            h,
            [
                {"type": "turn.start", "payload": {"history_len": 0}},
                {
                    "type": "message.assistant",
                    "payload": {"message": {"role": "assistant", "content": "忘了"}},
                },
            ],
        )

    with RawAgentServer(forgetful) as srv:
        v = await world.http_version(srv.url)
        report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert items["single"].status == "pass"
    assert items["multi_turn"].status == "fail" and "历史" in items["multi_turn"].message


async def test_no_history_report_is_warning_and_unpaired_events(world, ctx):
    def sloppy(h, body):
        ndjson_response(
            h,
            [
                {
                    "type": "tool.result",
                    "step_id": "s1",
                    "payload": {"call_id": "c", "name": "t", "output": 1},
                },
                {"type": "span.start", "step_id": "s2", "payload": {"name": "x"}},
                {"type": "message.assistant", "payload": {"message": {"role": "assistant", "content": "ok"}}},
            ],
        )

    with RawAgentServer(sloppy) as srv:
        v = await world.http_version(srv.url)
        report = await run_adapter_check(ctx, v["id"])
    items = by_name(report)
    assert items["multi_turn"].status == "warn"
    assert items["events"].status == "fail"
    problems = " ".join(items["events"].details["problems"])
    assert "tool.result" in problems and "span.start" in problems


async def test_good_raw_agent_and_api(world, client):
    with RawAgentServer(good_ndjson) as srv:
        v = await world.http_version(srv.url)
        r = await client.post(f"{API}/versions/{v['id']}/adapter-check")
        assert r.status_code == 200, r.text
        body = r.json()
        names = {i["name"]: i["status"] for i in body["items"]}
        assert body["ok"] is True and names["single"] == "pass" and names["multi_turn"] == "pass"
        assert body["version_id"] == v["id"] and body["duration_ms"] >= 0
    assert (await client.post(f"{API}/versions/ver_nope/adapter-check")).status_code == 404


async def test_unreachable_agent(world, ctx):
    v = await world.http_version("http://127.0.0.1:9")
    report = await run_adapter_check(ctx, v["id"])
    assert not report.ok and by_name(report)["health"].status == "fail"
    assert by_name(report)["single"].status == "fail"
