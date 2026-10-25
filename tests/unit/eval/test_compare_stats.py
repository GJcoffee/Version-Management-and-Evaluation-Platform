"""compare/stats.py：配对 bootstrap、符号检验、pass@k、用例分类（纯函数）。"""

import math

import pytest
from evalp.compare.stats import (
    CaseDelta,
    classify_cases,
    classify_delta,
    mean,
    paired_bootstrap_ci,
    pass_at_k,
    percentile,
    pstd,
    sign_test_p,
    summarize_deltas,
)


def test_basic_helpers():
    assert mean([]) is None and mean([1, 2, 3]) == 2.0
    assert pstd([1]) == 0.0 and pstd([0, 1]) == pytest.approx(0.5)
    assert percentile([], 50) is None
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    deltas = [0.1, 0.2, -0.05, 0.3, 0.15, 0.0, 0.25, 0.1, 0.05, 0.2]
    a = paired_bootstrap_ci(deltas)
    b = paired_bootstrap_ci(deltas)
    assert a == b  # 固定种子：结果可复现
    lo, hi = a
    assert lo < mean(deltas) < hi
    assert paired_bootstrap_ci(deltas, seed=1) != a  # 换种子才会变


def test_bootstrap_edge_cases():
    assert paired_bootstrap_ci([]) is None
    assert paired_bootstrap_ci([0.4]) == (0.4, 0.4)
    lo, hi = paired_bootstrap_ci([0.2] * 30)
    assert lo == pytest.approx(0.2) and hi == pytest.approx(0.2)


def test_bootstrap_excludes_zero_for_clear_improvement():
    lo, hi = paired_bootstrap_ci([0.5, 0.4, 0.6, 0.5, 0.45, 0.55] * 5)
    assert lo > 0
    lo, hi = paired_bootstrap_ci([0.1, -0.1, 0.2, -0.2, 0.05, -0.05] * 5)
    assert lo < 0 < hi


def test_bootstrap_handles_many_cases_in_chunks():
    ci = paired_bootstrap_ci([0.01 * (i % 7 - 3) for i in range(1000)], n_resamples=2000)
    assert ci is not None and ci[0] < ci[1]


def test_sign_test_exact_values():
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 5) == 1.0
    assert sign_test_p(10, 0) == pytest.approx(2 / 1024)
    assert sign_test_p(0, 10) == pytest.approx(2 / 1024)
    # n=8, k=2：2 * (1+8+28) / 256
    assert sign_test_p(2, 6) == pytest.approx(2 * 37 / 256)
    assert sign_test_p(1, 0) == 1.0


def test_pass_at_k():
    assert pass_at_k(3, 0, 1) == 0.0
    assert pass_at_k(3, 3, 1) == 1.0
    assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
    assert pass_at_k(5, 2, 3) == pytest.approx(1 - math.comb(3, 3) / math.comb(5, 3))
    assert pass_at_k(5, 3, 3) == 1.0  # n - c < k：必然至少一次通过
    assert pass_at_k(2, 1, 3) is None  # 重复次数不足 k


def test_classify_delta_threshold():
    assert classify_delta(0.05, 0.05) == "improved"
    assert classify_delta(0.049, 0.05) == "unchanged"
    assert classify_delta(-0.05, 0.05) == "regressed"
    assert classify_delta(0.0, 0.0) == "unchanged"


def test_classify_cases_all_classes():
    left = {"a": [0.0, 0.0], "b": [1.0], "c": [0.5], "gone": [1.0], "u": []}
    right = {"a": [1.0, 1.0], "b": [0.0], "c": [0.52], "fresh": [1.0], "u": [1.0]}
    out = {c.case_key: c for c in classify_cases(left, right, 0.05)}
    assert out["a"].cls == "improved" and out["a"].delta == 1.0
    assert out["b"].cls == "regressed" and out["b"].delta == -1.0
    assert out["c"].cls == "unchanged" and out["c"].delta == pytest.approx(0.02)
    assert out["gone"].cls == "missing" and out["gone"].left == 1.0 and out["gone"].delta is None
    assert out["fresh"].cls == "new" and out["fresh"].right == 1.0
    assert out["u"].cls == "unscored" and out["u"].delta is None


def test_classify_cases_flaky_flag():
    out = {
        c.case_key: c
        for c in classify_cases({"x": [0.0, 1.0, 0.0, 1.0], "y": [1.0, 1.0]}, {"x": [1.0], "y": [1.0]})
    }
    assert out["x"].flaky and not out["y"].flaky  # 重复间标准差 0.5 > 0.3
    # 阈值之内的波动不算 flaky
    assert not classify_cases({"z": [0.7, 0.9]}, {"z": [0.8]})[0].flaky


def test_summarize_deltas():
    cases = [
        CaseDelta("a", "improved", 0.0, 1.0, 1.0, False),
        CaseDelta("b", "improved", 0.0, 1.0, 1.0, False),
        CaseDelta("c", "regressed", 1.0, 0.0, -1.0, True),
        CaseDelta("d", "unchanged", 1.0, 1.0, 0.0, False),
        CaseDelta("e", "new", None, 1.0, None, False),
    ]
    s = summarize_deltas(cases)
    assert s["cases"] == 5 and s["paired"] == 4
    assert (s["improved"], s["regressed"], s["unchanged"], s["new"]) == (2, 1, 1, 1)
    assert s["flaky"] == 1
    assert s["mean_delta"] == pytest.approx(0.25)
    assert (s["wins"], s["ties"], s["losses"]) == (2, 1, 1)
    assert s["sign_test_p"] == pytest.approx(sign_test_p(2, 1))
    assert s["ci95"][0] <= s["mean_delta"] <= s["ci95"][1]


def test_summarize_empty():
    s = summarize_deltas([])
    assert s["mean_delta"] is None and s["ci95"] is None and s["sign_test_p"] == 1.0
