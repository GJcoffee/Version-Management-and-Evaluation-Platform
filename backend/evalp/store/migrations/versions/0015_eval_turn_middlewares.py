"""持久化评测逐轮中间件计划，历史评测默认空配置。"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_runs", sa.Column("turn_middlewares", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    if op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM eval_runs WHERE turn_middlewares != '{}'"
    ).scalar():
        raise RuntimeError("已有逐轮中间件评测，不能降级丢弃执行配置")
    op.drop_column("eval_runs", "turn_middlewares")
