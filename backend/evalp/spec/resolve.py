"""引用固化（详设 4.4 第 3 步）。

``resolve_spec``：把草稿里的 ``@latest`` 固化为具体 rev，并把引用替换为
``{"$asset": {kind, name, rev, hash}}``；同时收集 ``(asset_revision_id, spec_path)``，用于写
``version_asset_refs``。引用不存在或资产已软删除 → SpecInvalid（``resolve_spec`` 本身把问题收集到
``errors`` 里，不抛异常；``lookup_revision`` 抛 SpecInvalid）。

``unresolve``：固化引用 -> ``asset://kind/name@rev`` 字符串（用于 versions/{id}/to-draft）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evalp.errors import SpecInvalid
from evalp.spec.models import CodeSpec, ConfigSpec, dump_spec
from evalp.spec.refs import (
    AssetRef,
    format_asset_ref,
    is_pin,
    make_pin,
    node_ref,
    pin_to_ref,
)
from evalp.store.models import Asset, AssetRevision
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ResolvedAsset:
    """一个具体的资产修订（引用固化后的结果）。``hash`` 是该修订内容（blob）的 sha256。"""

    kind: str
    name: str
    rev: int
    hash: str
    revision_id: str
    asset_id: str

    @property
    def pin(self) -> dict[str, Any]:
        """Spec 中的固化形式 ``{"$asset": {kind,name,rev,hash}}``。"""
        return make_pin(self.kind, self.name, self.rev, self.hash)

    @classmethod
    def of(cls, asset: Asset, revision: AssetRevision) -> ResolvedAsset:
        return cls(asset.kind, asset.name, revision.rev, revision.blob_hash, revision.id, asset.id)


@dataclass(frozen=True)
class RefUse(ResolvedAsset):
    """Spec 中一处资产引用的固化结果（spec_path 如 ``prompts.system`` / ``tools[2]``）。"""

    spec_path: str = ""


@dataclass
class ResolveResult:
    resolved: dict[str, Any]
    refs: list[RefUse] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def astuple_of(r: ResolvedAsset) -> tuple[Any, ...]:
    return (r.kind, r.name, r.rev, r.hash, r.revision_id, r.asset_id)


async def lookup_revision(s: AsyncSession, ref: AssetRef) -> tuple[Asset, AssetRevision]:
    """按引用查找资产与修订；资产不存在、已软删除或修订不存在时抛 SpecInvalid。"""
    asset = (
        await s.execute(select(Asset).where(Asset.kind == ref.kind, Asset.name == ref.name))
    ).scalar_one_or_none()
    if asset is None:
        raise SpecInvalid(f"资产不存在: {ref.kind}/{ref.name}", {"ref": str(ref)})
    if asset.deleted_at is not None:
        raise SpecInvalid(f"资产已被删除: {ref.kind}/{ref.name}", {"ref": str(ref)})
    rev = asset.latest_rev if ref.rev is None else ref.rev
    revision = (
        await s.execute(
            select(AssetRevision).where(AssetRevision.asset_id == asset.id, AssetRevision.rev == rev)
        )
    ).scalar_one_or_none()
    if revision is None:
        raise SpecInvalid(f"资产修订不存在: {ref.kind}/{ref.name}@{rev}", {"ref": str(ref)})
    return asset, revision


async def resolve_spec(s: AsyncSession, spec: ConfigSpec | CodeSpec) -> ResolveResult:
    """固化 Spec 中的全部资产引用。出错的引用保持原样，并记录到 ``errors``。"""
    data = dump_spec(spec)
    result = ResolveResult(resolved=data)

    async def one(node: Any, path: str) -> Any:
        try:
            ref = node_ref(node)
        except ValueError as e:  # 理论上模型已校验过
            result.errors.append({"path": path, "message": str(e)})
            return node
        if ref is None:
            return node  # 内联文本
        try:
            asset, revision = await lookup_revision(s, ref)
        except SpecInvalid as e:
            result.errors.append({"path": path, "message": e.message})
            return node
        use = RefUse(*astuple_of(ResolvedAsset.of(asset, revision)), spec_path=path)
        result.refs.append(use)
        return use.pin

    if isinstance(spec, CodeSpec):
        if data["snapshot"] is not None:
            files = data["snapshot"]["files"]
            for rel in files:
                files[rel] = await one(files[rel], f"snapshot.files.{rel}")
        return result

    prompts = data["prompts"]
    prompts["system"] = await one(prompts["system"], "prompts.system")
    tools = data["tools"]
    for i, tool in enumerate(tools):
        if isinstance(tool, dict) and "builtin" in tool:
            sources = tool["with"]["sources"]
            for j, src in enumerate(sources):
                sources[j] = await one(src, f"tools[{i}].with.sources[{j}]")
        else:
            tools[i] = await one(tool, f"tools[{i}]")
    inject = data["knowledge"]["inject"]
    for k, node in enumerate(inject):
        inject[k] = await one(node, f"knowledge.inject[{k}]")
    return result


def unresolve(obj: Any) -> Any:
    """把固化引用 ``{"$asset": …}`` 还原为 ``asset://kind/name@rev`` 字符串（rev 保持原样）。"""
    if is_pin(obj):
        return pin_to_ref(obj)
    if isinstance(obj, dict):
        return {k: unresolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unresolve(v) for v in obj]
    return obj


def iter_pins(obj: Any, path: str = ""):
    """遍历 Spec 中的全部固化引用，产出 ``(spec_path, pin_dict)``。"""
    if is_pin(obj):
        yield path, obj["$asset"]
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_pins(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_pins(v, f"{path}[{i}]")


__all__ = [
    "RefUse",
    "ResolvedAsset",
    "ResolveResult",
    "format_asset_ref",
    "iter_pins",
    "lookup_revision",
    "resolve_spec",
    "unresolve",
]
