"""compare/align.py 的单测（纯函数）。"""

from __future__ import annotations

import pytest
from evalp.compare.align import (
    AlignTurn,
    Step,
    align_pair,
    align_steps,
    extract_steps,
    json_diff,
    pair_turns,
    turn_metrics,
)
from evalp.runtime.events import ev


def S(*sigs: str) -> list[Step]:
    return [Step(f"s{i + 1}", sig) for i, sig in enumerate(sigs)]


def test_align_steps_equal_insert_delete():
    left = S("llm", "tool:query_order", "llm", "final")
    right = S("llm", "tool:query_order", "tool:knowledge_search", "llm", "final")
    steps = align_steps(left, right)
    assert [(s["op"], s["sig"]) for s in steps] == [
        ("equal", "llm"),
        ("equal", "tool:query_order"),
        ("insert", "tool:knowledge_search"),
        ("equal", "llm"),
        ("equal", "final"),
    ]
    assert steps[2] == {"op": "insert", "right": "s3", "sig": "tool:knowledge_search"}
    assert steps[3]["left"] == "s3" and steps[3]["right"] == "s4"


def test_align_steps_replace_splits_into_delete_and_insert():
    steps = align_steps(S("llm", "tool:a", "final"), S("llm", "tool:b", "final"))
    ops = [(s["op"], s["sig"]) for s in steps]
    assert ops == [("equal", "llm"), ("delete", "tool:a"), ("insert", "tool:b"), ("equal", "final")]
    assert {s["op"] for s in steps} <= {"equal", "insert", "delete"}


def test_align_steps_empty_sides():
    assert align_steps([], []) == []
    assert [s["op"] for s in align_steps(S("llm"), [])] == ["delete"]
    assert [s["op"] for s in align_steps([], S("llm"))] == ["insert"]


def test_json_diff():
    assert json_diff({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 2]}) == []
    changes = json_diff(
        {"order_id": "A1", "x": {"y": 1}, "gone": 1}, {"order_id": "A2", "x": {"y": 2}, "new": 3}
    )
    by_path = {c["path"]: c for c in changes}
    assert by_path["order_id"]["left"] == "A1" and by_path["order_id"]["right"] == "A2"
    assert by_path["x.y"]["left"] == 1 and by_path["x.y"]["right"] == 2
    assert by_path["gone"]["op"] == "remove" and by_path["new"]["op"] == "add"
    assert json_diff([1, 2, 3], [1, 5])[0]["path"] == "[1]"
    assert json_diff(1, True)  # 类型不同视为不同


def test_pair_turns_by_pair_index_and_idx():
    a = [
        AlignTurn("l0", 0, pair_index=0),
        AlignTurn("l1", 1, pair_index=None),
        AlignTurn("l2", 2, pair_index=1),
    ]
    b = [AlignTurn("r0", 0, pair_index=0), AlignTurn("r1", 1, pair_index=1), AlignTurn("r2", 2, pair_index=2)]
    got = [(k, lt and lt.id, rt and rt.id) for k, lt, rt in pair_turns(a, b, "pair_index")]
    assert got == [(0, "l0", "r0"), (1, "l2", "r1"), (2, None, "r2")]  # 单侧消息（pair_index 为空）不参与
    got = [(k, lt and lt.id, rt and rt.id) for k, lt, rt in pair_turns(a, b, "idx")]
    assert got == [(0, "l0", "r0"), (1, "l1", "r1"), (2, "l2", "r2")]
    # 重放：原会话按 idx，新会话按 pair_index
    got = [
        (k, lt and lt.id, rt and rt.id)
        for k, lt, rt in pair_turns(a, b, left_by="idx", right_by="pair_index")
    ]
    assert got == [(0, "l0", "r0"), (1, "l1", "r1"), (2, "l2", "r2")]


def test_extract_steps_and_args_diff_and_metrics():
    def events(order_id: str, final: str):
        return [
            ev("turn.start", input={"role": "user", "content": "q"}),
            ev("llm.request", step_id="s1", model="m"),
            ev("llm.response", step_id="s1", content=None),
            ev("tool.call", step_id="s2", call_id="c", name="query_order", arguments={"order_id": order_id}),
            ev("tool.result", step_id="s2", call_id="c", name="query_order"),
            ev("message.assistant", message={"role": "assistant", "content": final}),
        ]

    steps = extract_steps(events("A1", "x"))
    assert [(s.id, s.sig) for s in steps] == [("s1", "llm"), ("s2", "tool:query_order"), ("final", "final")]

    left = AlignTurn(
        "l",
        0,
        final_text="x",
        latency_ms=100,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "llm_calls": 1},
        events=events("A1", "x"),
    )
    right = AlignTurn("r", 0, final_text="y", usage={"tool_calls": 2}, cost=0.5, events=events("A2", "y"))
    out = align_pair(0, left, right)
    assert out["tool_args_diff"] == [
        {
            "left": "s2",
            "right": "s2",
            "changes": [{"path": "order_id", "left": "A1", "right": "A2", "op": "change"}],
        }
    ]
    assert out["final_text"] == {"left": "x", "right": "y"}
    assert out["metrics"]["left"]["tokens"] == 15 and out["metrics"]["left"]["llm_calls"] == 1
    assert out["metrics"]["right"]["tool_calls"] == 2 and out["metrics"]["right"]["cost"] == 0.5
    assert turn_metrics(None) is None


