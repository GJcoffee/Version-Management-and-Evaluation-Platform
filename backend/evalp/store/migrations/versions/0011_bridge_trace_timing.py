"""从业务 HTTP 桥接的真实 Trace 日志回填历史轮次和评测用时，原事件保持不变。"""

import json
import math

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SOURCE = "actual_upstream_trace_timestamps"
ORDER = ("pending", "unavailable", "partial", "complete")


def _duration(value):
    if isinstance(value, int | float) and not isinstance(value, bool) \
            and value >= 0 and math.isfinite(value):
        return int(value)
    return None


def _refresh(conn, sessions):
    runs = set()
    for sid in sorted(sessions):
        turns = [(json.loads(u), lat) for u, lat in conn.execute(sa.text(
            "SELECT usage, latency_ms FROM turns WHERE session_id=:s ORDER BY idx"
        ), {"s": sid})]
        for iid, rid, previous in conn.execute(sa.text(
            "SELECT id, run_id, usage FROM eval_items"
            " WHERE session_id=:s AND status IN ('done','scoring')"
        ), {"s": sid}).all():
            usage = json.loads(previous or "{}")
            usage.update({
                k: sum(int(u.get(k, 0) or 0) for u, _ in turns)
                for k in ("prompt_tokens", "completion_tokens", "llm_calls", "tool_calls")
            })
            usage["turns"] = len(turns)
            statuses = {u.get("trace_status") for u, _ in turns}
            usage.pop("trace_status", None)
            if remote := [s for s in ORDER if s in statuses]:
                usage["trace_status"] = remote[0]
            latency = None if any(lat is None for _, lat in turns) else sum(lat for _, lat in turns)
            conn.execute(sa.text("UPDATE eval_items SET usage=:u, latency_ms=:l WHERE id=:i"),
                         {"u": json.dumps(usage, ensure_ascii=False), "l": latency, "i": iid})
            runs.add(rid)
    for rid in sorted(runs):
        conn.execute(sa.text(
            "UPDATE eval_runs SET stats=json_set(stats,'$.needs_recompute',json('true'))"
            " WHERE id=:r AND stats IS NOT NULL"
        ), {"r": rid})


def upgrade() -> None:
    conn = op.get_bind()
    timings = {}
    for tid, raw in conn.execute(sa.text(
        "SELECT e.turn_id, e.payload FROM events e JOIN turns t ON t.id=e.turn_id"
        " WHERE t.status!='running' AND e.type='log'"
        " AND json_extract(e.payload,'$.metric_source')=:source ORDER BY e.seq"
    ), {"source": SOURCE}):
        p = json.loads(raw)
        if _duration(p.get("upstream_run_ms")) is not None:
            timings[tid] = p  # 最后一个有效完整 Trace；坏值不能覆盖已取得的值。
    changed = set()
    for tid, p in timings.items():
        sid, raw, latency, ttft = conn.execute(sa.text(
            "SELECT session_id, usage, latency_ms, ttft_ms FROM turns WHERE id=:i"
        ), {"i": tid}).one()
        usage = json.loads(raw or "{}")
        if usage.get("source") == "hiagent_trace":
            continue
        usage["timing_backfill_previous"] = dict(usage)
        usage["timing_backfill_previous"].update(_latency_ms=latency, _ttft_ms=ttft)
        usage.setdefault("local_latency_ms", latency)
        usage.setdefault("local_ttft_ms", ttft)
        usage.update(source="hiagent_trace", metric_source=SOURCE, scope="turn",
                     trace_status="complete", timing_backfill="0011")
        usage.pop("trace_note", None)
        for key in ("upstream_run_ms", "llm_api_ms", "tool_execution_ms"):
            if (value := _duration(p.get(key))) is not None:
                usage[key] = value
        conn.execute(sa.text("UPDATE turns SET usage=:u, latency_ms=:l, ttft_ms=NULL WHERE id=:i"),
                     {"u": json.dumps(usage, ensure_ascii=False), "l": usage["upstream_run_ms"], "i": tid})
        changed.add(sid)
    _refresh(conn, changed)


def downgrade() -> None:
    conn = op.get_bind()
    changed = set()
    for tid, sid, raw in conn.execute(sa.text(
        "SELECT id,session_id,usage FROM turns WHERE json_extract(usage,'$.timing_backfill')='0011'"
    )).all():
        usage = json.loads(raw)
        usage = usage["timing_backfill_previous"]
        latency, ttft = usage.pop("_latency_ms"), usage.pop("_ttft_ms")
        conn.execute(sa.text("UPDATE turns SET usage=:u,latency_ms=:l,ttft_ms=:t WHERE id=:i"),
                     {"u": json.dumps(usage, ensure_ascii=False), "l": latency, "t": ttft, "i": tid})
        changed.add(sid)
    _refresh(conn, changed)
