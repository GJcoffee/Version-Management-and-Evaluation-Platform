import asyncio

import pytest
from evalp.store.models import Provider, Setting
from sqlalchemy import text


async def test_pragmas(ctx):
    async with ctx.db.write() as s:
        jm = (await s.execute(text("pragma journal_mode"))).scalar()
        sync = (await s.execute(text("pragma synchronous"))).scalar()
        fk = (await s.execute(text("pragma foreign_keys"))).scalar()
        bt = (await s.execute(text("pragma busy_timeout"))).scalar()
    assert (jm, sync, fk, bt) == ("wal", 1, 1, 5000)  # synchronous=NORMAL -> 1
    async with ctx.db.read() as s:
        assert (await s.execute(text("pragma foreign_keys"))).scalar() == 1
        assert (await s.execute(text("pragma query_only"))).scalar() == 1


async def test_write_commits_and_read_sees(ctx):
    async with ctx.db.write() as s:
        s.add(Setting(key="k", value="1"))
    async with ctx.db.read() as s:
        assert (await s.get(Setting, "k")).value == "1"


async def test_write_rolls_back_on_error(ctx):
    with pytest.raises(RuntimeError):
        async with ctx.db.write() as s:
            s.add(Setting(key="k", value="1"))
            await s.flush()
            raise RuntimeError("boom")
    async with ctx.db.read() as s:
        assert await s.get(Setting, "k") is None
    # 回滚后写锁已释放
    async with ctx.db.write() as s:
        s.add(Setting(key="k2", value="2"))


async def test_read_session_cannot_write(ctx):
    with pytest.raises(Exception, match="readonly|read-only|query_only"):
        async with ctx.db.read() as s:
            s.add(Setting(key="x", value="1"))
            await s.commit()


async def test_write_lock_serializes(ctx):
    active = 0
    max_active = 0
    order: list[int] = []

    async def worker(i: int):
        nonlocal active, max_active
        async with ctx.db.write():
            active += 1
            max_active = max(max_active, active)
            order.append(i)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker(i) for i in range(10)))
    assert max_active == 1
    assert sorted(order) == list(range(10))


async def test_concurrent_read_modify_write_is_consistent(ctx):
    async with ctx.db.write() as s:
        s.add(Setting(key="counter", value="0"))

    async def incr():
        async with ctx.db.write() as s:
            row = await s.get(Setting, "counter")
            await asyncio.sleep(0)  # 让出控制权，若无锁会丢更新
            row.value = str(int(row.value) + 1)

    await asyncio.gather(*(incr() for _ in range(30)))
    async with ctx.db.read() as s:
        assert (await s.get(Setting, "counter")).value == "30"


async def test_reads_not_blocked_by_write_lock(ctx):
    async with ctx.db.write():
        async with ctx.db.read() as s:  # 持有写锁时读取不会死锁
            assert (await s.execute(text("select 1"))).scalar() == 1


async def test_json_text_roundtrip_chinese(ctx):
    async with ctx.db.write() as s:
        s.add(
            Provider(
                id="prv_1", name="p", base_url="http://x", headers={"X-名称": "值"},
                models=[{"name": "模型", "price_in": 1.5}], created_at="t", updated_at="t",
            )
        )  # fmt: skip
    async with ctx.db.read() as s:
        raw = (await s.execute(text("select headers, models from providers"))).one()
        p = await s.get(Provider, "prv_1")
    assert "X-名称" in raw[0] and "\\u" not in raw[0]  # ensure_ascii=False
    assert p.headers == {"X-名称": "值"} and p.models == [{"name": "模型", "price_in": 1.5}]


async def test_json_defaults(ctx):
    async with ctx.db.write() as s:
        s.add(Provider(id="prv_2", name="q", base_url="u", created_at="t", updated_at="t"))
    async with ctx.db.read() as s:
        p = await s.get(Provider, "prv_2")
    assert p.headers == {} and p.models == [] and p.max_concurrency == 8 and p.rpm_limit is None


async def test_file_size(ctx):
    assert ctx.db.file_size() > 0
