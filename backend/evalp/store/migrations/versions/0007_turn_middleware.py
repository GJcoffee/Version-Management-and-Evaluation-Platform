"""保存轮间中间件快照和实际输入；旧轮次保持 NULL。"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("effective_input", sa.Text(), nullable=True))
    op.add_column("turns", sa.Column("middleware_snapshot", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("turns") as batch:
        batch.drop_column("middleware_snapshot")
        batch.drop_column("effective_input")
