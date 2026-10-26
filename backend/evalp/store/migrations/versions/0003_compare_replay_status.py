"""compare_groups.replay_status / replay_turns / replay_error（对比与重放）

重放状态与所选轮次从 ``notes``（JSON）迁到正式列；``notes`` 回归"用户备注"。
旧数据：``replay_turns`` 取自 notes 里的 ``{"replay_turns": [...]}``；``replay_status`` 按已有轮次推断
（所选轮次在新会话里都已有非 running 的轮次为 completed，否则为 failed——迁移时不可能还有后台重放在跑）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 10:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_INTERRUPTED = "重放未完成（升级时按已有轮次推断）"


def _legacy_turns(notes: str | None) -> tuple[list[int] | None, str | None]:
    """旧 notes -> (所选轮次, 迁移后应保留的备注)。notes 只有 replay_turns 这一项时，备注清空。"""
    if not notes:
        return None, None
    try:
        data = json.loads(notes)
    except ValueError:
        return None, notes
    if isinstance(data, dict) and "replay_turns" in data:
        raw = data.get("replay_turns")
        turns = sorted({int(x) for x in raw if isinstance(x, int)}) if isinstance(raw, list) else None
        rest = {k: v for k, v in data.items() if k != "replay_turns"}
        return turns, (json.dumps(rest, ensure_ascii=False) if rest else None)
    return None, notes


def upgrade() -> None:
    with op.batch_alter_table("compare_groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("replay_status", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("replay_turns", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("replay_error", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_compare_groups_replay_status"),
            "replay_status IS NULL OR replay_status IN ('running','completed','failed')",
        )

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, right_ref, notes FROM compare_groups WHERE mode = 'replay'")).all()
    for cid, right_ref, notes in rows:
        turns, keep = _legacy_turns(notes)
        have = {
            r[0]: r[1]
            for r in bind.execute(
                sa.text("SELECT pair_index, status FROM turns WHERE session_id = :s AND pair_index IS NOT NULL"),
                {"s": right_ref},
            )
        }
        wanted = turns if turns is not None else sorted(have)
        done = bool(wanted) and all(p in have and have[p] != "running" for p in wanted)
        bind.execute(
            sa.text(
                "UPDATE compare_groups SET replay_status = :st, replay_turns = :t, replay_error = :e, "
                "notes = :n WHERE id = :id"
            ),
            {
                "st": "completed" if done else "failed",
                "t": json.dumps(wanted, ensure_ascii=False),
                "e": None if done else _INTERRUPTED,
                "n": keep,
                "id": cid,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, replay_turns FROM compare_groups WHERE mode = 'replay' AND replay_turns IS NOT NULL")
    ).all()
    for cid, turns in rows:  # 还原成 0002 的用法：把所选轮次写回 notes（会覆盖用户备注）
        bind.execute(
            sa.text("UPDATE compare_groups SET notes = :n WHERE id = :id"),
            {"n": json.dumps({"replay_turns": json.loads(turns)}, ensure_ascii=False), "id": cid},
        )
    with op.batch_alter_table("compare_groups", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_compare_groups_replay_status"), type_="check")
        batch_op.drop_column("replay_error")
        batch_op.drop_column("replay_turns")
        batch_op.drop_column("replay_status")
