# 评测与对比接口契约（一期收窄版，总控定义）

评测后端、对比后端、MCP/CLI、前端都按本文件开发。实现者可以**增加**字段，但不能改名或删除下面的字段。实现完成后，评测和对比的后端开发者把最终文档补进 `docs/api.md`。

## 1. 评估器配置（创建评测时传入）

```json
{"key": "exact", "type": "exact_match", "gate": true, "normalize": ["strip", "lower"]}
{"key": "kw",    "type": "contains", "values": ["退货"], "mode": "all", "negate": false}
{"key": "re",    "type": "regex", "pattern": "订单\\s*A\\d+", "flags": "i"}
{"key": "judge", "type": "llm_judge", "provider": "deepseek", "model": "deepseek-chat",
 "prompt": "asset://prompt/judge-default@latest", "scale": [1, 5], "pass_threshold": 4}
```

- `key` 在同一次评测中唯一；`gate` 默认 true（参与 item 是否通过的判定）；
- `exact_match` 的 `values` 为空时与用例的 `expected` 比较；`contains` 的 `values` 为空时使用 `expected`；
- 所有评估器输出 `score ∈ [0,1]`、`passed`、`reason`；llm_judge 的原始分数按 scale 归一化。

## 2. REST

### POST /api/v1/evals → 201

```json
{
  "name": "v13 回归",
  "version_ids": ["ver_…", "customer-support:v12", "customer-support:baseline"],
  "dataset": "asset://dataset/cs-regression@latest",
  "case_filter": {"tags": ["p0"], "case_keys": []},
  "evaluators": [ … ],
  "repeats": 1,
  "concurrency": 8,
  "seed": null,
  "cache_policy": "reuse"
}
```

`version_ids` 中的每一项可以是版本 id，也可以是 `agent:vN` 或 `agent:标签`。`dataset` 可以是引用，也可以是修订 id（`arv_…`）。

```json
{"batch_id": "evb_…", "runs": [{"id": "evr_…", "version_id": "ver_…", "status": "queued"}]}
```

### GET /api/v1/evals?agent_id=&batch_id=&status=&limit=&cursor= → Page[EvalRun]

### GET /api/v1/evals/{id} → EvalRun

```json
{
  "id": "evr_…", "batch_id": "evb_…", "name": "v13 回归",
  "agent_id": "agt_…", "agent_name": "customer-support",
  "version_id": "ver_…", "version_seq": 13,
  "dataset_rev_id": "arv_…", "dataset_name": "cs-regression", "dataset_rev": 3,
  "evaluators": [ … ], "repeats": 1, "concurrency": 8, "cache_policy": "reuse",
  "status": "queued | running | completed | completed_with_errors | cancelled | failed",
  "progress": {"total": 600, "done": 420, "failed": 3, "running": 8, "cached": 120},
  "stats": null,
  "created_at": "…", "started_at": "…", "finished_at": null
}
```

`stats` 在 run 结束后填充：

```json
{
  "cases": 200, "items": 600, "done": 594, "failed": 6, "cached": 120,
  "pass_rate": 0.82,
  "evaluators": {"judge": {"mean": 0.78, "pass_rate": 0.8, "errors": 2}},
  "latency_ms": {"p50": 3200, "p95": 9100},
  "tokens": {"prompt": 1200000, "completion": 310000},
  "cost": 6.12,
  "error_rate": 0.01
}
```

- 用例通过率 = 通过的重复次数 / 重复次数；`pass_rate` 为各用例通过率的平均值；
- item 是否通过 = 所有 gate 评估器都通过，且执行成功。

### GET /api/v1/evals/{id}/items?status=&passed=&q=&limit=&cursor= → Page[EvalItem]

```json
{
  "id": "evi_…", "case_id": "cas_…", "case_key": "refund-001", "repeat_idx": 0,
  "input_preview": "我要退货…", "expected": "…", "tags": ["p0"],
  "status": "pending | running | scoring | done | failed | cancelled",
  "cached": false, "session_id": "ses_…", "passed": true,
  "output_preview": "您好，退货流程…",
  "scores": [{"evaluator_key": "judge", "status": "done | error", "score": 0.75, "raw_score": 4,
              "passed": true, "reason": "…"}],
  "latency_ms": 3100, "usage": {"prompt_tokens": 900, "completion_tokens": 300}, "cost": 0.01,
  "error": null, "attempts": 1
}
```

`q` 对 case_key、输入、输出做模糊匹配。`GET /evals/{id}/items/{item_id}` 返回单条，结构相同。

### POST /api/v1/evals/{id}/cancel → EvalRun

### GET /api/v1/evals/{id}/stream（SSE）

- `eval.progress`：`{"run_id", "progress": {...}}`，每秒最多 4 条；
- `eval.item_done`：`{"run_id", "item": EvalItem}`；
- `eval.status`：`{"run_id", "status", "stats"}`；
- run 已结束时，先发一条 `eval.status`，再发 `idle` 并关闭。

