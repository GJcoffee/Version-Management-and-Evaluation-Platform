"""评测以远端指标为准：Trace 补齐前不评分、补齐期间不占用并发名额，补齐后用远端用时 / token 计入统计。

上游 SSE 与 Trace 全部是手写的虚构数据。
"""

import asyncio
import json

import httpx
import respx
from evalp.services.eval import EvalService

from tests.integration.adapter_env import configure_production, make_credential

CHAT = "https://hiagent.example/api/v1/chat_query_v2"
CREATE = "https://hiagent.example/api/v1/create_conversation"
OBSERVE = "https://observe.example/api/observe"
ANSWER = "订单已发货"
SSE = "".join(f"data: {json.dumps(f, ensure_ascii=False)}\n\n" for f in [
    {"event": "message_start", "id": "msg-1", "conversation_id": "conv-1"},
    {"event": "message", "answer": ANSWER},
    {"event": "message_end", "id": "msg-1", "input_tokens": 9, "output_tokens": 3},
])
TRACE = {"TraceID": "tr-1", "StartTime": "2026-11-01T00:00:00Z", "EndTime": "2026-11-01T00:00:20Z"}
SPAN = {
    "Context": {"TraceID": "tr-1", "SpanID": "a"}, "Name": "豆包",
    "StartTime": "2026-11-01T00:00:01Z", "EndTime": "2026-11-01T00:00:03Z",
    "Attributes": {"gen_ai.span_type": "llm", "message_id": "msg-1", "app_id": "app-test",
                   "gen_ai.agent.version": "47", "input_tokens": 9, "output_tokens": 3,
                   "input": "查订单", "output": json.dumps({"content": ANSWER}, ensure_ascii=False)},
}


class Gate:
    def __init__(self, trace=TRACE):
        self.open, self.trace = False, trace

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.params["Action"] == "GetTraceInfo":
            trace = self.trace if self.open else {**self.trace, "EndTime": None}
            return httpx.Response(200, json={"Result": trace})
        return httpx.Response(200, json={"Result": {"Items": [SPAN], "Total": 1, "HasMore": False}})


async def hiagent_version(ev, observe_headers=True):
    ref = await make_credential(ev.client, {"api_key": "k", **({"observe_headers": {"Cookie": "c"}}
                                                               if observe_headers else {})})
    await configure_production(ev.client, {
        "id": "hiagent", "version": "1.0.0", "credential_ref": ref, "config": {
            "base_url": "https://hiagent.example", "app_id": "app-test", "user_id": "u",
            "published_version": "47", "observe_base_url": "https://observe.example",
            "observation_wait_s": 30}})
    return await ev.code_version({"transport": "adapter"})


def mock_upstream(router, gate):
    router.post(CREATE).mock(return_value=httpx.Response(
        200, json={"Conversation": {"AppConversationID": "conv-1"}}))
    chat = router.post(CHAT).mock(return_value=httpx.Response(
        200, text=SSE, headers={"Content-Type": "text/event-stream"}))
    router.post(OBSERVE).mock(side_effect=gate)
    return chat


async def until(predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)


async def test_scoring_waits_for_trace_without_holding_the_concurrency_slot_and_stats_use_remote_values(ev):
    version = await hiagent_version(ev)
    await ev.dataset("ds", [{"case_key": f"c{i}", "input": "查订单", "expected": ANSWER} for i in range(3)])
    gate = Gate()
    with respx.mock() as router:
        chat = mock_upstream(router, gate)
        created = await ev.create(version, "asset://dataset/ds@latest", concurrency=1)
        run_id = created.runs[0].id

        async def all_executed():
            return {i.status for i in await ev.items(run_id)} == {"scoring"}

        await until(all_executed)  # 并发上限 1：补齐等待期间后面的用例照样能执行
        assert chat.call_count == 3
        items = await ev.items(run_id)
        assert all(i.latency_ms is None for i in items)  # 远端用时补齐前不用本地值
        assert (await ev.evals.get(run_id)).status == "running"
        assert all(not i.scores for i in items)  # 补齐前不评分

        gate.open = True
        detail = await ev.evals.wait_finished(run_id, timeout=30, interval=0.05)
    assert detail.status == "completed"
    items = await ev.items(run_id)
    assert {i.status for i in items} == {"done"} and all(i.passed for i in items)
    assert [i.latency_ms for i in items] == [20000] * 3
    assert all(i.usage["trace_status"] == "complete" for i in items)
    stats = detail.stats
    assert stats["latency_ms"] == {"p50": 20000, "p95": 20000} and stats["latency_missing"] == 0
    assert stats["tokens"] == {"prompt": 27, "completion": 9} and stats["remote"] is True


async def test_unavailable_trace_items_are_scored_but_excluded_from_latency_stats(ev):
    version = await hiagent_version(ev, observe_headers=False)
    await ev.dataset("ds", [{"case_key": "c0", "input": "查订单", "expected": ANSWER}])
    with respx.mock(assert_all_called=False) as router:
        mock_upstream(router, Gate())
        detail, _ = await ev.run(version, "asset://dataset/ds@latest")
    (item,) = await ev.items(detail.id)
    assert item.status == "done" and item.passed is True
    assert item.latency_ms is None and item.usage["trace_status"] == "unavailable"
    assert detail.stats["latency_ms"] == {"p50": None, "p95": None} and detail.stats["latency_missing"] == 1
    assert detail.stats["tokens"]["prompt"] == 9 and detail.stats["remote"] is True


async def test_items_waiting_for_trace_when_the_service_restarts_are_scored_after_recovery(ev):
    version = await hiagent_version(ev)
    await ev.dataset("ds", [{"case_key": "c0", "input": "查订单", "expected": ANSWER}])
    gate = Gate()
    with respx.mock() as router:
        chat = mock_upstream(router, gate)
        created = await ev.create(version, "asset://dataset/ds@latest")
        run_id = created.runs[0].id

        async def executed():
            return chat.call_count == 1 and (await ev.items(run_id))[0].status == "scoring"

        await until(executed)
        await ev.sessions.aclose()  # 补齐被中断
        await ev.ctx.svc(EvalService).aclose()  # 等待补齐的任务随进程消失
        await ev.sched.wait_idle(10)
        (item,) = await ev.items(run_id)
        assert item.status == "scoring"
        ev.sessions._recovered = False  # 模拟重启后的首次使用：遗留的 pending 标为 unavailable
        await ev.evals.reconcile()
        detail = await ev.evals.wait_finished(run_id, timeout=30, interval=0.05)
    (item,) = await ev.items(run_id)
    assert detail.status == "completed" and item.status == "done" and item.passed is True
    assert item.latency_ms is None and item.usage["trace_status"] == "unavailable"