def test_align_pair_one_side_missing():
    out = align_pair(3, None, AlignTurn("r", 3, events=[ev("llm.response", step_id="s1")]))
    assert out["left_turn_id"] is None and out["right_turn_id"] == "r"
    assert [s["op"] for s in out["steps"]] == ["insert"]
    assert out["metrics"]["left"] is None


@pytest.mark.parametrize("mapping", [False, True])
def test_recorded_metrics_use_source_duration_without_mutating_imported_values(mapping):
    payload = {
        "message": "录制来源",
        "source_mode": "recorded",
        "original_latency_ms": 71_235,
        "original_started_at": "2026-10-27T09:43:59.524Z",
        "original_ended_at": "2026-10-27T09:45:10.759729552Z",
    }
    source = {"type": "log", "payload": payload} if mapping else ev("log", **payload)
    turn = AlignTurn(
        "r", 0, latency_ms=697, ttft_ms=602,
        usage={"prompt_tokens": 270074, "completion_tokens": 5808, "llm_calls": 11, "tool_calls": 15},
        events=[source],
    )
    metrics = align_pair(0, AlignTurn("l", 0, latency_ms=44170, ttft_ms=38672), turn)["metrics"]
    assert metrics["left"]["latency_ms"] == 44170 and metrics["left"]["ttft_ms"] == 38672
    assert metrics["right"] == {
        "latency_ms": 71235, "ttft_ms": None, "tokens": 275882,
        "cost": 0, "llm_calls": 11, "tool_calls": 15,
    }
    assert turn.latency_ms == 697 and turn.ttft_ms == 602
    assert payload["original_latency_ms"] == 71235
    assert extract_steps([source]) == []


@pytest.mark.parametrize("ttft", [None, -1, float("nan"), float("inf"), "602", True, 0, 38672])
def test_recorded_original_timing_fallbacks(ttft):
    turn = AlignTurn("r", 0, latency_ms=697, ttft_ms=602, events=[ev(
        "log", message="录制来源", source_mode="recorded", original_latency_ms=-1, original_ttft_ms=ttft,
        original_started_at="2026-10-27T09:43:59.524Z",
        original_ended_at="2026-10-27T09:45:10.759729552Z",
    )])
    metrics = turn_metrics(turn)
    assert metrics["latency_ms"] == 71235
    assert metrics["ttft_ms"] == (ttft if type(ttft) is int and ttft >= 0 else None)
    turn.events = [ev("log", message="录制来源", source_mode="recorded", original_started_at="bad")]
    assert turn_metrics(turn)["ttft_ms"] is None  # 缺少原始总时间仍不回退到导入 TTFT
    turn.events = [
        ev("log", message="普通日志", source_mode="live", original_latency_ms=999, original_ttft_ms=999)
    ]
    assert turn_metrics(turn)["latency_ms"] == 697 and turn_metrics(turn)["ttft_ms"] == 602
    turn.events = [
        ev("log", message="录制来源", source_mode="recorded", original_latency_ms=0, original_ttft_ms=0)
    ]
    assert turn_metrics(turn)["latency_ms"] == 0 and turn_metrics(turn)["ttft_ms"] == 0


def test_remote_turn_metrics_use_remote_values_and_mark_not_applicable_ones():
    remote = AlignTurn("r", 0, latency_ms=20_000, ttft_ms=None, cost=0.0,
                       usage={"prompt_tokens": 9, "completion_tokens": 3, "llm_calls": 2, "tool_calls": 1,
                              "trace_status": "complete", "local_latency_ms": 31_000})
    assert turn_metrics(remote) == {
        "latency_ms": 20_000, "ttft_ms": None, "tokens": 12, "cost": None,
        "llm_calls": 2, "tool_calls": 1, "trace_status": "complete",
    }
    pending = AlignTurn("p", 0, latency_ms=None,
                        usage={"trace_status": "pending", "local_latency_ms": 31_000})
    assert turn_metrics(pending)["latency_ms"] is None and turn_metrics(pending)["trace_status"] == "pending"
    local = AlignTurn("l", 0, latency_ms=100, ttft_ms=30, cost=0.5, usage={"prompt_tokens": 1})
    assert "trace_status" not in turn_metrics(local) and turn_metrics(local)["cost"] == 0.5
