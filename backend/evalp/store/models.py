"""全部 ORM 模型，严格对应详设 3.3 的 DDL。

时间字段为 ISO8601 UTC 字符串（TEXT）；JSON 字段使用 JSONText；布尔语义字段用 INTEGER(0/1)。
后续模块只读不改本文件；确需加字段，请在 docs/DEVIATIONS.md 记录并新增 Alembic 迁移。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from evalp.store.db import JSONText

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# 含排序方向（DESC）的表达式索引：SQLite 方言无法反射比较，迁移中以原生 SQL 手工维护，
# autogenerate / 一致性测试需要忽略它们。
MANUAL_INDEXES = frozenset({"ix_sessions_agent_mode", "ix_jobs_claim"})


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _json_col(default: str | None = None, *, nullable: bool = False) -> Mapped[Any]:
    """JSON 列：default 为服务端默认的 JSON 文本（如 '{}'）。"""
    kwargs: dict[str, Any] = {}
    if default is not None:
        kwargs["server_default"] = text(f"'{default}'")
        kwargs["default"] = dict if default == "{}" else list
    return mapped_column(JSONText, nullable=nullable, **kwargs)


# ---------------------------------------------------------------- 模型服务 / Agent / 版本


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[str | None] = mapped_column(Text)  # secrets 中的条目名，不存明文
    headers: Mapped[dict[str, Any]] = _json_col("{}")
    models: Mapped[list[dict[str, Any]]] = _json_col("[]")  # [{name, context_window, price_in, price_out}]
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"), default=8)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (CheckConstraint("kind IN ('config','code')", name="kind"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_drafts_agent_id_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, ForeignKey("agents.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'main'"), default="main")
    spec: Mapped[dict[str, Any]] = _json_col()  # 未固化的 AgentSpec
    base_version_id: Mapped[str | None] = mapped_column(Text, ForeignKey("versions.id"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"), default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "seq", name="uq_versions_agent_id_seq"),
        UniqueConstraint("agent_id", "spec_hash", name="uq_versions_agent_id_spec_hash"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, ForeignKey("agents.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 展示为 v{seq}
    spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = _json_col()  # 已固化、规范化的 Spec
    source: Mapped[dict[str, Any]] = _json_col("{}")  # {git_commit, git_branch, dirty, patch_blob}
    parent_version_id: Mapped[str | None] = mapped_column(Text, ForeignKey("versions.id"))
    from_draft_id: Mapped[str | None] = mapped_column(Text, ForeignKey("drafts.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_via: Mapped[str] = mapped_column(Text, nullable=False)  # ui | cli | mcp | api
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class VersionTag(Base):
    __tablename__ = "version_tags"
    __table_args__ = (PrimaryKeyConstraint("agent_id", "tag", name="pk_version_tags"),)

    agent_id: Mapped[str] = mapped_column(Text, ForeignKey("agents.id"), nullable=False)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[str] = mapped_column(Text, ForeignKey("versions.id"), nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class VersionAssetRef(Base):
    __tablename__ = "version_asset_refs"
    __table_args__ = (
        PrimaryKeyConstraint("version_id", "asset_revision_id", "spec_path", name="pk_version_asset_refs"),
    )

    version_id: Mapped[str] = mapped_column(Text, ForeignKey("versions.id"), nullable=False)
    asset_revision_id: Mapped[str] = mapped_column(Text, ForeignKey("asset_revisions.id"), nullable=False)
    spec_path: Mapped[str] = mapped_column(Text, nullable=False)  # 如 prompts.system / tools[2]


# ---------------------------------------------------------------- Blob / 资产 / 数据集


class Blob(Base):
    __tablename__ = "blobs"

    hash: Mapped[str] = mapped_column(Text, primary_key=True)  # sha256
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    refcount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("kind IN ('prompt','tool','knowledge','dataset','file')", name="kind"),
        UniqueConstraint("kind", "name", name="uq_assets_kind_name"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # 小写字母、数字、连字符
    description: Mapped[str | None] = mapped_column(Text)
    latest_rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    deleted_at: Mapped[str | None] = mapped_column(Text)  # 软删除
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AssetRevision(Base):
    __tablename__ = "asset_revisions"
    __table_args__ = (
        UniqueConstraint("asset_id", "rev", name="uq_asset_revisions_asset_id_rev"),
        UniqueConstraint("asset_id", "blob_hash", name="uq_asset_revisions_asset_id_blob_hash"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    asset_id: Mapped[str] = mapped_column(Text, ForeignKey("assets.id"), nullable=False)
    rev: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_hash: Mapped[str] = mapped_column(Text, ForeignKey("blobs.hash"), nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    meta: Mapped[dict[str, Any]] = _json_col("{}")  # dataset: {case_count, schema_warnings}
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class EvalCase(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint("dataset_rev_id", "case_key", name="uq_eval_cases_dataset_rev_id_case_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_rev_id: Mapped[str] = mapped_column(Text, ForeignKey("asset_revisions.id"), nullable=False)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict[str, Any]] = _json_col()  # {"turns":[...]}，统一为多轮格式
    expected: Mapped[Any] = _json_col(nullable=True)
    rubric: Mapped[Any] = _json_col(nullable=True)
    tags: Mapped[list[str]] = _json_col("[]")
    metadata_: Mapped[dict[str, Any]] = mapped_column(  # 可含 tool_mocks / variables
        "metadata", JSONText, nullable=False, server_default=text("'{}'"), default=dict
    )
    case_hash: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------- 会话


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("mode IN ('chat','eval','replay','judge')", name="mode"),
        CheckConstraint("replay_mode IN ('anchored','free')", name="replay_mode"),
        Index("ix_sessions_agent_mode", "agent_id", "mode", text("created_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, ForeignKey("agents.id"), nullable=False)
    version_id: Mapped[str] = mapped_column(Text, ForeignKey("versions.id"), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    source_session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sessions.id"))  # 重放来源
    replay_mode: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'idle'"), default="idle")
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    stats: Mapped[dict[str, Any]] = _json_col("{}")  # 累计 tokens / cost / turns
    variables: Mapped[dict[str, Any]] = _json_col("{}")  # 会话级变量（迁移 0002）
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("session_id", "idx", name="uq_turns_session_id_idx"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    pair_index: Mapped[int | None] = mapped_column(Integer)  # 双栏对比时两侧轮次共享
    status: Mapped[str] = mapped_column(Text, nullable=False)  # running | ok | error | cancelled | timeout
    input: Mapped[Any] = _json_col()  # Message
    output: Mapped[Any] = _json_col(nullable=True)  # {final: Message, messages: [Message]}
    usage: Mapped[dict[str, Any]] = _json_col("{}")
    cost: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"), default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)  # 服务层序列化的 JSON：{code, message, retriable}
    # 锚定重放：本轮的上下文取自原会话的哪一轮（迁移 0002）
    anchored_from_turn_id: Mapped[str | None] = mapped_column(Text, ForeignKey("turns.id"))
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "seq", name="pk_events"),
        Index("ix_events_turn", "turn_id", "seq"),
        {"sqlite_with_rowid": False},
    )

    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, ForeignKey("turns.id"), nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str | None] = mapped_column(Text)
    parent_step_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any] = _json_col()  # 超过 32KB 的部分转存为 blob：{"$blob": hash}


# ---------------------------------------------------------------- 评测


class EvalBatch(Base):
    __tablename__ = "eval_batches"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    created_via: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, ForeignKey("eval_batches.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, ForeignKey("agents.id"), nullable=False)
    version_id: Mapped[str] = mapped_column(Text, ForeignKey("versions.id"), nullable=False)
    dataset_rev_id: Mapped[str] = mapped_column(Text, ForeignKey("asset_revisions.id"), nullable=False)
    case_filter: Mapped[dict[str, Any]] = _json_col("{}")  # {tags:[], case_keys:[]}
    evaluators: Mapped[Any] = _json_col()  # 评估器配置数组（见 8.5）
    evaluators_hash: Mapped[str] = mapped_column(Text, nullable=False)
    repeats: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"), default=3)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"), default=8)
    seed: Mapped[int | None] = mapped_column(Integer)
    cache_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'reuse'"), default="reuse"
    )  # reuse | refresh
    budget: Mapped[dict[str, Any]] = _json_col("{}")  # {max_cost, max_tokens}
    status: Mapped[str] = mapped_column(Text, nullable=False)  # 见 8.2
    progress: Mapped[dict[str, Any]] = _json_col("{}")  # {total, done, failed, running, cached}
    stats: Mapped[Any] = _json_col(nullable=True)  # 完成后的汇总（见 8.6）
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)


class EvalItem(Base):
    __tablename__ = "eval_items"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", "repeat_idx", name="uq_eval_items_run_id_case_id_repeat_idx"),
        Index("ix_items_cache", "cache_key", "status"),
        Index("ix_items_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("eval_runs.id"), nullable=False)
    case_id: Mapped[str] = mapped_column(Text, ForeignKey("eval_cases.id"), nullable=False)
    repeat_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # 见 8.2
    session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sessions.id"))
    reused_from_item_id: Mapped[str | None] = mapped_column(Text, ForeignKey("eval_items.id"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    usage: Mapped[Any] = _json_col(nullable=True)
    cost: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[int | None] = mapped_column(Integer)  # 门禁评估器是否全部通过
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)


class EvalScore(Base):
    __tablename__ = "eval_scores"
    __table_args__ = (
        UniqueConstraint("item_id", "evaluator_key", name="uq_eval_scores_item_id_evaluator_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, ForeignKey("eval_items.id"), nullable=False)
    evaluator_key: Mapped[str] = mapped_column(Text, nullable=False)
    evaluator_hash: Mapped[str] = mapped_column(Text, nullable=False)  # 单个评估器配置的哈希
    status: Mapped[str] = mapped_column(Text, nullable=False)  # done | error | pending_human
    score: Mapped[float | None] = mapped_column(Float)  # 归一化到 [0,1]
    raw_score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[Any] = _json_col(nullable=True)
    judge_session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sessions.id"))
    annotator: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class CompareGroup(Base):
    __tablename__ = "compare_groups"
    __table_args__ = (
        CheckConstraint("kind IN ('session','eval','version')", name="kind"),
        CheckConstraint("mode IN ('live','history','replay')", name="mode"),
        CheckConstraint(
            "replay_status IS NULL OR replay_status IN ('running','completed','failed')", name="replay_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(Text)
    left_ref: Mapped[str] = mapped_column(Text, nullable=False)
    right_ref: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)  # 用户备注（0003 之前 replay 组在此存 JSON，已迁出）
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    # 以下三列仅 mode=replay 使用（迁移 0003）
    replay_status: Mapped[str | None] = mapped_column(Text)  # running | completed | failed
    replay_turns: Mapped[list[int] | None] = _json_col(nullable=True)  # 被选中重放的原轮次 idx
    replay_error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 任务 / 设置


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", text("priority DESC"), "run_after", "created_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # eval_item | replay_turn | rescore
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    group_id: Mapped[str | None] = mapped_column(Text)  # eval_run_id / replay session_id
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending | running | done | failed | cancelled
    run_after: Mapped[str] = mapped_column(Text, nullable=False)  # 退避重试时推迟
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
