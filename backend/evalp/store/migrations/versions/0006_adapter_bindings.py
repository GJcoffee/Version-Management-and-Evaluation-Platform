"""接入修订、凭据元数据与运行快照；旧运行保持原接入，不按新绑定倒推。"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapter_revisions",
        sa.Column("version_id", sa.Text(), sa.ForeignKey("versions.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "revision"),
    )
    op.create_table(
        "adapter_credentials",
        sa.Column("ref", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("adapter_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    for table in ("sessions", "eval_runs"):
        op.add_column(table, sa.Column("adapter_snapshot", sa.Text(), nullable=True))
        # 历史运行固定业务版本里原有的接入；NULL 不代表可以使用将来的覆盖。
        op.execute(
            sa.text(
                f"UPDATE {table} SET adapter_snapshot = json_object("
                "'revision', 0, 'binding', json_extract("
                f"(SELECT spec FROM versions WHERE versions.id = {table}.version_id), '$.adapter'), "
                "'source', 'legacy')"
            )
        )


def downgrade() -> None:
    for table in ("eval_runs", "sessions"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("adapter_snapshot")
    op.drop_table("adapter_credentials")
    op.drop_table("adapter_revisions")
