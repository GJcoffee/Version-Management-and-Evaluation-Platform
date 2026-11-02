"""services/eval.py：Item 的用量 / 用时由各轮规范指标汇总（远端为主）。"""

from types import SimpleNamespace

from evalp.services.eval import aggregate_turns


def turn(latency, status=None, **usage):
    u = {"prompt_tokens": 9, "completion_tokens": 3, "llm_calls": 1, "tool_calls": 1, **usage}
    if status:
        u["trace_status"] = status
    return SimpleNamespace(usage=u, latency_ms=latency, cost=0.0)


def test_remote_turns_sum_remote_latency_and_report_the_worst_trace_status():
    usage, cost, latency = aggregate_turns([turn(20_000, "complete"), turn(5_000, "partial")], None)
    assert latency == 25_000 and usage["trace_status"] == "partial" and usage["turns"] == 2
    assert usage["prompt_tokens"] == 18 and usage["llm_calls"] == 2 and cost == 0.0


def test_a_missing_remote_latency_leaves_the_item_latency_empty_instead_of_a_partial_sum():
    _, _, latency = aggregate_turns([turn(20_000, "complete"), turn(None, "unavailable")], None)
    assert latency is None
    usage, _, latency = aggregate_turns([turn(None, "pending")], None)
    assert latency is None and usage["trace_status"] == "pending"


def test_local_agents_keep_their_own_measurements_and_failed_attempts():
    usage, cost, latency = aggregate_turns(
        [turn(100), turn(250)], {"failed_attempts": [{"attempt": 1}], "wasted_cost": 0.2})
    assert latency == 350 and "trace_status" not in usage
    assert usage["failed_attempts"] == [{"attempt": 1}] and usage["wasted_cost"] == 0.2
