"""独立记录历史版本接入与迁移审计，不改写历史业务 Spec。"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapter_binding_migrations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("agent_id", sa.Text(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "version_adapter_routes",
        sa.Column("version_id", sa.Text(), sa.ForeignKey("versions.id"), primary_key=True),
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("execution_blocked_reason", sa.Text(), nullable=False),
        sa.Column("migration_id", sa.Text(), sa.ForeignKey("adapter_binding_migrations.id"), nullable=False),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.exec_driver_sql("SELECT count(*) FROM adapter_binding_migrations").scalar():
        raise RuntimeError("存在接入迁移审计，不能丢弃迁移数据后降级")
    op.drop_table("version_adapter_routes")
    op.drop_table("adapter_binding_migrations")
