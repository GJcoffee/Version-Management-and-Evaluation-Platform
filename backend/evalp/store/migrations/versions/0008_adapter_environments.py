"""适配器接入配置改为全平台生产 / 测试两套，配置变化按环境存档；会话和评测已固化的快照不改。

升级：原版本修订按时间从旧到新依次成为生产存档 #1、#2…，最新的一条号码最大并且是当前生效的存档；
没有任何修订时，取最新带 ``spec.adapter`` 的版本作为生产存档 #1。测试初始为空。
降级：把来源为"迁移"的存档还原成原版本修订，其余存档丢弃。
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    environments = op.create_table(
        "adapter_environments",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=True),
        sa.CheckConstraint("name IN ('production','test')", name=op.f("ck_adapter_environments_name")),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_adapter_environments")),
    )
    revisions = op.create_table(
        "adapter_environment_revisions",
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("binding", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source IN ('edit','push','rollback','migration')",
            name=op.f("ck_adapter_environment_revisions_source"),
        ),
        sa.ForeignKeyConstraint(
            ["environment"], ["adapter_environments.name"],
            name=op.f("fk_adapter_environment_revisions_environment_adapter_environments"),
        ),
        sa.PrimaryKeyConstraint("environment", "revision"),
    )
    op.bulk_insert(environments, [
        {"name": "production", "revision": 0, "updated_at": None},
        {"name": "test", "revision": 0, "updated_at": None},
    ])

    conn = op.get_bind()
    old = [
        {"version_id": r[0], "revision": r[1], "binding": json.loads(r[2]).get("binding"),
         "reason": r[3], "created_at": r[4], "label": f"{r[6]}:v{r[5]}"}
        for r in conn.execute(sa.text(
            "SELECT r.version_id, r.revision, r.snapshot, r.reason, r.created_at, v.seq, a.name"
            " FROM adapter_revisions r JOIN versions v ON v.id = r.version_id"
            " JOIN agents a ON a.id = v.agent_id"
            " ORDER BY r.created_at ASC, r.version_id ASC, r.revision ASC"
        ))
    ]
    # 修订里 binding 为空表示"撤销覆盖"，不是一套接入配置，不进入存档。
    rows = []
    for item in (o for o in old if o["binding"] is not None):
        rows.append({
            "environment": "production", "revision": len(rows) + 1, "binding": json.dumps(
                item["binding"], ensure_ascii=False, separators=(",", ":")),
            "source": "migration",
            "reason": f"迁移自 {item['label']} 的接入修订 #{item['revision']}：{item['reason']}",
            "origin": json.dumps({
                "version_id": item["version_id"], "revision": item["revision"], "reason": item["reason"],
            }, ensure_ascii=False, separators=(",", ":")),
            "created_at": item["created_at"],
        })
    if not rows:
        spec_row = conn.execute(sa.text(
            "SELECT v.id, v.seq, a.name, json_extract(v.spec, '$.adapter'), v.created_at"
            " FROM versions v JOIN agents a ON a.id = v.agent_id"
            " WHERE json_extract(v.spec, '$.adapter') IS NOT NULL"
            " ORDER BY v.created_at DESC, v.id DESC LIMIT 1"
        )).first()
        if spec_row is not None:
            rows.append({
                "environment": "production", "revision": 1, "binding": spec_row[3], "source": "migration",
                "reason": f"迁移自 {spec_row[2]}:v{spec_row[1]} 的 spec.adapter",
                "origin": json.dumps({"version_id": spec_row[0]}), "created_at": spec_row[4],
            })
    if rows:
        op.bulk_insert(revisions, rows)
        conn.execute(
            sa.text(
                "UPDATE adapter_environments SET revision = :n, updated_at = :t WHERE name = 'production'"
            ),
            {"n": len(rows), "t": rows[-1]["created_at"]},
        )
    op.drop_table("adapter_revisions")


def downgrade() -> None:
    old = op.create_table(
        "adapter_revisions",
        sa.Column("version_id", sa.Text(), sa.ForeignKey("versions.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "revision"),
    )
    conn = op.get_bind()
    restored = []
    for binding, origin, created_at in conn.execute(sa.text(
        "SELECT binding, origin, created_at FROM adapter_environment_revisions"
        " WHERE source = 'migration' AND json_extract(origin, '$.revision') IS NOT NULL"
    )):
        origin = json.loads(origin)
        binding = json.loads(binding)
        fingerprint = hashlib.sha256(json.dumps(
            binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        restored.append({
            "version_id": origin["version_id"], "revision": origin["revision"], "reason": origin["reason"],
            "snapshot": json.dumps({
                "revision": origin["revision"], "binding": binding, "source": "override",
                "fingerprint": fingerprint,
            }, ensure_ascii=False, separators=(",", ":")),
            "created_at": created_at,
        })
    if restored:
        op.bulk_insert(old, restored)
    op.drop_table("adapter_environment_revisions")
    op.drop_table("adapter_environments")
