"""工作区同步（详设 10）中服务端负责的部分。

- ``push``：一个事务内完成——agent 不存在则创建；对内容有变化的文件创建资产修订；把 spec 中的
  ``file:路径`` 改写为 ``asset://kind/name@latest``；最后保存草稿（乐观锁，force 时跳过）；
- ``pull``：返回草稿 Spec，以及 ``asset_map`` 中各资产的最新内容。

客户端（读文件、``.evalp.toml``、state.json）由 CLI / MCP 实现。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import posixpath
from typing import Any

from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import EvalpError, NotFound, ValidationFailed
from evalp.logging import get_logger
from evalp.schemas.agent import PullIn, PullOut, PushIn, PushOut, WorkspaceFile
from evalp.services.agent import AgentService, check_draft_spec, minimal_spec
from evalp.services.asset import AssetService
from evalp.services.convert import draft_out
from evalp.services.draft import DraftService, conflict_error
from evalp.spec.diff import diff_specs
from evalp.spec.refs import (
    RefError,
    format_asset_ref,
    is_asset_ref,
    is_file_ref,
    parse_asset_ref,
    parse_file_ref,
    validate_asset_kind,
    validate_asset_name,
)
from evalp.store.models import Asset, AssetRevision, Blob
from evalp.utils.time import now_iso

log = get_logger(__name__)

CONTENT_MISSING = "CONTENT_MISSING"


def norm_path(path: str) -> str:
    """规范化本地相对路径（统一为 / 分隔，去掉 ./ 前缀）。"""
    p = posixpath.normpath(path.replace("\\", "/"))
    return p[2:] if p.startswith("./") else p


def parse_asset_key(key: str) -> tuple[str, str]:
    """``kind/name`` -> (kind, name)；格式不对抛 ValueError。"""
    kind, sep, name = key.partition("/")
    if not sep:
        raise RefError(f"资产必须写成 kind/name: {key!r}")
    return validate_asset_kind(kind), validate_asset_name(name)


def rewrite_file_refs(spec: dict[str, Any], mapping: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """把引用位置上的 ``file:路径`` 改写为 ``asset://kind/name@latest``。

    mapping: 规范化路径 -> ``kind/name``。返回 (新 spec, 没有映射的 file: 路径列表)。
    """
    out = copy.deepcopy(spec)
    missing: list[str] = []

    def one(value: Any) -> Any:
        if isinstance(value, str) and is_file_ref(value):
            path = norm_path(parse_file_ref(value))
            key = mapping.get(path)
            if key is None:
                missing.append(path)
                return value
            kind, name = key.split("/", 1)
            return format_asset_ref(kind, name, None)
        return value

    def each(lst: Any) -> None:
        if isinstance(lst, list):
            for i, v in enumerate(lst):
                lst[i] = one(v)

    prompts = out.get("prompts")
    if isinstance(prompts, dict) and "system" in prompts:
        prompts["system"] = one(prompts["system"])
    tools = out.get("tools")
    if isinstance(tools, list):
        for i, tool in enumerate(tools):
            if isinstance(tool, dict):
                w = tool.get("with")
                if isinstance(w, dict):
                    each(w.get("sources"))
            else:
                tools[i] = one(tool)
    knowledge = out.get("knowledge")
    if isinstance(knowledge, dict):
        each(knowledge.get("inject"))
    return out, missing


def restore_file_refs(spec: dict[str, Any], reverse: dict[str, str]) -> dict[str, Any]:
    """``asset://kind/name@latest`` -> ``file:路径``（只处理 asset_map 中有映射的 @latest 引用）。"""
    out = copy.deepcopy(spec)

    def one(value: Any) -> Any:
        if isinstance(value, str) and is_asset_ref(value):
            try:
                ref = parse_asset_ref(value)
            except RefError:
                return value
            path = reverse.get(f"{ref.kind}/{ref.name}")
            if ref.is_latest and path is not None:
                return f"file:{path}"
        return value

    def each(lst: Any) -> None:
        if isinstance(lst, list):
            for i, v in enumerate(lst):
                lst[i] = one(v)

    prompts = out.get("prompts")
    if isinstance(prompts, dict) and "system" in prompts:
        prompts["system"] = one(prompts["system"])
    tools = out.get("tools")
    if isinstance(tools, list):
        for i, tool in enumerate(tools):
            if isinstance(tool, dict):
                w = tool.get("with")
                if isinstance(w, dict):
                    each(w.get("sources"))
            else:
                tools[i] = one(tool)
    knowledge = out.get("knowledge")
    if isinstance(knowledge, dict):
        each(knowledge.get("inject"))
    return out


