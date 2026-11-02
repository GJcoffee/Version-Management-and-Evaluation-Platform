"""HiAgent 历史数据回填为远端口径（规范指标以远端为主）。

此前 HiAgent 轮次的 ``turns.latency_ms`` / ``ttft_ms`` 是本地测量（含传输与 Trace 补齐前的等待），新口径下规范用时是
HiAgent Trace 的运行时长（``usage.upstream_run_ms``），首 token 时间不适用。为避免新评测与历史基线混比，回填：

- ``turns``：``usage`` 里有 ``upstream_run_ms`` 的轮次，``latency_ms`` 改为该值、``ttft_ms`` 置空；没有 ``upstream_run_ms``
  但来自 HiAgent 适配器的轮次（会话 ``adapter_snapshot.binding.id == "hiagent"`` 且不是业务桥接，Trace 未取得），
  ``latency_ms`` / ``ttft_ms`` 置空。原本地值移到 ``usage.local_latency_ms`` / ``local_ttft_ms``（已有则不覆盖）；
  ``usage.trace_status`` 缺失时推断：有模型调用明细为 complete，否则 partial；未取得的标 unavailable 并写原因。
- ``eval_items``：受影响会话对应的 done / scoring 用例，按新汇总规则（任一轮缺失则用时为空）重算 ``latency_ms`` 与 ``usage``。
- ``eval_runs``：已存的统计快照在 ``stats`` 里打上 ``needs_recompute``，由服务启动对账用 ``compute_stats`` 重算并清除标记。

没有远端数据的 agent（配置型、普通代码型、业务桥接）完全不动。降级：把 ``usage.local_*`` 还原回 ``latency_ms`` / ``ttft_ms``，
并按本地值重算受影响用例的用时；评测统计快照不回退（旧版本没有对账，保留重算后的值）。
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

NOTE = "历史数据未取得 Trace"
TRACE_ORDER = ("pending", "unavailable", "partial", "complete")
CHUNK = 500


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _chunks(items: list[str]):
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


def _remote_turns(conn):
    """候选轮次：usage 里有远端用时，或来自 HiAgent 适配器（非业务桥接）的已结束轮次。"""
    return conn.execute(sa.text(
        "SELECT t.id, t.session_id, t.usage, t.latency_ms, t.ttft_ms,"
        " json_extract(s.adapter_snapshot, '$.binding.id'),"
        " json_extract(s.adapter_snapshot, '$.binding.config.business_bridge')"
        " FROM turns t JOIN sessions s ON s.id = t.session_id"
        " WHERE t.status != 'running' AND (instr(t.usage, 'upstream_run_ms') > 0"
        " OR json_extract(s.adapter_snapshot, '$.binding.id') = 'hiagent')"
    )).all()


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _backfilled(usage: dict, latency, ttft, binding_id, bridge):
    """返回 (usage, latency_ms, ttft_ms)；该轮不需要回填时返回 None。"""
    run_ms = usage.get("upstream_run_ms")
    if _is_number(run_ms):
        new_latency = int(run_ms)
    elif binding_id == "hiagent" and bridge is None and "local_latency_ms" not in usage \
            and usage.get("trace_status") != "pending":
        new_latency = None
    else:
        return None
    out = dict(usage)
    out.setdefault("local_latency_ms", latency)
    out.setdefault("local_ttft_ms", ttft)
    if new_latency is not None:
        out.setdefault("trace_status", "complete" if usage.get("llm_calls") else "partial")
    else:
        if out.get("trace_status") not in ("complete", "partial"):
            out["trace_status"] = "unavailable"
            out.setdefault("trace_note", NOTE)
    return out, new_latency, None


def _aggregate(turns, prev) -> tuple[dict, int | None]:
    """与 ``services.eval.aggregate_turns`` 同口径（迁移里不依赖应用代码）。"""
    usage = {
        k: sum(int(u.get(k, 0) or 0) for u, _ in turns)
        for k in ("prompt_tokens", "completion_tokens", "llm_calls", "tool_calls")
    }
    usage["turns"] = len(turns)
    statuses = {u.get("trace_status") for u, _ in turns}
    if remote := [s for s in TRACE_ORDER if s in statuses]:
        usage["trace_status"] = remote[0]
    for k in ("failed_attempts", "wasted_cost"):
        if k in prev:
            usage[k] = prev[k]
    latency = None if any(lat is None for _, lat in turns) else sum(lat for _, lat in turns)
    return usage, latency


def _refresh_items(conn, session_ids: list[str], *, restore_local: bool) -> set[str]:
    """重算这些会话对应的 done / scoring 用例；返回受影响的 run id。"""
    runs: set[str] = set()
    for chunk in _chunks(session_ids):
        marks = ",".join(f":s{i}" for i in range(len(chunk)))
        params = {f"s{i}": sid for i, sid in enumerate(chunk)}
        turns: dict[str, list] = {}
        for sid, usage, latency in conn.execute(sa.text(
            f"SELECT session_id, usage, latency_ms FROM turns WHERE session_id IN ({marks}) ORDER BY session_id, idx"
        ), params):
            turns.setdefault(sid, []).append((json.loads(usage), latency))
        items = conn.execute(sa.text(
            f"SELECT id, run_id, session_id, usage FROM eval_items"
            f" WHERE session_id IN ({marks}) AND status IN ('done', 'scoring')"
        ), params).all()
        for item_id, run_id, sid, usage in items:
            prev = json.loads(usage) if usage else {}
            if restore_local:  # 旧口径：缺失按 0 累加，用量字段不变
                new_usage, latency = prev, sum(int(lat or 0) for _, lat in turns[sid])
            else:
                new_usage, latency = _aggregate(turns[sid], prev)
            conn.execute(
                sa.text("UPDATE eval_items SET usage = :u, latency_ms = :l WHERE id = :i"),
                {"u": _dumps(new_usage), "l": latency, "i": item_id},
            )
            runs.add(run_id)
    return runs


def upgrade() -> None:
    conn = op.get_bind()
    changed_sessions: set[str] = set()
    for turn_id, session_id, usage, latency, ttft, binding_id, bridge in _remote_turns(conn):
        result = _backfilled(json.loads(usage), latency, ttft, binding_id, bridge)
        if result is None:
            continue
        new_usage, new_latency, new_ttft = result
        conn.execute(
            sa.text("UPDATE turns SET usage = :u, latency_ms = :l, ttft_ms = :t WHERE id = :i"),
            {"u": _dumps(new_usage), "l": new_latency, "t": new_ttft, "i": turn_id},
        )
        changed_sessions.add(session_id)
    runs = _refresh_items(conn, sorted(changed_sessions), restore_local=False)
    for chunk in _chunks(sorted(runs)):
        marks = ",".join(f":r{i}" for i in range(len(chunk)))
        conn.execute(
            sa.text(f"UPDATE eval_runs SET stats = json_set(stats, '$.needs_recompute', json('true'))"
                    f" WHERE stats IS NOT NULL AND id IN ({marks})"),
            {f"r{i}": rid for i, rid in enumerate(chunk)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    sessions: set[str] = set()
    rows = conn.execute(sa.text(
        "SELECT id, session_id, usage FROM turns WHERE instr(usage, 'local_latency_ms') > 0"
    )).all()
    for turn_id, session_id, usage in rows:
        u = json.loads(usage)
        conn.execute(
            sa.text("UPDATE turns SET latency_ms = :l, ttft_ms = :t WHERE id = :i"),
            {"l": u["local_latency_ms"], "t": u.get("local_ttft_ms"), "i": turn_id},
        )
        sessions.add(session_id)
    _refresh_items(conn, sorted(sessions), restore_local=True)
    conn.execute(sa.text(
        "UPDATE eval_runs SET stats = json_remove(stats, '$.needs_recompute')"
        " WHERE stats IS NOT NULL AND json_extract(stats, '$.needs_recompute') IS NOT NULL"
    ))
