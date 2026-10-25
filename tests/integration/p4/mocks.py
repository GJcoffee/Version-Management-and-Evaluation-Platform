"""按 docs/contract-eval.md 构造的评测接口返回值，供 CLI / MCP 测试用 respx 回放。"""

from __future__ import annotations

from typing import Any


def stats(pass_rate: float = 0.5) -> dict[str, Any]:
    return {
        "cases": 4, "items": 4, "done": 4, "failed": 0, "cached": 0, "pass_rate": pass_rate,
        "evaluators": {"kw": {"mean": 0.6, "pass_rate": pass_rate, "errors": 0}},
        "latency_ms": {"p50": 1200, "p95": 3000}, "tokens": {"prompt": 1000, "completion": 300},
        "cost": 0.0123, "error_rate": 0.0,
    }  # fmt: skip


def run(
    rid: str, seq: int, status: str = "completed", pass_rate: float = 0.5, done: int = 4
) -> dict[str, Any]:
    return {
        "id": rid, "batch_id": "evb_1", "name": "回归", "agent_id": "agt_1", "agent_name": "support",
        "version_id": f"ver_{seq}", "version_seq": seq, "dataset_rev_id": "arv_1",
        "dataset_name": "support-regression", "dataset_rev": 1, "evaluators": [], "repeats": 1,
        "concurrency": 8, "cache_policy": "reuse", "status": status,
        "progress": {"total": 4, "done": done, "failed": 0, "running": 4 - done, "cached": 0},
        "stats": stats(pass_rate) if status == "completed" else None,
        "created_at": "2026-10-25T08:00:00.000Z", "started_at": None, "finished_at": None,
    }  # fmt: skip


def item(key: str, passed: bool, score: float, output: str = "抱歉，我不清楚。") -> dict[str, Any]:
    return {
        "id": f"evi_{key}", "case_id": f"cas_{key}", "case_key": key, "repeat_idx": 0,
        "input_preview": f"{key} 的问题", "expected": "7 天", "tags": ["p0"], "status": "done",
        "cached": False, "session_id": f"ses_{key}", "passed": passed, "output_preview": output,
        "scores": [{"evaluator_key": "kw", "status": "done", "score": score, "raw_score": score,
                    "passed": passed, "reason": "回复里没有提到 7 天" if not passed else "命中关键词"}],
        "latency_ms": 1000, "usage": {}, "cost": 0.001, "error": None, "attempts": 1,
    }  # fmt: skip


def compare() -> dict[str, Any]:
    return {
        "left": {"id": "evr_a", "version_seq": 1, "stats": stats(0.75)},
        "right": {"id": "evr_b", "version_seq": 2, "stats": stats(0.5)},
        "warnings": ["两次评测的数据集修订不同（rev 1 vs rev 2）"],
        "summary": {
            "improved": 1, "regressed": 2, "unchanged": 1, "new": 0, "missing": 0, "mean_delta": -0.25,
            "metric_deltas": {"pass_rate": -0.25, "latency_p50_ms": 120, "cost": 0.004, "tokens_total": 500},
        },
        "cases": {
            "items": [
                {"case_key": "refund-001", "class": "regressed", "delta": -0.67, "tags": ["p0"],
                 "left": {"score": 1.0, "pass_rate": 1.0, "item_ids": ["evi_l1"]},
                 "right": {"score": 0.33, "pass_rate": 0.33, "item_ids": ["evi_r1"]}},
                {"case_key": "hours-001", "class": "regressed", "delta": -0.3, "tags": [],
                 "left": {"score": 1.0, "pass_rate": 1.0, "item_ids": []},
                 "right": {"score": 0.7, "pass_rate": 1.0, "item_ids": []}},
                {"case_key": "faq-001", "class": "improved", "delta": 0.5, "tags": [],
                 "left": {"score": 0.5, "pass_rate": 0.5, "item_ids": []},
                 "right": {"score": 1.0, "pass_rate": 1.0, "item_ids": []}},
            ],
            "next_cursor": None,
        },
    }  # fmt: skip


VERSION_DIFF = {
    "left": {"id": "ver_1", "seq": 1, "spec_hash": "a" * 64},
    "right": {"id": "ver_2", "seq": 2, "spec_hash": "b" * 64},
    "changes": [
        {"path": "model.params.temperature", "op": "replace", "left": 0.3, "right": 0.7},
        {"path": "prompts.system", "op": "asset_changed", "left": {"rev": 1}, "right": {"rev": 2},
         "content_diff": {"format": "unified", "text": "@@ -1,2 +1,2 @@\n 你是客服\n-简洁回答\n+详细回答"}},
        {"path": "tools[extra]", "op": "add", "right": {"kind": "tool", "name": "extra", "rev": 1}},
    ],
    "summary": {"add": 1, "remove": 0, "replace": 1, "asset_changed": 1},
}  # fmt: skip

EVALUATORS_YAML = "- key: kw\n  type: contains\n  values: [退货]\n"
