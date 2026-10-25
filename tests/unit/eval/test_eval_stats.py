"""services/eval_stats.py：汇总指标（详设 8.6）的口径。"""

import pytest
from evalp.services.eval_stats import (
    ItemRow,
    ScoreRow,
    case_scores,
    compute_passed,
    compute_stats,
    item_score,
)


def item(i, case, rep, status="done", passed=1, tags=(), cost=0.001, cached=False, lat=100, usage=None):
    return ItemRow(
        id=i,
        case_id=case,
        case_key=case,
        repeat_idx=rep,
        status=status,
        tags=list(tags),
        passed=passed,
        latency_ms=lat,
        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5},
        cost=cost,
        cached=cached,
    )


def score(i, key="em", st="done", s=1.0, passed=1, detail=None):
    return ScoreRow(i, key, st, s, passed, detail)


def test_compute_passed_requires_all_gates_and_ignores_unusable():
    rows = [
        score("i", "a", passed=1),
        score("i", "b", passed=0),
        score("i", "h", "pending_human", None, None),
    ]
    assert compute_passed(["a", "b", "h"], rows) == 0
    assert compute_passed(["a"], rows) == 1
    assert compute_passed(["h"], rows) is None  # 只有未出分的门禁评估器：通过与否未知
    assert compute_passed(["a", "x"], rows) == 1
    errored = [score("i", "a", "error", None, None)]
    assert compute_passed(["a"], errored) is None


def test_item_score_and_case_scores():
    it_ok = item("1", "c1", 0)
    it_failed = item("2", "c1", 1, status="failed", passed=None)
    it_cancel = item("3", "c2", 0, status="cancelled", passed=None)
    scores = [score("1", "a", s=0.8), score("1", "b", s=0.4), score("1", "nogate", s=0.0)]
    assert item_score(it_ok, scores, ["a", "b"]) == pytest.approx(0.6)
    assert item_score(it_failed, [], ["a"]) == 0.0  # 执行失败记 0 分
    assert item_score(it_cancel, [], ["a"]) is None
    cs = case_scores([it_ok, it_failed, it_cancel], scores, ["a", "b"])
    assert cs == {"c1": [pytest.approx(0.6), 0.0]}  # 已取消的用例不出现


def test_stats_pass_rate_failed_counts_as_not_passed_and_unknown_is_excluded():
    items = [
        item("1", "A", 0, passed=1),
        item("2", "A", 1, passed=1),
        item("3", "A", 2, passed=0),
        item("4", "B", 0, status="failed", passed=None),
        item("5", "B", 1, passed=None),  # 通过与否未知：不参与
        item("6", "C", 0, passed=1),
    ]
    scores = [score(str(i)) for i in (1, 2, 6)] + [score("3", s=0.0, passed=0)]
    st = compute_stats(items, scores, evaluator_keys=["em"], repeats=3)
    # A=2/3，B=0/1，C=1/1
    assert st["pass_rate"] == pytest.approx((2 / 3 + 0 + 1) / 3)
    assert st["failed"] == 1 and st["done"] == 5 and st["cases"] == 3 and st["items"] == 6
    assert st["flaky_cases"] == 1  # 只有 A 的重复结果不一致
    assert st["error_rate"] == pytest.approx(1 / 6)


def test_pass_at_k_only_when_repeats_allow():
    items = [item(f"{c}{r}", c, r, passed=int(r == 0)) for c in "ab" for r in range(3)]
    st3 = compute_stats(items, [], evaluator_keys=[], repeats=3)
    assert set(st3["pass_at_k"]) == {"1", "3"}
    assert st3["pass_at_k"]["1"] == pytest.approx(1 / 3)
    assert st3["pass_at_k"]["3"] == 1.0
    st1 = compute_stats(items[:1], [], evaluator_keys=[], repeats=1)
    assert set(st1["pass_at_k"]) == {"1"}
    st2 = compute_stats(items[:2], [], evaluator_keys=[], repeats=2)
    assert "3" not in st2["pass_at_k"]


def test_evaluator_stats_and_error_counters():
    items = [item(str(i), f"c{i}", 0) for i in range(4)]
    scores = [
        score("0", s=1.0),
        score("1", s=0.0, passed=0),
        score("2", st="error", s=None, passed=None, detail={"parse_failed": True}),
        score("3", st="pending_human", s=None, passed=None),
    ]
    st = compute_stats(items, scores, evaluator_keys=["em"], repeats=1)
    e = st["evaluators"]["em"]
    assert e["n"] == 2 and e["mean"] == 0.5 and e["pass_rate"] == 0.5
    assert e["errors"] == 1 and e["parse_failures"] == 1 and e["pending_human"] == 1
    assert st["score_error_rate"] == pytest.approx(1 / 4)


def test_cost_split_between_nominal_new_and_wasted():
    items = [
        item("1", "a", 0, cost=0.01),
        item("2", "b", 0, cost=0.02, cached=True),
        item("3", "c", 0, cost=0.03, usage={"prompt_tokens": 1, "completion_tokens": 1, "wasted_cost": 0.5}),
    ]
    st = compute_stats(items, [], evaluator_keys=[], repeats=1)
    assert st["cost"] == pytest.approx(0.06) and st["cost_new"] == pytest.approx(0.04)
    assert st["cost_wasted"] == pytest.approx(0.5) and st["cached"] == 1
    assert st["tokens"]["prompt"] == 21


def test_by_tag_and_latency():
    items = [
        item("1", "a", 0, tags=["x"], lat=100),
        item("2", "b", 0, tags=["x", "y"], passed=0, lat=300),
    ]
    st = compute_stats(items, [], evaluator_keys=[], repeats=1)
    assert st["by_tag"]["x"] == {"pass_rate": 0.5, "cases": 2}
    assert st["by_tag"]["y"] == {"pass_rate": 0.0, "cases": 1}
    assert st["latency_ms"]["p50"] == 200
