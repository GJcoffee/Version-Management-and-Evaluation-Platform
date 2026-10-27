"""agents.conventions；assets.display_name / source_id / meta（发布规范与资产来源身份）

- ``agents.conventions``：该 agent 的发布 / 推送规范（JSON，默认 ``{}`` 即没有任何约束）；
- ``assets.source_id``：来源平台上的稳定 ID，(kind, source_id) 唯一（可空，多个 NULL 不冲突），用于识别改名；
- ``assets.display_name``：来源平台上的原始名称，仅用于展示；
- ``assets.meta``：资产级扩展信息，目前只有改名后保留的旧名字 ``former_names``。

只加列和约束，不改已有数据；降级时删除这些列。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("conventions", sa.Text(), server_default=sa.text("'{}'"), nullable=False)
        )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("source_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("meta", sa.Text(), server_default=sa.text("'{}'"), nullable=False))
        batch_op.create_unique_constraint("uq_assets_kind_source_id", ["kind", "source_id"])


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_constraint("uq_assets_kind_source_id", type_="unique")
        batch_op.drop_column("meta")
        batch_op.drop_column("source_id")
        batch_op.drop_column("display_name")
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("conventions")
