"""assets.kind 新增 kb_config / skill / mcp / database / app_config（agent 快照按规则入库）

只放宽 CHECK 约束，不改数据。降级时这些类型的资产改回 file（同名冲突会让降级失败，需要人工处理）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27 10:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD_KINDS = "'prompt','tool','knowledge','dataset','file'"
NEW_KINDS = "'prompt','tool','knowledge','kb_config','skill','mcp','database','app_config','dataset','file'"


def _set_kinds(kinds: str) -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_assets_kind"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_assets_kind"), f"kind IN ({kinds})")


def upgrade() -> None:
    _set_kinds(NEW_KINDS)


def downgrade() -> None:
    op.execute(
        "UPDATE assets SET kind = 'file' WHERE kind IN ('kb_config','skill','mcp','database','app_config')"
    )
    _set_kinds(OLD_KINDS)
