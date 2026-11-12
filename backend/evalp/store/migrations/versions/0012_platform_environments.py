"""允许按平台维护独立环境，保留 HiAgent 的旧存档键与所有历史数据。"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 环境表被存档表引用；用显式 CREATE/COPY 保留引用，不重写存档。
    conn = op.get_bind()
    conn.exec_driver_sql(
        "CREATE TABLE adapter_environments_new (name TEXT PRIMARY KEY NOT NULL,"
        " revision INTEGER DEFAULT 0 NOT NULL, updated_at TEXT,"
        " CONSTRAINT ck_adapter_environments_name CHECK"
        " (name IN ('production','test') OR name LIKE '%:production' OR name LIKE '%:test'))"
    )
    conn.exec_driver_sql("INSERT INTO adapter_environments_new SELECT * FROM adapter_environments")
    conn.exec_driver_sql("DROP TABLE adapter_environments")
    conn.exec_driver_sql("ALTER TABLE adapter_environments_new RENAME TO adapter_environments")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.exec_driver_sql(
        "SELECT count(*) FROM adapter_environments WHERE name NOT IN ('production','test')"
    ).scalar():
        raise RuntimeError("其他平台仍有配置，不能降级；请保留数据并使用新版服务")
    conn.exec_driver_sql(
        "CREATE TABLE adapter_environments_old (name TEXT PRIMARY KEY NOT NULL,"
        " revision INTEGER DEFAULT 0 NOT NULL, updated_at TEXT,"
        " CONSTRAINT ck_adapter_environments_name CHECK (name IN ('production','test')))"
    )
    conn.exec_driver_sql("INSERT INTO adapter_environments_old SELECT * FROM adapter_environments")
    conn.exec_driver_sql("DROP TABLE adapter_environments")
    conn.exec_driver_sql("ALTER TABLE adapter_environments_old RENAME TO adapter_environments")
