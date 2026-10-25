"""ORM -> DTO 转换（agent / draft / version 共用）。"""

from __future__ import annotations

from evalp.schemas.agent import AgentOut, DraftOut, VersionBrief, VersionSummary
from evalp.store.models import Agent, Draft, Version


def version_brief(v: Version) -> VersionBrief:
    return VersionBrief(id=v.id, seq=v.seq, spec_hash=v.spec_hash, created_at=v.created_at)


def agent_out(a: Agent, latest: Version | None = None) -> AgentOut:
    return AgentOut(
        id=a.id,
        name=a.name,
        kind=a.kind,
        description=a.description,
        archived=bool(a.archived),
        created_at=a.created_at,
        updated_at=a.updated_at,
        latest_version=version_brief(latest) if latest is not None else None,
    )


def draft_out(d: Draft) -> DraftOut:
    return DraftOut(
        id=d.id,
        agent_id=d.agent_id,
        name=d.name,
        spec=d.spec,
        base_version_id=d.base_version_id,
        revision=d.revision,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def version_summary(v: Version, tags: list[str] | None = None) -> VersionSummary:
    return VersionSummary(
        id=v.id,
        agent_id=v.agent_id,
        seq=v.seq,
        spec_hash=v.spec_hash,
        source=v.source or {},
        parent_version_id=v.parent_version_id,
        from_draft_id=v.from_draft_id,
        notes=v.notes,
        created_via=v.created_via,
        created_at=v.created_at,
        tags=tags or [],
    )
