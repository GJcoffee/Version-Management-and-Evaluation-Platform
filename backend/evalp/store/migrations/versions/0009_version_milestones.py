"""版本里程碑与归档：``versions`` 新增 ``milestone`` / ``milestone_title`` / ``archived_into``。

里程碑是阶段性成果，此前的过程版本可以归档到它名下（只影响列表展示）。已有版本全部是未归档的普通版本。
降级：删除这三列（归档关系丢失，版本本身不受影响）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

FK = "fk_versions_archived_into_versions"


def upgrade() -> None:
    with op.batch_alter_table("versions") as batch:
        batch.add_column(sa.Column("milestone", sa.Boolean(), server_default=sa.text("0"), nullable=False))
        batch.add_column(sa.Column("milestone_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("archived_into", sa.Text(), nullable=True))
        batch.create_foreign_key(op.f(FK), "versions", ["archived_into"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("versions") as batch:
        batch.drop_constraint(op.f(FK), type_="foreignkey")
        batch.drop_column("archived_into")
        batch.drop_column("milestone_title")
        batch.drop_column("milestone")
