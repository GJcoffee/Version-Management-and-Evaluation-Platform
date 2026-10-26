"""评测汇总指标（详设 8.6）的纯计算部分：输入是 Item / 评分的行数据，输出 ``eval_runs.stats``。

口径（详设之外的取舍，已记入 DEVIATIONS.md）：

- **用例通过率** = 通过的重复次数 / 参与判定的重复次数。执行失败的 Item（``failed``）算未通过；
  ``done`` 但没有任何可用的门禁评分（评审解析失败、人工尚未打分等）的 Item 通过与否未知，不参与判定；
  已取消 / 未完成的 Item 同样不参与。``pass_rate`` 是各用例通过率的平均值。
- **pass@k** 用无偏估计，只在 ``repeats >= k`` 时计算（k 取 1、3、5、10）。
- **Item 分数**（对比用）= 该 Item 全部门禁评估器 score 的平均值；执行失败的 Item 记 0 分。
- **按工具指标**（``tools``）：来自 ``tool.call`` / ``tool.result`` 事件（用 ``call_id`` 配对；耗时取
  ``tool.result.latency_ms``，失败取 ``tool.result.is_error``）；"每用例"指每个已结束（done / failed）的
  Item。
- ``cost`` 是各 Item 的"名义成本"之和（缓存命中的 Item 沿用来源 Item 的成本）；``cost_new``
  是本次评测真正新增的成本
  （不含缓存命中），预算按它计算；``cost_wasted`` 是失败尝试的成本（记在 Item 的 ``usage.failed_attempts``
  里）。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalp.compare.stats import mean, pass_at_k, percentile, pstd

PASS_AT_K = (1, 3, 5, 10)
FINISHED = ("done", "failed")


@dataclass
class ItemRow:
    """统计所需的 Item 字段（已 join 用例）。"""

    id: str
    case_id: str
    case_key: str
    repeat_idx: int
    status: str
    tags: list[str] = field(default_factory=list)
    passed: int | None = None
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None
    cost: float | None = None
    cached: bool = False


@dataclass
class ScoreRow:
    """统计所需的评分字段。"""

    item_id: str
    key: str
    status: str
    score: float | None = None
    passed: int | None = None
    detail: dict[str, Any] | None = None


@dataclass
class ToolCallRow:
    """一次工具调用：``tool.call`` 与同 ``call_id`` 的 ``tool.result`` 合并后的结果。"""

    item_id: str
    name: str
    latency_ms: int | None = None
    is_error: bool = False


def tool_call_rows(item_id: str, events: Iterable[tuple[str, dict[str, Any]]]) -> list[ToolCallRow]:
    """一个 Item 的 ``(事件类型, payload)`` 序列 -> 工具调用列表（耗时与是否失败取自 ``tool.result``）。"""
    events = list(events)
    results = {p["call_id"]: p for t, p in events if t == "tool.result"}
    rows: list[ToolCallRow] = []
    for t, p in events:
        if t == "tool.call":
            r = results.get(p["call_id"], {})
            rows.append(ToolCallRow(item_id, p["name"], r.get("latency_ms"), bool(r.get("is_error"))))
    return rows


def item_outcome(item: ItemRow) -> int | None:
    """1 通过 / 0 未通过 / None 不参与判定。"""
    if item.status == "failed":
        return 0
    if item.status == "done" and item.passed is not None:
        return int(bool(item.passed))
    return None


def compute_passed(gate_keys: Iterable[str], scores: Iterable[ScoreRow]) -> int | None:
    """Item 是否通过：所有已出分的门禁评估器都通过；没有任何可用的门禁评分时为 None。"""
    gates = set(gate_keys)
    done = [s for s in scores if s.key in gates and s.status == "done" and s.passed is not None]
    if not done:
        return None
    return int(all(s.passed for s in done))


def item_score(item: ItemRow, scores: Sequence[ScoreRow], gate_keys: Iterable[str]) -> float | None:
    """Item 的分数（门禁评估器 score 的平均值；失败的 Item 为 0；没有可用分数为 None）。"""
    if item.status == "failed":
        return 0.0
    if item.status != "done":
        return None
    gates = set(gate_keys)
    vals = [s.score for s in scores if s.key in gates and s.status == "done" and s.score is not None]
    return mean(vals)


def case_scores(
    items: Sequence[ItemRow], scores: Sequence[ScoreRow], gate_keys: Iterable[str]
) -> dict[str, list[float]]:
    """``{case_key: [各重复的分数]}``（只含已完成的 Item；没有可用分数的重复被略过，但用例键仍保留）。"""
    by_item: dict[str, list[ScoreRow]] = defaultdict(list)
    for s in scores:
        by_item[s.item_id].append(s)
    gates = list(gate_keys)
    out: dict[str, list[float]] = {}
    for it in items:
        if it.status not in FINISHED:
            continue
        lst = out.setdefault(it.case_key, [])
        v = item_score(it, by_item.get(it.id, ()), gates)
        if v is not None:
            lst.append(v)
    return out


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int, int]:
    u = usage or {}
    return int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0)


def compute_tool_stats(items: Sequence[ItemRow], calls: Sequence[ToolCallRow]) -> dict[str, Any]:
    """按工具名聚合：调用次数、平均每用例（每个已结束的 Item）调用次数、耗时 p50/p95、失败率。

    按调用次数从多到少排列；耗时只统计 ``tool.result`` 带 ``latency_ms`` 的调用。
    """
    finished = {it.id for it in items if it.status in FINISHED}
    by_name: dict[str, list[ToolCallRow]] = defaultdict(list)
    for c in calls:
        if c.item_id in finished:
            by_name[c.name].append(c)
    out: dict[str, Any] = {}
    for name, rows in sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lat = [c.latency_ms for c in rows if c.latency_ms is not None]
        errors = sum(1 for c in rows if c.is_error)
        out[name] = {
            "calls": len(rows),
            "calls_per_case": len(rows) / len(finished),
            "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95)},
            "errors": errors,
            "error_rate": errors / len(rows),
        }
    return out


def compute_stats(
    items: Sequence[ItemRow],
    scores: Sequence[ScoreRow],
    *,
    evaluator_keys: Sequence[str],
    repeats: int,
    tool_calls: Sequence[ToolCallRow] = (),
) -> dict[str, Any]:
    """汇总指标（详设 8.6）。"""
    by_status: dict[str, int] = defaultdict(int)
    for it in items:
        by_status[it.status] += 1
    done_items = [it for it in items if it.status == "done"]
    finished = by_status["done"] + by_status["failed"]

    # 按用例聚合通过情况
    outcomes: dict[str, list[int]] = defaultdict(list)
    case_tags: dict[str, list[str]] = {}
    for it in items:
        case_tags.setdefault(it.case_id, it.tags)
        o = item_outcome(it)
        if o is not None:
            outcomes[it.case_id].append(o)
    rates = {cid: sum(v) / len(v) for cid, v in outcomes.items() if v}
    pass_at: dict[str, float] = {}
    for k in PASS_AT_K:
        if k > repeats:
            continue
        vals = [v for cid, o in outcomes.items() if (v := pass_at_k(len(o), sum(o), k)) is not None]
        m = mean(vals)
        if m is not None:
            pass_at[str(k)] = m

    by_tag: dict[str, list[float]] = defaultdict(list)
    for cid, r in rates.items():
        for t in case_tags.get(cid, ()):
            by_tag[t].append(r)

    # 评估器
    by_key: dict[str, list[ScoreRow]] = defaultdict(list)
    done_ids = {it.id for it in done_items}
    for s in scores:
        if s.item_id in done_ids:
            by_key[s.key].append(s)
    ev_stats: dict[str, Any] = {}
    for key in evaluator_keys:
        rows = by_key.get(key, [])
        vals = [s.score for s in rows if s.status == "done" and s.score is not None]
        passed = [s.passed for s in rows if s.status == "done" and s.passed is not None]
        ev_stats[key] = {
            "mean": mean(vals),
            "std": pstd(vals) if vals else None,
            "pass_rate": (sum(1 for p in passed if p) / len(passed)) if passed else None,
            "n": len(vals),
            "errors": sum(1 for s in rows if s.status == "error"),
            "parse_failures": sum(
                1 for s in rows if s.status == "error" and (s.detail or {}).get("parse_failed")
            ),
            "pending_human": sum(1 for s in rows if s.status == "pending_human"),
        }
    all_rows = [s for rows in by_key.values() for s in rows]
    score_errors = sum(1 for s in all_rows if s.status == "error")

    lat = [it.latency_ms for it in done_items if it.latency_ms is not None]
    prompt = sum(_usage_tokens(it.usage)[0] for it in done_items)
    completion = sum(_usage_tokens(it.usage)[1] for it in done_items)
    wasted = sum(float((it.usage or {}).get("wasted_cost", 0) or 0) for it in items)
    cost = sum(it.cost or 0.0 for it in done_items)
    cost_new = sum(it.cost or 0.0 for it in done_items if not it.cached)

    return {
        "cases": len({it.case_id for it in items}),
        "items": len(items),
        "done": by_status["done"],
        "failed": by_status["failed"],
        "cancelled": by_status["cancelled"],
        "cached": sum(1 for it in items if it.cached),
        "pass_rate": mean(list(rates.values())),
        "pass_at_k": pass_at,
        "flaky_cases": sum(1 for r in rates.values() if 0 < r < 1),
        "evaluators": ev_stats,
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95)},
        "tokens": {"prompt": prompt, "completion": completion},
        "cost": round(cost, 8),
        "cost_new": round(cost_new, 8),
        "cost_wasted": round(wasted, 8),
        "error_rate": (by_status["failed"] / finished) if finished else 0.0,
        "tools": compute_tool_stats(items, tool_calls),
        "score_error_rate": score_errors / len(all_rows) if all_rows else 0.0,
        "by_tag": {t: {"pass_rate": mean(v), "cases": len(v)} for t, v in sorted(by_tag.items())},
    }
