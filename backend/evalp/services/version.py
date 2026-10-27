"""版本服务：列表 / 详情 / 标签 / to-draft / diff，以及供运行时、评测使用的 ``get_resolved``。

版本引用写法（diff、会话创建等处通用）：版本 id（``ver_…``）、``agent名:v12``、
``agent名:标签``、``agent名:latest``。

diff 的两侧还可以是草稿（取已保存的内容）：草稿 id（``drf_…``）或 ``agent名:@草稿名``（如 ``bot:@main``）。
标签名不能以 ``@`` 开头，所以不会与标签混淆。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import NotFound, ValidationFailed
from evalp.schemas.agent import (
    DraftOut,
    TagOut,
    VersionAssetItem,
    VersionAssetRefOut,
    VersionAssetsOut,
    VersionOut,
    VersionSummary,
)
from evalp.services.agent import AgentService
from evalp.services.asset import AssetService
from evalp.services.convert import draft_out, version_summary
from evalp.spec.canonical import canonicalize
from evalp.spec.diff import diff_specs, identity_paths
from evalp.spec.models import CodeSpec, ConfigSpec, parse_spec
from evalp.spec.resolve import iter_pins, unresolve
from evalp.spec.validate import validate_spec
from evalp.store.models import Agent, Asset, AssetRevision, Draft, Version, VersionAssetRef, VersionTag
from evalp.utils.time import now_iso

_SEQ_RE = re.compile(r"^v(\d+)$")


@dataclass
class ResolvedVersion:
    """已固化的版本：``spec`` 中的资产引用是 ``{"$asset": {kind,name,rev,hash}}``。

    读取资产内容：``AssetService.read_bytes(pin)`` / ``read_text(pin)``，pin 即上述 ``$asset`` 字典。
    """

    id: str
    agent_id: str
    agent_name: str
    agent_kind: str
    seq: int
    spec_hash: str
    spec: dict[str, Any]
    source: dict[str, Any]

    def spec_model(self) -> ConfigSpec | CodeSpec:
        """把 spec 解析为 ConfigSpec / CodeSpec（引用位置是 ResolvedRef 模型）。"""
        return parse_spec(self.spec)


class VersionService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------ 引用解析

    async def resolve_ref_orm(self, s: AsyncSession, ref: str) -> Version:
        """把版本引用（id / ``agent:v12`` / ``agent:tag`` / ``agent:latest``）解析为 Version。"""
        if ref.startswith("ver_"):
            v = await s.get(Version, ref)
            if v is None:
                raise NotFound(f"版本不存在: {ref}", {"ref": ref})
            return v
        agent_part, sep, sel = ref.partition(":")
        if not sep or not agent_part or not sel:
            raise ValidationFailed(
                f"版本引用格式不对: {ref!r}（应为版本 id、agent名:v12 或 agent名:标签）",
                {"errors": [{"path": "ref", "message": "格式不对"}]},
            )
        agent = await AgentService.get_orm(s, agent_part)
        stmt = select(Version).where(Version.agent_id == agent.id)
        if (m := _SEQ_RE.match(sel)) is not None:
            stmt = stmt.where(Version.seq == int(m.group(1)))
        elif sel == "latest":
            stmt = stmt.order_by(Version.seq.desc()).limit(1)
        else:
            stmt = stmt.join(VersionTag, VersionTag.version_id == Version.id).where(
                VersionTag.agent_id == agent.id, VersionTag.tag == sel
            )
        v = (await s.execute(stmt)).scalars().first()
        if v is None:
            raise NotFound(f"版本不存在: {ref}", {"ref": ref})
        return v

    async def resolve_ref(self, ref: str) -> str:
        """版本引用 -> 版本 id。"""
        async with self.ctx.db.read() as s:
            return (await self.resolve_ref_orm(s, ref)).id

    # ------------------------------------------------------------ 供其他模块使用

    async def get_resolved(self, version_id: str, *, session: AsyncSession | None = None) -> ResolvedVersion:
        """按 id 读取已固化的版本 Spec（会话、评测模块使用）；不存在抛 NotFound。"""

        async def run(s: AsyncSession) -> ResolvedVersion:
            v = await s.get(Version, version_id)
            if v is None:
                raise NotFound(f"版本不存在: {version_id}", {"id": version_id})
            a = await s.get(Agent, v.agent_id)
            assert a is not None
            return ResolvedVersion(
                id=v.id,
                agent_id=a.id,
                agent_name=a.name,
                agent_kind=a.kind,
                seq=v.seq,
                spec_hash=v.spec_hash,
                spec=v.spec,
                source=v.source or {},
            )

        if session is not None:
            return await run(session)
        async with self.ctx.db.read() as s:
            return await run(s)

    # ------------------------------------------------------------ 查询

    async def _tags_of(self, s: AsyncSession, version_ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        if not version_ids:
            return out
        rows = (
            await s.execute(
                select(VersionTag.version_id, VersionTag.tag)
                .where(VersionTag.version_id.in_(version_ids))
                .order_by(VersionTag.tag)
            )
        ).all()
        for vid, tag in rows:
            out.setdefault(vid, []).append(tag)
        return out

    async def out_in_session(self, s: AsyncSession, v: Version) -> VersionOut:
        """版本详情（含标签与引用的资产）。"""
        tags = (await self._tags_of(s, [v.id])).get(v.id, [])
        rows = (
            await s.execute(
                select(VersionAssetRef, AssetRevision)
                .join(AssetRevision, AssetRevision.id == VersionAssetRef.asset_revision_id)
                .where(VersionAssetRef.version_id == v.id)
                .order_by(VersionAssetRef.spec_path)
            )
        ).all()
        # 资产名在 spec 里的 pin 上，这里通过 spec 反查 (spec_path -> kind/name)
        # spec_path 对外用身份键路径（tools[query-order]），与 diff 的 path 一致；库里存的是下标路径
        names = _pin_names(v.spec)
        xpaths = identity_paths(v.spec)
        assets = [
            VersionAssetRefOut(
                spec_path=xpaths.get(ref.spec_path, ref.spec_path),
                kind=names.get(ref.spec_path, ("", ""))[0],
                name=names.get(ref.spec_path, ("", ""))[1],
                rev=rev.rev,
                asset_revision_id=rev.id,
            )
            for ref, rev in rows
        ]
        return VersionOut(**version_summary(v, tags).model_dump(), spec=v.spec, assets=assets)

    async def get(self, ref: str) -> VersionOut:
        """版本详情（含标签）；ref 可以是版本 id 或 ``agent:v12`` 等。"""
        async with self.ctx.db.read() as s:
            v = await self.resolve_ref_orm(s, ref)
            return await self.out_in_session(s, v)

    async def assets(self, ref: str) -> VersionAssetsOut:
        """版本绑定的资产清单：所用修订、来源，以及每个资产最近一次变化发生在哪个版本。

        "最近一次变化"：从该版本往前，连续使用同一修订的最早版本；前一个版本用了别的修订时，
        ``previous_rev`` 给出变化前的修订号，资产是该版本新加入的则为 null。
        """
        async with self.ctx.db.read() as s:
            v = await self.resolve_ref_orm(s, ref)
            rows = (
                await s.execute(
                    select(Version, VersionAssetRef.spec_path, AssetRevision, Asset)
                    .join(VersionAssetRef, VersionAssetRef.version_id == Version.id)
                    .join(AssetRevision, AssetRevision.id == VersionAssetRef.asset_revision_id)
                    .join(Asset, Asset.id == AssetRevision.asset_id)
                    .where(Version.agent_id == v.agent_id, Version.seq <= v.seq)
                    .order_by(Version.seq.desc(), VersionAssetRef.spec_path)
                )
            ).all()
            tags = await self._tags_of(s, [v.id])
            summary = version_summary(v, tags.get(v.id))
            xpaths = identity_paths(v.spec)
        # 每个资产在各版本使用的修订（版本从新到旧）：asset_id -> [(版本, 修订)]
        history: dict[str, list[tuple[Version, AssetRevision]]] = {}
        current: dict[str, tuple[Asset, AssetRevision, list[str]]] = {}
        for ver, spec_path, rev, asset in rows:
            seen = history.setdefault(asset.id, [])
            if not seen or seen[-1][0].id != ver.id:
                seen.append((ver, rev))
            if ver.id == v.id:
                _, _, paths = current.setdefault(asset.id, (asset, rev, []))
                paths.append(xpaths.get(spec_path, spec_path))
        items: list[VersionAssetItem] = []
        for asset_id, (asset, rev, paths) in current.items():
            changed_in, previous = v, None
            for ver, used in history[asset_id][1:]:
                if ver.seq != changed_in.seq - 1:  # 中间有版本没用这个资产：从头算
                    break
                if used.id != rev.id:
                    previous = used.rev
                    break
                changed_in = ver
            items.append(
                VersionAssetItem(
                    asset_id=asset_id,
                    kind=asset.kind,
                    name=asset.name,
                    display_name=asset.display_name,
                    source_id=asset.source_id,
                    rev=rev.rev,
                    asset_revision_id=rev.id,
                    spec_paths=paths,
                    origin=(rev.meta or {}).get("origin"),
                    changed_in_seq=changed_in.seq,
                    changed_in_version_id=changed_in.id,
                    previous_rev=previous,
                )
            )
        items.sort(key=lambda i: (i.kind, i.name))
        return VersionAssetsOut(version=summary, items=items)

    async def list(
        self, agent_ref: str, *, limit: int = 50, cursor: str | None = None
    ) -> Page[VersionSummary]:
        """某个 agent 的版本列表（新版本在前，含标签）。"""
        async with self.ctx.db.read() as s:
            agent = await AgentService.get_orm(s, agent_ref)
            page = await paginate(
                s, select(Version).where(Version.agent_id == agent.id), Version.id, limit=limit, cursor=cursor
            )
            versions: list[Version] = page.items
            tags = await self._tags_of(s, [v.id for v in versions])
            items = [version_summary(v, tags.get(v.id)) for v in versions]
        return Page[VersionSummary](items=items, next_cursor=page.next_cursor)

    async def list_tags(self, agent_ref: str) -> list[TagOut]:
        """agent 的全部标签。"""
        async with self.ctx.db.read() as s:
            agent = await AgentService.get_orm(s, agent_ref)
            rows = (
                await s.execute(
                    select(VersionTag).where(VersionTag.agent_id == agent.id).order_by(VersionTag.tag)
                )
            ).scalars()
            return [
                TagOut(agent_id=t.agent_id, tag=t.tag, version_id=t.version_id, updated_at=t.updated_at)
                for t in rows
            ]

    # ------------------------------------------------------------ 标签

    async def set_tag(self, version_ref: str, tag: str) -> TagOut:
        """把标签指向这个版本（UPSERT：同一标签会从旧版本移过来）。"""
        async with self.ctx.db.write() as s:
            v = await self.resolve_ref_orm(s, version_ref)
            row = await self.upsert_tag(s, v, tag)
            return TagOut(
                agent_id=row.agent_id, tag=row.tag, version_id=row.version_id, updated_at=row.updated_at
            )

    @staticmethod
    async def upsert_tag(s: AsyncSession, v: Version, tag: str) -> VersionTag:
        """事务内：把标签指向版本（已存在则移过来）。发布时带标签也走这里，与创建版本在同一个事务里。"""
        row = await s.get(VersionTag, (v.agent_id, tag))
        now = now_iso()
        if row is None:
            row = VersionTag(agent_id=v.agent_id, tag=tag, version_id=v.id, updated_at=now)
            s.add(row)
        else:
            row.version_id = v.id
            row.updated_at = now
        await s.flush()
        return row

    async def delete_tag(self, version_ref: str, tag: str) -> None:
        """删除指向该版本的标签；标签不存在或指向其他版本时抛 NotFound。"""
        async with self.ctx.db.write() as s:
            v = await self.resolve_ref_orm(s, version_ref)
            row = await s.get(VersionTag, (v.agent_id, tag))
            if row is None or row.version_id != v.id:
                raise NotFound(f"该版本上没有标签: {tag}", {"tag": tag})
            await s.delete(row)

    # ------------------------------------------------------------ 回滚

    async def to_draft(self, version_ref: str, draft_name: str = "main") -> DraftOut:
        """用版本的 Spec 覆盖草稿（不存在则新建），``@rev`` 保持原样，``base_version_id`` 设为该版本。"""
        async with self.ctx.db.write() as s:
            v = await self.resolve_ref_orm(s, version_ref)
            spec = unresolve(v.spec)
            draft = (
                await s.execute(select(Draft).where(Draft.agent_id == v.agent_id, Draft.name == draft_name))
            ).scalar_one_or_none()
            if draft is None:
                agent = await s.get(Agent, v.agent_id)
                assert agent is not None
                draft = await AgentService.insert_draft(s, agent, draft_name, spec, base_version_id=v.id)
            else:
                draft.spec = spec
                draft.base_version_id = v.id
                draft.revision += 1
                draft.updated_at = now_iso()
            await s.flush()
            return draft_out(draft)

    # ------------------------------------------------------------ diff

    async def _diff_side(self, s: AsyncSession, ref: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """diff 一侧的引用 -> ``(摘要, 固化后的 Spec, 是否草稿)``。

        版本摘要为 ``{id, seq, spec_hash}``；草稿摘要为 ``{id, draft: true, name, revision, spec_hash}``。
        草稿按发布时的流程固化引用并规范化（补齐默认值）；
        校验不通过时和发布一样抛错（原因在 ``details.errors``）。
        """
        if ref.startswith("drf_") or ref.partition(":")[2].startswith("@"):
            if ref.startswith("drf_"):
                draft = await s.get(Draft, ref)
                if draft is None:
                    raise NotFound(f"草稿不存在: {ref}", {"ref": ref})
                agent = await s.get(Agent, draft.agent_id)
                assert agent is not None
            else:
                agent_part, _, sel = ref.partition(":")
                if not agent_part or len(sel) < 2:
                    raise ValidationFailed(
                        f"草稿引用格式不对: {ref!r}（应为 drf_… 或 agent名:@草稿名）",
                        {"errors": [{"path": "ref", "message": "格式不对"}]},
                    )
                agent = await AgentService.get_orm(s, agent_part)
                draft = (
                    await s.execute(select(Draft).where(Draft.agent_id == agent.id, Draft.name == sel[1:]))
                ).scalar_one_or_none()
                if draft is None:
                    raise NotFound(f"草稿不存在: {ref}", {"ref": ref})
            report = await validate_spec(self.ctx, s, draft.spec, agent_kind=agent.kind)
            report.raise_for_errors()
            assert report.resolved is not None
            canon = canonicalize(report.resolved)
            info = {
                "id": draft.id,
                "draft": True,
                "name": draft.name,
                "revision": draft.revision,
                "spec_hash": canon.hash,
            }
            return info, canon.data, True
        v = await self.resolve_ref_orm(s, ref)
        return {"id": v.id, "seq": v.seq, "spec_hash": v.spec_hash}, v.spec, False

    async def diff(self, left_ref: str, right_ref: str) -> dict[str, Any]:
        """两个版本（或草稿）的 Spec diff（详设 4.5）。

        草稿侧的 ``source.git`` 是发布时才采集的（草稿里没有），涉及草稿时两侧都忽略它，避免出现无意义的差异。
        """
        async with self.ctx.db.read() as s:
            left, lspec, ldraft = await self._diff_side(s, left_ref)
            right, rspec, rdraft = await self._diff_side(s, right_ref)
        if ldraft or rdraft:
            lspec, rspec = _without_git(lspec), _without_git(rspec)

        assets = self.ctx.svc(AssetService)

        async def load_text(pin: dict[str, Any]) -> str | None:
            try:
                return await assets.read_text(pin)
            except (NotFound, UnicodeDecodeError):
                return None

        async with self.ctx.db.read() as s:
            renames = await self._renames(s, lspec, rspec)
        result = await diff_specs(lspec, rspec, load_text, renames)
        return {"left": left, "right": right, **result}

    @staticmethod
    async def _renames(
        s: AsyncSession, lspec: dict[str, Any], rspec: dict[str, Any]
    ) -> dict[tuple[str, str], str]:
        """左侧 Spec 用的旧资产名 -> 右侧 Spec 里的新名：右侧新出现的资产，
        其 ``former_names`` 里有左侧消失的名字。"""
        left = {(p["kind"], p["name"]) for _, p in iter_pins(lspec)}
        right = {(p["kind"], p["name"]) for _, p in iter_pins(rspec)}
        gone, new = left - right, right - left
        if not gone or not new:
            return {}
        assets = (await s.execute(select(Asset).where(tuple_(Asset.kind, Asset.name).in_(new)))).scalars()
        return {
            (a.kind, old): a.name
            for a in assets
            for old in a.meta.get("former_names", [])
            if (a.kind, old) in gone
        }

    async def next_seq(self, s: AsyncSession, agent_id: str) -> int:
        """下一个版本序号。"""
        cur = (
            await s.execute(select(func.max(Version.seq)).where(Version.agent_id == agent_id))
        ).scalar_one()
        return (cur or 0) + 1


def _without_git(spec: dict[str, Any]) -> dict[str, Any]:
    """去掉代码型 Spec 的 ``source.git``（发布时采集）。"""
    source = spec.get("source")
    if isinstance(source, dict) and "git" in source:
        return {**spec, "source": {k: v for k, v in source.items() if k != "git"}}
    return spec


def _pin_names(spec: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {path: (pin["kind"], pin["name"]) for path, pin in iter_pins(spec)}
