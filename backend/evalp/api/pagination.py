"""分页（详设 11.1）：``?limit=50&cursor={上一页最后一条的 id}``，响应 ``{items, next_cursor}``。

基于 ULID 的有序性做 keyset 分页：默认按 id 降序（最新在前），``order="asc"`` 为升序。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    """分页响应。"""

    items: list[T]
    next_cursor: str | None = None


@dataclass
class PageParams:
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None


def page_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="每页条数（1-200）"),
    cursor: str | None = Query(None, description="上一页最后一条的 id"),
) -> PageParams:
    """路由依赖：``p: PageParams = Depends(page_params)``。"""
    return PageParams(limit=limit, cursor=cursor)


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    id_col: InstrumentedAttribute[str],
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    transform: Callable[[Any], T] | None = None,
) -> Page[T]:
    """对 ORM 实体查询做 keyset 分页。

    stmt 是 ``select(Model)...``（可带 where，不要带 order_by / limit）；id_col 是主键列（如 ``Agent.id``）；
    transform 把 ORM 对象转为 DTO。多取一条用于判断是否还有下一页。
    """
    limit = max(1, min(limit, MAX_LIMIT))
    if cursor:
        stmt = stmt.where(id_col > cursor if order == "asc" else id_col < cursor)
    stmt = stmt.order_by(id_col.asc() if order == "asc" else id_col.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = getattr(rows[-1], id_col.key) if has_more and rows else None
    items = [transform(r) if transform else r for r in rows]
    return Page(items=items, next_cursor=next_cursor)
