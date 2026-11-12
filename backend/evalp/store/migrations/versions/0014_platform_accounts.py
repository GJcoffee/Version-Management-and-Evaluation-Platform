"""账号与不可变加密登录态修订，原绑定和历史快照保持不变。"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("adapter_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("tenant_name", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_secret_ref", sa.Text(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("session_revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_check_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("adapter_id", "origin", "tenant_name", "username", name="uq_platform_account_identity"),
    )
    op.create_table(
        "platform_account_sessions",
        sa.Column("account_id", sa.Text(), sa.ForeignKey("platform_accounts.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM platform_accounts").scalar():
        raise RuntimeError("已有账号与登录态修订，不能降级丢弃凭据管理数据")
    op.drop_table("platform_account_sessions")
    op.drop_table("platform_accounts")
