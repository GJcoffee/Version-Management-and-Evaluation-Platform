"""initial schema（详设 3.3 全部表）

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('config','code')", name=op.f("ck_agents_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("name", name=op.f("uq_agents_name")),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("latest_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('prompt','tool','knowledge','dataset','file')", name=op.f("ck_assets_kind")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("kind", "name", name="uq_assets_kind_name"),
    )
    op.create_table(
        "blobs",
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("refcount", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_blobs")),
    )
    op.create_table(
        "compare_groups",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("left_ref", sa.Text(), nullable=False),
        sa.Column("right_ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('session','eval','version')", name=op.f("ck_compare_groups_kind")),
        sa.CheckConstraint("mode IN ('live','history','replay')", name=op.f("ck_compare_groups_mode")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compare_groups")),
    )
    op.create_table(
        "drafts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), server_default=sa.text("'main'"), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("base_version_id", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_drafts_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["base_version_id"], ["versions.id"], name=op.f("fk_drafts_base_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
        sa.UniqueConstraint("agent_id", "name", name="uq_drafts_agent_id_name"),
    )
    op.create_table(
        "eval_batches",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_via", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_batches")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_after", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key_ref", sa.Text(), nullable=True),
        sa.Column("headers", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("models", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("tpm_limit", sa.Integer(), nullable=True),
        sa.Column("max_concurrency", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_providers_name")),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    op.create_table(
        "versions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("spec_hash", sa.Text(), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("parent_version_id", sa.Text(), nullable=True),
        sa.Column("from_draft_id", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_via", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_versions_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["from_draft_id"], ["drafts.id"], name=op.f("fk_versions_from_draft_id_drafts")
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["versions.id"], name=op.f("fk_versions_parent_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_versions")),
        sa.UniqueConstraint("agent_id", "seq", name="uq_versions_agent_id_seq"),
        sa.UniqueConstraint("agent_id", "spec_hash", name="uq_versions_agent_id_spec_hash"),
    )
    op.create_table(
        "asset_revisions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("asset_id", sa.Text(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("blob_hash", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("meta", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name=op.f("fk_asset_revisions_asset_id_assets")),
        sa.ForeignKeyConstraint(
            ["blob_hash"], ["blobs.hash"], name=op.f("fk_asset_revisions_blob_hash_blobs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_revisions")),
        sa.UniqueConstraint("asset_id", "blob_hash", name="uq_asset_revisions_asset_id_blob_hash"),
        sa.UniqueConstraint("asset_id", "rev", name="uq_asset_revisions_asset_id_rev"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_session_id", sa.Text(), nullable=True),
        sa.Column("replay_mode", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'idle'"), nullable=False),
        sa.Column("last_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("stats", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("mode IN ('chat','eval','replay','judge')", name=op.f("ck_sessions_mode")),
        sa.CheckConstraint("replay_mode IN ('anchored','free')", name=op.f("ck_sessions_replay_mode")),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_sessions_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["sessions.id"], name=op.f("fk_sessions_source_session_id_sessions")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["versions.id"], name=op.f("fk_sessions_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_table(
        "version_tags",
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_version_tags_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["version_id"], ["versions.id"], name=op.f("fk_version_tags_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("agent_id", "tag", name="pk_version_tags"),
    )
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("dataset_rev_id", sa.Text(), nullable=False),
        sa.Column("case_key", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("rubric", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("metadata", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("case_hash", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_rev_id"],
            ["asset_revisions.id"],
            name=op.f("fk_eval_cases_dataset_rev_id_asset_revisions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_cases")),
        sa.UniqueConstraint("dataset_rev_id", "case_key", name="uq_eval_cases_dataset_rev_id_case_key"),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("dataset_rev_id", sa.Text(), nullable=False),
        sa.Column("case_filter", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("evaluators", sa.Text(), nullable=False),
        sa.Column("evaluators_hash", sa.Text(), nullable=False),
        sa.Column("repeats", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("concurrency", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("cache_policy", sa.Text(), server_default=sa.text("'reuse'"), nullable=False),
        sa.Column("budget", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("stats", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_eval_runs_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["eval_batches.id"], name=op.f("fk_eval_runs_batch_id_eval_batches")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_rev_id"],
            ["asset_revisions.id"],
            name=op.f("fk_eval_runs_dataset_rev_id_asset_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["versions.id"], name=op.f("fk_eval_runs_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("pair_index", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("usage", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("cost", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_turns_session_id_sessions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turns")),
        sa.UniqueConstraint("session_id", "idx", name="uq_turns_session_id_idx"),
    )
    op.create_table(
        "version_asset_refs",
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("asset_revision_id", sa.Text(), nullable=False),
        sa.Column("spec_path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_revision_id"],
            ["asset_revisions.id"],
            name=op.f("fk_version_asset_refs_asset_revision_id_asset_revisions"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["versions.id"], name=op.f("fk_version_asset_refs_version_id_versions")
        ),
        sa.PrimaryKeyConstraint("version_id", "asset_revision_id", "spec_path", name="pk_version_asset_refs"),
    )
    op.create_table(
        "eval_items",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("repeat_idx", sa.Integer(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("reused_from_item_id", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.Text(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("passed", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"], ["eval_cases.id"], name=op.f("fk_eval_items_case_id_eval_cases")
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_item_id"],
            ["eval_items.id"],
            name=op.f("fk_eval_items_reused_from_item_id_eval_items"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["eval_runs.id"], name=op.f("fk_eval_items_run_id_eval_runs")),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name=op.f("fk_eval_items_session_id_sessions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_items")),
        sa.UniqueConstraint(
            "run_id", "case_id", "repeat_idx", name="uq_eval_items_run_id_case_id_repeat_idx"
        ),
    )
    op.create_index("ix_items_cache", "eval_items", ["cache_key", "status"], unique=False)
    op.create_index("ix_items_run_status", "eval_items", ["run_id", "status"], unique=False)

    op.create_table(
        "events",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=True),
        sa.Column("parent_step_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_events_session_id_sessions")),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], name=op.f("fk_events_turn_id_turns")),
        sa.PrimaryKeyConstraint("session_id", "seq", name="pk_events"),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_events_turn", "events", ["turn_id", "seq"], unique=False)

    op.create_table(
        "eval_scores",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("evaluator_key", sa.Text(), nullable=False),
        sa.Column("evaluator_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("raw_score", sa.Float(), nullable=True),
        sa.Column("passed", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("judge_session_id", sa.Text(), nullable=True),
        sa.Column("annotator", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["eval_items.id"], name=op.f("fk_eval_scores_item_id_eval_items")
        ),
        sa.ForeignKeyConstraint(
            ["judge_session_id"], ["sessions.id"], name=op.f("fk_eval_scores_judge_session_id_sessions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_scores")),
        sa.UniqueConstraint("item_id", "evaluator_key", name="uq_eval_scores_item_id_evaluator_key"),
    )

    # 含排序方向的表达式索引（autogenerate 无法反射比较，手工维护）
    op.execute("CREATE INDEX ix_sessions_agent_mode ON sessions (agent_id, mode, created_at DESC)")
    op.execute("CREATE INDEX ix_jobs_claim ON jobs (status, priority DESC, run_after, created_at)")


def downgrade() -> None:
    op.drop_index("ix_sessions_agent_mode", table_name="sessions")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("eval_scores")
    op.drop_index("ix_events_turn", table_name="events")

    op.drop_table("events")
    op.drop_index("ix_items_run_status", table_name="eval_items")
    op.drop_index("ix_items_cache", table_name="eval_items")

    op.drop_table("eval_items")
    op.drop_table("version_asset_refs")
    op.drop_table("turns")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_table("version_tags")
    op.drop_table("sessions")
    op.drop_table("asset_revisions")
    op.drop_table("versions")
    op.drop_table("settings")
    op.drop_table("providers")
    op.drop_table("jobs")
    op.drop_table("eval_batches")
    op.drop_table("drafts")
    op.drop_table("compare_groups")
    op.drop_table("blobs")
    op.drop_table("assets")
    op.drop_table("agents")