### GET /api/v1/evals/compare?left=evr_a&right=evr_b&threshold=0.05&class=&limit=&cursor=

```json
{
  "left":  {"id": "evr_a", "version_seq": 12, "stats": { … }},
  "right": {"id": "evr_b", "version_seq": 13, "stats": { … }},
  "warnings": ["两次评测的数据集修订不同（rev 3 vs rev 4）"],
  "summary": {
    "improved": 7, "regressed": 3, "unchanged": 180, "new": 0, "missing": 0,
    "mean_delta": 0.031,
    "metric_deltas": {"pass_rate": 0.02, "latency_p50_ms": -120, "cost": 0.4, "tokens_total": 5000}
  },
  "cases": {
    "items": [{"case_key": "refund-001", "class": "regressed", "delta": -0.67, "tags": ["p0"],
               "left":  {"score": 1.0, "pass_rate": 1.0, "item_ids": ["evi_…"]},
               "right": {"score": 0.33, "pass_rate": 0.33, "item_ids": ["evi_…"]}}],
    "next_cursor": null
  }
}
```

- 用例分数 = 各 gate 评估器分数的平均值，再对重复次数取平均；
- `|Δ| < threshold` 为 unchanged，否则按符号分为 improved / regressed；只在一侧出现的用例为 new / missing；
- 排序：regressed 在前，其次按 |Δ| 从大到小。

## 3. 对比与重放（对比后端实现）

### POST /api/v1/compare → 201 CompareGroup

```json
{"kind": "session", "mode": "live", "left": {"version_id": "ver_a"}, "right": {"version_id": "ver_b"}, "title": "…"}
{"kind": "session", "mode": "history", "left": {"session_id": "ses_x"}, "right": {"session_id": "ses_y"}}
```

`version_id` 同样可以写成 `agent:vN` 或 `agent:标签`。

```json
{"id": "cmp_…", "kind": "session", "mode": "live | history | replay",
 "left":  {"session_id": "ses_…", "version_id": "ver_…", "version_seq": 12, "agent_name": "…"},
 "right": {"session_id": "ses_…", "version_id": "ver_…", "version_seq": 13, "agent_name": "…"},
 "title": "…", "created_at": "…"}
```

### GET /api/v1/compare?limit=&cursor= → Page[CompareGroup]；GET /api/v1/compare/{id} → CompareGroup

### POST /api/v1/compare/{id}/turns `{"input": "…", "variables"?: {}}` → 202

```json
{"pair_index": 3, "left_turn_id": "trn_…", "right_turn_id": "trn_…"}
```

任意一侧会话忙时返回 409 SESSION_BUSY。前端分别订阅两侧会话的 SSE（`/sessions/{id}/stream`）。

### GET /api/v1/compare/{id}/alignment

```json
{
  "turns": [{
    "pair": 0, "left_turn_id": "trn_…", "right_turn_id": "trn_…",
    "steps": [
      {"op": "equal",  "left": "s1", "right": "s1", "sig": "llm"},
      {"op": "insert", "right": "s3", "sig": "tool:knowledge_search"},
      {"op": "delete", "left": "s3", "sig": "tool:query_order"}
    ],
    "tool_args_diff": [{"left": "s2", "right": "s2", "changes": [{"path": "order_id", "left": "A1", "right": "A2"}]}],
    "metrics": {
      "left":  {"latency_ms": 2100, "ttft_ms": 400, "tokens": 1830, "cost": 0.01, "llm_calls": 2, "tool_calls": 1},
      "right": {"latency_ms": 3400, "ttft_ms": 380, "tokens": 2410, "cost": 0.02, "llm_calls": 3, "tool_calls": 2}
    },
    "final_text": {"left": "…", "right": "…"}
  }]
}
```

- 轮次对齐：live 和 replay 模式按 pair_index，history 模式按 idx；只有一侧有的轮次，另一侧的 turn_id 为 null；
- 步骤对齐：对两侧的步骤签名序列做 LCS，只输出 equal / insert / delete 三种 op（签名不同的步骤拆成一个 delete 加一个 insert）；
- step_id 每轮都从 s1 开始编号，需要和 turn_id 组合才能唯一确定一个步骤。

### POST /api/v1/sessions/{id}/replay `{"target_version_id": "ver_b | agent:v13", "turns"?: [0, 2]}` → 201

```json
{"session_id": "ses_new", "compare_id": "cmp_…"}
```

- 只实现锚定模式：第 k 轮的上下文用原会话前 k 轮的历史（`SessionService.get_history(src, upto_idx=k)` + `history_override`），各轮相互独立，在后台并发执行（不经过调度器，用 asyncio 任务，并发上限 4）；
- 每一轮的 `anchored_from_turn_id` 指向原轮次，`pair_index` = 原轮次的 idx；
- 自动创建 `CompareGroup(kind=session, mode=replay, left=原会话, right=新会话)`。
