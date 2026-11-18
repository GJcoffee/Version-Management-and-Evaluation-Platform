"""可管理的观测密钥连接；凭据保存在现有加密存储。"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_key_connections",
        sa.Column("id", sa.Text(), primary_key=True),
        *[sa.Column(k, sa.Text(), nullable=False) for k in (
            "name", "origin", "top_url", "workspace_id", "credential_source", "status", "created_at", "updated_at",
        )],
        sa.Column("secret_ref", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.Text()),
        sa.Column("last_error", sa.Text()),
    )


def downgrade() -> None:
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM trace_key_connections").scalar():
        raise RuntimeError("已有密钥连接，不能降级删除连接配置")
    op.drop_table("trace_key_connections")