def decode_file(f: WorkspaceFile) -> bytes:
    """解码 content（utf-8 或 base64）。"""
    assert f.content is not None
    if f.encoding == "base64":
        try:
            return base64.b64decode(f.content, validate=True)
        except ValueError:
            raise ValidationFailed(
                f"{f.path} 的 content 不是合法的 base64",
                {"errors": [{"path": f.path, "message": "不是合法的 base64"}], "file": f.path},
            ) from None
    return f.content.encode("utf-8")


class WorkspaceService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def push(self, body: PushIn) -> PushOut:
        """同步工作区到草稿（一个事务内完成，任何一步失败整体回滚）。"""
        assets = self.ctx.svc(AssetService)
        # --- 0. 校验 files
        keys: dict[str, WorkspaceFile] = {}
        mapping: dict[str, str] = {}
        seen_assets: set[str] = set()
        for f in body.files:
            try:
                parse_asset_key(f.asset)
            except RefError as e:
                raise ValidationFailed(
                    f"{f.path}: {e}", {"errors": [{"path": f.path, "message": str(e)}], "file": f.path}
                ) from None
            p = norm_path(f.path)
            if p in keys or f.asset in seen_assets:
                raise ValidationFailed(
                    f"files 中存在重复的路径或资产: {f.path} / {f.asset}",
                    {"errors": [{"path": f.path, "message": "路径或资产重复"}]},
                )
            keys[p] = f
            seen_assets.add(f.asset)
            mapping[p] = f.asset

        # --- 1. 把 spec 中的 file: 改写为 asset://…@latest
        new_spec, unmapped = rewrite_file_refs(body.spec, mapping)
        if unmapped:
            raise ValidationFailed(
                "spec 中的 file: 引用没有对应的 files 条目: " + ", ".join(sorted(set(unmapped))),
                {"errors": [{"path": p, "message": "files 中没有这个文件"} for p in sorted(set(unmapped))]},
            )

        uploaded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        async with self.ctx.db.write() as s:
            # --- 2. agent / 草稿
            agent = await AgentService.find_orm(s, body.agent_name)
            agent_created = False
            spec_kind = new_spec.get("kind")
            if agent is None:
                kind = body.agent_kind or spec_kind or "config"
                if body.agent_kind and spec_kind and body.agent_kind != spec_kind:
                    raise ValidationFailed(
                        "agent_kind 与 spec.kind 不一致",
                        {"errors": [{"path": "spec.kind", "message": f"应为 {body.agent_kind}"}]},
                    )
                agent = await AgentService.insert_agent(s, body.agent_name, kind)
                agent_created = True
            elif body.agent_kind and body.agent_kind != agent.kind:
                raise ValidationFailed(
                    f"agent {agent.name} 的类型是 {agent.kind}，与 agent_kind={body.agent_kind} 不一致",
                    {"errors": [{"path": "agent_kind", "message": f"应为 {agent.kind}"}]},
                )
            new_spec = check_draft_spec(new_spec, agent.kind)
            draft = None if agent_created else await DraftService.find_by_name(s, agent.id, body.draft_name)
            if draft is not None and not body.force and body.expected_revision != draft.revision:
                local_diff = await diff_specs(draft.spec, new_spec)
                raise conflict_error(draft, diff=local_diff, server_spec=draft.spec)

            # --- 3. 资产修订
            for path, f in keys.items():
                kind, name = parse_asset_key(f.asset)
                asset, created = await assets.get_or_create_asset(s, kind, name)
                head = (
                    await s.execute(
                        select(AssetRevision).where(
                            AssetRevision.asset_id == asset.id, AssetRevision.rev == asset.latest_rev
                        )
                    )
                ).scalar_one_or_none()
                info = {"path": f.path, "asset": f.asset, "hash": f.hash}
                try:
                    if f.content is not None:
                        data = decode_file(f)
                        if hashlib.sha256(data).hexdigest() != f.hash:
                            raise ValidationFailed(
                                f"{f.path} 的 content 与 hash 不一致",
                                {"errors": [{"path": f.path, "message": "content 的 sha256 与 hash 不一致"}]},
                            )
                    if head is not None and head.blob_hash == f.hash:
                        skipped.append({**info, "rev": head.rev, "asset_id": asset.id})
                        continue
                    meta_extra = {"origin": f.origin} if f.origin else None
                    if f.content is not None:
                        revision, existed = await assets.add_revision_bytes(
                            s, asset, data, f.content_type, f"push {path}", f.column_map, meta_extra
                        )
                    else:
                        existing = (
                            await s.execute(
                                select(AssetRevision).where(
                                    AssetRevision.asset_id == asset.id, AssetRevision.blob_hash == f.hash
                                )
                            )
                        ).scalar_one_or_none()
                        if existing is not None:
                            asset.latest_rev = existing.rev  # 改回旧内容
                            revision, existed = existing, True
                        elif self.ctx.blobs.exists(f.hash) and await s.get(Blob, f.hash) is not None:
                            revision, existed = await assets.add_revision_from_blob(
                                s, asset, f.hash, f.content_type, f"push {path}", meta_extra
                            )
                        else:
                            missing.append(info)
                            continue
                except EvalpError as e:
                    e.details.setdefault("file", f.path)
                    e.message = f"{f.path}: {e.message}"
                    raise
                uploaded.append(
                    {
                        **info,
                        "rev": revision.rev,
                        "revision_id": revision.id,
                        "asset_id": asset.id,
                        "asset_created": created,
                        "existed": existed,
                    }
                )
            if missing:
                raise ValidationFailed(
                    "以下文件在服务端没有对应内容，请带上 content 重新提交: "
                    + ", ".join(m["path"] for m in missing),
                    {"missing": missing},
                    code=CONTENT_MISSING,
                )

            # --- 4. 保存草稿
            if draft is None:
                draft = await AgentService.insert_draft(s, agent, body.draft_name, new_spec)
                if agent_created and body.draft_name != "main":
                    await AgentService.insert_draft(s, agent, "main", await minimal_spec(s, agent.kind))
            else:
                draft.spec = new_spec
                draft.revision += 1
                draft.updated_at = now_iso()
            agent.updated_at = now_iso()
            await s.flush()
            out = PushOut(
                agent_id=agent.id,
                agent_created=agent_created,
                draft=draft_out(draft),
                uploaded=uploaded,
                skipped=skipped,
            )
        log.info(
            "workspace.push",
            agent=body.agent_name,
            uploaded=len(uploaded),
            skipped=len(skipped),
            revision=out.draft.revision,
        )
        return out

    async def pull(self, body: PullIn) -> PullOut:
        """返回草稿 Spec 与 asset_map 中各资产的最新内容。

        local_spec：把引用 ``@latest`` 且在 asset_map 中有映射的资产还原为 ``file:路径`` 后的 Spec。
        """
        reverse: dict[str, str] = {}
        for key, path in body.asset_map.items():
            try:
                parse_asset_key(key)
            except RefError as e:
                raise ValidationFailed(
                    f"asset_map 的键格式不对: {e}", {"errors": [{"path": key, "message": str(e)}]}
                ) from None
            reverse[key] = norm_path(path)
        async with self.ctx.db.read() as s:
            agent = await AgentService.get_orm(s, body.agent_name)
            draft = await DraftService.find_by_name(s, agent.id, body.draft_name)
            if draft is None:
                raise NotFound(
                    f"草稿不存在: {body.agent_name}/{body.draft_name}", {"draft_name": body.draft_name}
                )
            found: list[tuple[Asset, AssetRevision, str]] = []
            missing: list[str] = []
            for key, path in reverse.items():
                kind, name = parse_asset_key(key)
                asset = (
                    await s.execute(select(Asset).where(Asset.kind == kind, Asset.name == name))
                ).scalar_one_or_none()
                rev = None
                if asset is not None and asset.deleted_at is None:
                    rev = (
                        await s.execute(
                            select(AssetRevision).where(
                                AssetRevision.asset_id == asset.id, AssetRevision.rev == asset.latest_rev
                            )
                        )
                    ).scalar_one_or_none()
                if asset is None or rev is None:
                    missing.append(key)
                else:
                    found.append((asset, rev, path))
            draft_dto = draft_out(draft)
            agent_id = agent.id
        items: list[dict[str, Any]] = []
        for asset, rev, path in found:
            data = await self.ctx.blobs.get_bytes(rev.blob_hash)
            try:
                content, encoding = data.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                content, encoding = base64.b64encode(data).decode("ascii"), "base64"
            items.append(
                {
                    "asset": f"{asset.kind}/{asset.name}",
                    "path": path,
                    "rev": rev.rev,
                    "hash": rev.blob_hash,
                    "content_type": rev.content_type,
                    "encoding": encoding,
                    "content": content,
                }
            )
        return PullOut(
            agent_id=agent_id,
            draft=draft_dto,
            local_spec=restore_file_refs(draft_dto.spec, reverse),
            assets=items,
            missing=missing,
        )
