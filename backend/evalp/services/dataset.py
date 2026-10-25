"""数据集服务（详设 5.3、5.4）：导入、用例查询、修订 diff。

供评测模块使用：

- ``list_cases(dataset_rev_id, filter)``：按 idx 顺序返回用例（可按 tags / case_keys 过滤）；
- ``resolve_dataset("asset://dataset/x@latest")``：把引用解析为具体修订（含 ``revision_id``）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import NotFound, SpecInvalid
from evalp.schemas.asset import AssetCreate, CaseFilter, CaseOut, DatasetImport, DatasetImportOut
from evalp.services.asset import AssetService
from evalp.spec.refs import AssetRef, parse_asset_ref
from evalp.spec.resolve import ResolvedAsset
from evalp.store.models import Asset, AssetRevision, EvalCase

MAX_DIFF_DETAIL = 500
CASE_FIELDS = ("input", "expected", "rubric", "tags", "metadata")
_CONTENT_TYPE = {"jsonl": "application/jsonl", "csv": "text/csv"}


def case_out(c: EvalCase) -> CaseOut:
    """ORM -> DTO。"""
    return CaseOut(
        id=c.id,
        dataset_rev_id=c.dataset_rev_id,
        case_key=c.case_key,
        idx=c.idx,
        input=c.input,
        expected=c.expected,
        rubric=c.rubric,
        tags=c.tags or [],
        metadata=c.metadata_ or {},
        case_hash=c.case_hash,
    )


def _coerce_filter(flt: CaseFilter | dict[str, Any] | None) -> CaseFilter:
    if flt is None:
        return CaseFilter()
    if isinstance(flt, dict):
        return CaseFilter(tags=flt.get("tags") or [], case_keys=flt.get("case_keys") or [])
    return flt


def _apply_filter(stmt: Any, flt: CaseFilter) -> Any:
    if flt.case_keys:
        stmt = stmt.where(EvalCase.case_key.in_(flt.case_keys))
    if flt.tags:
        je = func.json_each(cast(EvalCase.tags, Text)).table_valued("value")
        stmt = stmt.where(select(je.c.value).where(je.c.value.in_(flt.tags)).exists())
    return stmt


class DatasetService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def import_dataset(self, body: DatasetImport) -> DatasetImportOut:
        """导入 JSONL / CSV：创建数据集资产（已存在则新增修订）并解析出用例。"""
        res = await self.ctx.svc(AssetService).create(
            AssetCreate(
                kind="dataset",
                name=body.name,
                description=body.description,
                content=body.content,
                content_type=_CONTENT_TYPE[body.format],
                message=body.message,
                column_map=body.column_map,
            )
        )
        meta = res.revision.meta
        return DatasetImportOut(
            asset=res.asset,
            revision=res.revision,
            case_count=int(meta.get("case_count", 0)),
            warnings=list(meta.get("schema_warnings", [])),
            existed=res.existed,
            created=res.created,
        )

    async def resolve_dataset(
        self, ref: str | AssetRef, *, session: AsyncSession | None = None
    ) -> ResolvedAsset:
        """解析 ``asset://dataset/x@latest|rev`` 为具体的数据集修订；类型不是 dataset 时抛 SpecInvalid。"""
        parsed = parse_asset_ref(ref) if isinstance(ref, str) else ref
        if parsed.kind != "dataset":
            raise SpecInvalid(f"需要 dataset 类型的资产引用: {parsed}", {"ref": str(parsed)})
        return await self.ctx.svc(AssetService).resolve_ref(parsed, session=session)

    async def _ensure_dataset_rev(self, s: AsyncSession, dataset_rev_id: str) -> None:
        row = (
            await s.execute(
                select(Asset.kind)
                .join(AssetRevision, AssetRevision.asset_id == Asset.id)
                .where(AssetRevision.id == dataset_rev_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound(f"数据集修订不存在: {dataset_rev_id}", {"id": dataset_rev_id})
        if row != "dataset":
            raise NotFound(f"该修订不是数据集: {dataset_rev_id}", {"id": dataset_rev_id})

    async def list_cases(
        self,
        dataset_rev_id: str,
        flt: CaseFilter | dict[str, Any] | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> list[CaseOut]:
        """返回数据集修订的全部用例（按 idx 升序）。

        flt: ``{"tags": [...], "case_keys": [...]}``，两者同时给出时取交集，tags 为"含任一"。
        """
        f = _coerce_filter(flt)

        async def run(s: AsyncSession) -> list[CaseOut]:
            await self._ensure_dataset_rev(s, dataset_rev_id)
            stmt = _apply_filter(select(EvalCase).where(EvalCase.dataset_rev_id == dataset_rev_id), f)
            rows = (await s.execute(stmt.order_by(EvalCase.idx))).scalars().all()
            return [case_out(c) for c in rows]

        if session is not None:
            return await run(session)
        async with self.ctx.db.read() as s:
            return await run(s)

    async def page_cases(
        self,
        dataset_rev_id: str,
        flt: CaseFilter | None = None,
        *,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[CaseOut]:
        """分页返回用例（按 idx 升序；``q`` 在 case_key 中模糊匹配）。"""
        f = _coerce_filter(flt)
        async with self.ctx.db.read() as s:
            await self._ensure_dataset_rev(s, dataset_rev_id)
            stmt = _apply_filter(select(EvalCase).where(EvalCase.dataset_rev_id == dataset_rev_id), f)
            if q:
                stmt = stmt.where(EvalCase.case_key.like(f"%{q}%"))
            # 用例 id 在导入时按 idx 顺序生成（ULID 单调递增），所以按 id 升序即 idx 升序
            return await paginate(
                s, stmt, EvalCase.id, limit=limit, cursor=cursor, order="asc", transform=case_out
            )

    async def get_case(self, case_id: str) -> CaseOut:
        """按用例 id 获取。"""
        async with self.ctx.db.read() as s:
            c = await s.get(EvalCase, case_id)
            if c is None:
                raise NotFound(f"用例不存在: {case_id}", {"id": case_id})
            return case_out(c)

    async def case_count(self, dataset_rev_id: str) -> int:
        """修订的用例总数。"""
        async with self.ctx.db.read() as s:
            return (
                await s.execute(
                    select(func.count())
                    .select_from(EvalCase)
                    .where(EvalCase.dataset_rev_id == dataset_rev_id)
                )
            ).scalar_one()

    async def diff_revisions(self, left_rev_id: str, right_rev_id: str) -> dict[str, Any]:
        """按 case_key 对比两个数据集修订：增加 / 删除 / 修改的用例数及明细。"""
        async with self.ctx.db.read() as s:
            await self._ensure_dataset_rev(s, left_rev_id)
            await self._ensure_dataset_rev(s, right_rev_id)
            lc = {c.case_key: case_out(c) for c in await self._all(s, left_rev_id)}
            rc = {c.case_key: case_out(c) for c in await self._all(s, right_rev_id)}
        added = [k for k in rc if k not in lc]
        removed = [k for k in lc if k not in rc]
        modified: list[dict[str, Any]] = []
        unchanged = 0
        for key, left in lc.items():
            right = rc.get(key)
            if right is None:
                continue
            changed = [f for f in CASE_FIELDS if getattr(left, f) != getattr(right, f)]
            if changed:
                modified.append(
                    {
                        "case_key": key,
                        "changed_fields": changed,
                        "left": {f: getattr(left, f) for f in changed},
                        "right": {f: getattr(right, f) for f in changed},
                    }
                )
            else:
                unchanged += 1
        summary = {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": unchanged,
            "left_count": len(lc),
            "right_count": len(rc),
        }
        return {
            "summary": summary,
            "added": [
                {"case_key": k, "input": rc[k].input, "tags": rc[k].tags} for k in added[:MAX_DIFF_DETAIL]
            ],
            "removed": [
                {"case_key": k, "input": lc[k].input, "tags": lc[k].tags} for k in removed[:MAX_DIFF_DETAIL]
            ],
            "modified": modified[:MAX_DIFF_DETAIL],
            "truncated": len(added) > MAX_DIFF_DETAIL
            or len(removed) > MAX_DIFF_DETAIL
            or len(modified) > MAX_DIFF_DETAIL,
        }

    @staticmethod
    async def _all(s: AsyncSession, rev_id: str) -> list[EvalCase]:
        return list(
            (
                await s.execute(
                    select(EvalCase).where(EvalCase.dataset_rev_id == rev_id).order_by(EvalCase.idx)
                )
            )
            .scalars()
            .all()
        )
