"""sessions.variables 与 turns.anchored_from_turn_id（P3-A 会话服务）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 19:55:27.484653
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("variables", sa.Text(), server_default=sa.text("'{}'"), nullable=False))

    with op.batch_alter_table("turns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("anchored_from_turn_id", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_turns_anchored_from_turn_id_turns"), "turns", ["anchored_from_turn_id"], ["id"]
        )
    _recreate_sessions_index()


def _recreate_sessions_index() -> None:
    """batch 重建 sessions 表会丢掉含 DESC 的表达式索引（反射不出方向），这里按 0001 的定义重建。"""
    op.execute("DROP INDEX IF EXISTS ix_sessions_agent_mode")
    op.execute("CREATE INDEX ix_sessions_agent_mode ON sessions (agent_id, mode, created_at DESC)")


def downgrade() -> None:
    with op.batch_alter_table("turns", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_turns_anchored_from_turn_id_turns"), type_="foreignkey")
        batch_op.drop_column("anchored_from_turn_id")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("variables")
    _recreate_sessions_index()
