"""评测对比的统计方法（详设 8.6、8.7），全部是纯函数。

- :func:`paired_bootstrap_ci`：对用例做配对 bootstrap（默认重采样 2000 次，固定随机种子，结果可复现）；
- :func:`sign_test_p`：双侧精确符号检验（二项分布，平局不计入）；
- :func:`classify_cases`：按 ``case_key`` 关联左右两侧的用例分数并分类
  （improved / regressed / unchanged / new / missing / unscored），任意一侧重复间标准差大于 0.3 时标记 flaky；
- :func:`pass_at_k`：无偏的 pass@k 估计；
- :func:`percentile`：线性插值分位数。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

DEFAULT_SEED = 20261026
DEFAULT_RESAMPLES = 2000
DEFAULT_THRESHOLD = 0.05
FLAKY_STD = 0.3
_CHUNK = 200  # 每批重采样的次数，控制内存（用例很多时 resamples × cases 的矩阵会很大）

CaseClass = Literal["improved", "regressed", "unchanged", "new", "missing", "unscored"]


def mean(values: Sequence[float]) -> float | None:
    """算术平均；空序列返回 None。"""
    return float(np.mean(values)) if len(values) else None


def pstd(values: Sequence[float]) -> float:
    """总体标准差（ddof=0）；少于 2 个值时为 0。"""
    return float(np.std(values)) if len(values) > 1 else 0.0


def percentile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位数（q 取 0–100）；空序列返回 None。"""
    return float(np.percentile(values, q)) if len(values) else None


def paired_bootstrap_ci(
    deltas: Sequence[float],
    *,
    n_resamples: int = DEFAULT_RESAMPLES,
    confidence: float = 0.95,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float] | None:
    """配对 bootstrap 置信区间：对"逐用例差值"有放回重采样，取均值分布的百分位区间。

    差值序列为空返回 None；只有一个用例时区间退化为该差值。种子固定，所以同样的输入总是得到同样的结果。
    """
    arr = np.asarray(deltas, dtype=float)
    n = arr.size
    if n == 0:
        return None
    if n == 1:
        return float(arr[0]), float(arr[0])
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples, dtype=float)
    done = 0
    while done < n_resamples:
        k = min(_CHUNK, n_resamples - done)
        idx = rng.integers(0, n, size=(k, n))
        means[done : done + k] = arr[idx].mean(axis=1)
        done += k
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.percentile(means, [alpha * 100, (1.0 - alpha) * 100])
    return float(lo), float(hi)


def sign_test_p(wins: int, losses: int) -> float:
    """双侧精确符号检验的 p 值（H0：胜负概率各 1/2；平局不参与）。没有非平局样本时返回 1.0。"""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2 * tail / (1 << n))


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """无偏 pass@k：n 次尝试中 c 次通过，任取 k 次至少一次通过的概率。n < k 时返回 None。"""
    if k < 1 or n < k:
        return None
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def classify_delta(delta: float, threshold: float) -> Literal["improved", "regressed", "unchanged"]:
    """``|Δ| < threshold`` 或 Δ 为 0 时为 unchanged；Δ > 0 为 improved；Δ < 0 为 regressed。"""
    if delta == 0 or abs(delta) < threshold:
        return "unchanged"
    return "improved" if delta > 0 else "regressed"


@dataclass(frozen=True)
class CaseDelta:
    """一个用例在左右两侧的对比结果。分数为各重复的均值（None 表示该侧没有可用分数）。"""

    case_key: str
    cls: CaseClass
    left: float | None
    right: float | None
    delta: float | None
    flaky: bool


def classify_cases(
    left: Mapping[str, Sequence[float]],
    right: Mapping[str, Sequence[float]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[CaseDelta]:
    """按 case_key 关联并分类。

    ``left`` / ``right``：``{case_key: [各重复的分数]}``；只在右侧出现 -> new，只在左侧出现 -> missing；
    两侧都出现但任意一侧没有可用分数（列表为空）-> unscored。
    """
    out: list[CaseDelta] = []
    for key in sorted(set(left) | set(right)):
        ls, rs = list(left.get(key, ())), list(right.get(key, ()))
        flaky = pstd(ls) > FLAKY_STD or pstd(rs) > FLAKY_STD
        if key not in left:
            out.append(CaseDelta(key, "new", None, mean(rs), None, flaky))
        elif key not in right:
            out.append(CaseDelta(key, "missing", mean(ls), None, None, flaky))
        elif not ls or not rs:
            out.append(CaseDelta(key, "unscored", mean(ls), mean(rs), None, flaky))
        else:
            lm, rm = float(np.mean(ls)), float(np.mean(rs))
            d = rm - lm
            out.append(CaseDelta(key, classify_delta(d, threshold), lm, rm, d, flaky))
    return out


def summarize_deltas(
    cases: Sequence[CaseDelta], *, n_resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED
) -> dict:
    """整体结论：计数、平均 Δ 与 95% 置信区间、胜/平/负与符号检验 p 值。

    只统计两侧都有分数的用例（improved / regressed / unchanged）。胜 = improved，负 = regressed，
    平 = unchanged（即按阈值判定，与用例分类一致）。
    """
    counts: dict[str, int] = {
        c: 0 for c in ("improved", "regressed", "unchanged", "new", "missing", "unscored")
    }
    for c in cases:
        counts[c.cls] += 1
    paired = [c.delta for c in cases if c.delta is not None]
    ci = paired_bootstrap_ci(paired, n_resamples=n_resamples, seed=seed)
    wins, losses, ties = counts["improved"], counts["regressed"], counts["unchanged"]
    return {
        "cases": len(cases),
        "paired": len(paired),
        **counts,
        "flaky": sum(1 for c in cases if c.flaky),
        "mean_delta": mean(paired),
        "ci95": [ci[0], ci[1]] if ci else None,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "sign_test_p": sign_test_p(wins, losses),
    }
