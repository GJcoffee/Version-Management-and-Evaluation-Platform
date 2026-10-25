import pytest
from evalp.api.pagination import MAX_LIMIT, Page, paginate
from evalp.ids import new_id
from evalp.store.models import Setting
from evalp.utils.time import now_iso
from sqlalchemy import select


async def make_providers(client, n):
    ids = []
    for i in range(n):
        r = await client.post("/api/v1/providers", json={"name": f"p{i:03d}", "base_url": "http://x"})
        assert r.status_code == 201
        ids.append(r.json()["id"])
    return ids


async def test_pages_cover_everything_newest_first(client):
    ids = await make_providers(client, 7)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/api/v1/providers", params=params)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"items", "next_cursor"}
        seen += [it["id"] for it in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert cursor == body["items"][-1]["id"]  # cursor 为上一页最后一条的 id
    assert pages == 3
    assert seen == list(reversed(ids))  # ULID 有序 -> 最新在前


async def test_exact_multiple_has_no_next_cursor(client):
    await make_providers(client, 4)
    r = await client.get("/api/v1/providers", params={"limit": 4})
    assert len(r.json()["items"]) == 4 and r.json()["next_cursor"] is None


async def test_default_limit_and_bounds(client):
    r = await client.get("/api/v1/providers")
    assert r.json() == {"items": [], "next_cursor": None}
    for bad in (0, -1, MAX_LIMIT + 1):
        r = await client.get("/api/v1/providers", params={"limit": bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await client.get("/api/v1/providers", params={"limit": MAX_LIMIT})).status_code == 200


async def test_default_limit_is_50(client, ctx):
    from evalp.api.pagination import DEFAULT_LIMIT

    assert DEFAULT_LIMIT == 50 and MAX_LIMIT == 200


@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_paginate_helper_order(ctx, order):
    async with ctx.db.write() as s:
        for i in range(5):
            s.add(Setting(key=new_id("job"), value=str(i)))
    now_iso()
    async with ctx.db.read() as s:
        p1 = await paginate(
            s, select(Setting), Setting.key, limit=2, order=order, transform=lambda r: r.value
        )
        assert isinstance(p1, Page) and len(p1.items) == 2 and p1.next_cursor
        p2 = await paginate(
            s,
            select(Setting),
            Setting.key,
            limit=2,
            cursor=p1.next_cursor,
            order=order,
            transform=lambda r: r.value,
        )
        p3 = await paginate(
            s,
            select(Setting),
            Setting.key,
            limit=2,
            cursor=p2.next_cursor,
            order=order,
            transform=lambda r: r.value,
        )
    values = p1.items + p2.items + p3.items
    assert p3.next_cursor is None
    assert values == (["0", "1", "2", "3", "4"] if order == "asc" else ["4", "3", "2", "1", "0"])
