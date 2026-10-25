"""EventSink：先发布后落库、批量写入、立即 flush、blob 转存、脱敏、失败重试。"""

import asyncio
import hashlib
import json

import pytest
from evalp.runtime.events import ev
from evalp.services.event_sink import BLOB_KEY, EventSink
from evalp.store.models import Event, Session, Turn
from evalp.utils.time import now_iso
from sqlalchemy import func, select

from .conftest import API


async def make_turn(ctx, world, idx=0):
    """建一个会话和一个 running 的轮次（events 外键需要）。"""
    v = await world.config_version()
    s = await world.session(v)
    async with ctx.db.write() as db:
        db.add(
            Turn(id=f"trn_t{idx}", session_id=s.id, idx=idx, status="running",
                 input={"role": "user", "content": "x"}, usage={}, cost=0.0, started_at=now_iso())
        )  # fmt: skip
    return s


async def rows(ctx, session_id):
    async with ctx.db.read() as db:
        return (
            (await db.execute(select(Event).where(Event.session_id == session_id).order_by(Event.seq)))
            .scalars()
            .all()
        )


async def count(ctx, session_id):
    async with ctx.db.read() as db:
        return (
            await db.execute(select(func.count()).select_from(Event).where(Event.session_id == session_id))
        ).scalar_one()


async def last_seq(ctx, session_id):
    async with ctx.db.read() as db:
        return (await db.get(Session, session_id)).last_seq


def log(i):
    return ev("log", message=f"日志 {i}", turn_id="trn_t0")


async def test_batches_by_count_and_updates_last_seq(ctx, world):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_batch = 100
    ctx.config.events.flush_interval_ms = 60_000  # 关闭定时 flush 的干扰
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    for i in range(99):
        await sink.emit(log(i))
    assert sink.flushes == 0 and sink.pending == 99 and await count(ctx, s.id) == 0
    await sink.emit(log(99))  # 第 100 条触发批量写入
    assert sink.flushes == 1 and sink.pending == 0 and await count(ctx, s.id) == 100
    assert await last_seq(ctx, s.id) == 100
    for i in range(30):
        await sink.emit(log(i))
    assert await count(ctx, s.id) == 100  # 还在缓冲
    await sink.close()  # close 做最后一次 flush
    assert await count(ctx, s.id) == 130 and await last_seq(ctx, s.id) == 130
    assert [r.seq for r in await rows(ctx, s.id)] == list(range(1, 131))


async def test_timer_flushes_small_batches(ctx, world):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_interval_ms = 50
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    await sink.emit(log(1))
    await sink.emit(log(2))
    assert await count(ctx, s.id) == 0  # 发布了，但还没落库
    for _ in range(40):
        if await count(ctx, s.id) == 2:
            break
        await asyncio.sleep(0.025)
    assert await count(ctx, s.id) == 2
    await sink.emit(log(3))
    for _ in range(40):
        if await count(ctx, s.id) == 3:
            break
        await asyncio.sleep(0.025)
    assert await count(ctx, s.id) == 3
    await sink.close()


@pytest.mark.parametrize("etype", ["turn.end", "error"])
async def test_turn_end_and_error_flush_immediately(ctx, world, etype):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_interval_ms = 60_000
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    await sink.emit(log(1))
    event = ev("turn.end", status="completed") if etype == "turn.end" else ev("error", code="X", message="m")
    await sink.emit(event)
    assert await count(ctx, s.id) == 2 and sink.pending == 0
    await sink.close()


async def test_publish_before_persist_and_delta_not_stored(ctx, world):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_interval_ms = 60_000
    sink = EventSink(ctx, s.id, 41, turn_id="trn_t0")
    sub = ctx.bus.subscribe(f"session:{s.id}")
    await sink.emit(ev("llm.delta", step_id="s1", text="你"))
    await sink.emit(log(1))
    assert await count(ctx, s.id) == 0  # 总线上已经有了，库里还没有
    got = [await sub.get(), await sub.get()]
    assert [(e.type, e.seq) for e in got] == [("llm.delta", None), ("log", 42)]
    assert got[1].turn_id == "trn_t0"
    await sink.close()
    sub.close()
    assert [r.seq for r in await rows(ctx, s.id)] == [42] and await last_seq(ctx, s.id) == 42
    assert sink.last_seq == 42


async def test_large_payload_goes_to_blob(ctx, world):
    s = await make_turn(ctx, world)
    ctx.config.events.payload_inline_limit_kb = 1
    big = "大" * 5000
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    sub = ctx.bus.subscribe(f"session:{s.id}")
    await sink.emit(ev("tool.call", call_id="c1", name="search", arguments={"q": big}, step_id="s2"))
    await sink.emit(
        ev("tool.result", call_id="c1", name="search", output={"text": big}, step_id="s2", latency_ms=12)
    )
    await sink.emit(log(1))
    await sink.close()
    live = [await sub.get() for _ in range(3)]
    sub.close()
    assert live[0].payload["arguments"] == {"q": big}  # 总线上是完整内容
    r = await rows(ctx, s.id)
    stub = r[0].payload
    assert set(stub) == {BLOB_KEY, "call_id", "name"}  # 大字段不在库里，体积小的顶层字段保留
    assert stub["name"] == "search" and stub["call_id"] == "c1"
    assert r[1].payload[BLOB_KEY] and r[1].payload["latency_ms"] == 12 and r[1].payload["is_error"] is False
    assert r[2].payload == {"level": "info", "message": "日志 1"}  # 小 payload 原样内联
    # blob 内容是完整 payload，哈希对得上，引用计数为 1
    raw = await ctx.blobs.get_bytes(stub[BLOB_KEY])
    assert hashlib.sha256(raw).hexdigest() == stub[BLOB_KEY]
    assert json.loads(raw)["arguments"] == {"q": big}
    assert await ctx.blobs.refcount(stub[BLOB_KEY]) == 1
    # 服务层展开
    events = await world.sessions.get_events(s.id, resolve_blobs=True)
    assert events[0].payload["arguments"] == {"q": big} and events[2].payload["message"] == "日志 1"
    # 步骤签名不需要读 blob
    from evalp.runtime.events import step_signature

    raw_events = await world.sessions.get_events(s.id)
    assert step_signature(raw_events[0]) == "tool:search"


async def test_blob_api_access_control(ctx, world, client):
    s = await make_turn(ctx, world)
    ctx.config.events.payload_inline_limit_kb = 1
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    await sink.emit(ev("tool.result", call_id="c", name="n", output="x" * 3000))
    await sink.close()
    h = (await rows(ctx, s.id))[0].payload[BLOB_KEY]
    r = await client.get(f"{API}/blobs/{h}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.json()["output"] == "x" * 3000
    assert (await client.get(f"{API}/blobs/{h}", params={"session_id": s.id})).status_code == 200
    assert (await client.get(f"{API}/blobs/{h}", params={"session_id": "ses_other"})).status_code == 404
    # 未被引用的 blob 不可读
    orphan = await ctx.blobs.put_bytes(b"secret orphan")
    assert (await client.get(f"{API}/blobs/{orphan}")).status_code == 404
    assert (await client.get(f"{API}/blobs/{'0' * 64}")).status_code == 404
    assert (await client.get(f"{API}/blobs/not-a-hash")).status_code == 400
    # 资产内容的 blob 可读（作为附件下载）
    asset = await world.api.asset("prompt", "p1", "提示词内容")
    r = await client.get(f"{API}/blobs/{asset['revision']['blob_hash']}")
    assert r.status_code == 200 and r.content.decode() == "提示词内容"
    assert (
        "attachment" in r.headers["content-disposition"]
        and r.headers["content-type"] == "application/octet-stream"
    )


async def test_redaction_before_publish_and_persist(ctx, world):
    s = await make_turn(ctx, world)
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    sub = ctx.bus.subscribe(f"session:{s.id}")
    payload = {
        "provider": "p", "model": "m", "params": {"max_tokens": 10, "api_key": "sk-abcdefghijklmnopqrstuvwx"},
        "messages": [{"role": "user", "content": "我的密钥是 sk-abcdefghijklmnopqrstuvwxyz0123 请记住"}],
        "headers": {"Authorization": "Bearer abcdef1234567890", "X-Api-Token": "t0k3n", "Accept": "json"},
    }  # fmt: skip
    await sink.emit(ev("llm.request", **payload))
    await sink.emit(ev("tool.call", call_id="c", name="http", arguments={"password": "p@ss", "q": "ok"}))
    await sink.close()
    live = await sub.get()
    sub.close()
    stored = (await rows(ctx, s.id))[0].payload
    text = json.dumps(stored, ensure_ascii=False) + json.dumps(live.payload, ensure_ascii=False)
    for leaked in ("sk-abcdefghijklmnopqrstuvwx", "abcdef1234567890", "t0k3n"):
        assert leaked not in text
    assert stored["params"] == {"max_tokens": 10, "api_key": "***"}  # max_tokens 不受影响
    assert stored["headers"]["Authorization"] == "***" and stored["headers"]["Accept"] == "json"
    assert stored["messages"][0]["content"] == "我的密钥是 *** 请记住"
    assert (await rows(ctx, s.id))[1].payload["arguments"] == {"password": "***", "q": "ok"}


async def test_write_failure_keeps_buffer_and_retries_without_duplicates(ctx, world, monkeypatch):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_interval_ms = 60_000
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    original = sink._write
    calls = {"n": 0}

    async def flaky(batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("磁盘暂时不可用")
        await original(batch)

    monkeypatch.setattr(sink, "_write", flaky)
    await sink.emit(log(1))
    await sink.emit(ev("error", code="X", message="m"))  # 立即 flush，第一次失败但不抛异常
    assert sink.pending == 2 and await count(ctx, s.id) == 0
    await sink.emit(log(2))
    await sink.close()  # 重试成功
    assert [r.seq for r in await rows(ctx, s.id)] == [1, 2, 3] and await last_seq(ctx, s.id) == 3


async def test_cancel_during_flush_does_not_lose_or_duplicate(ctx, world):
    s = await make_turn(ctx, world)
    ctx.config.events.flush_interval_ms = 60_000
    sink = EventSink(ctx, s.id, 0, turn_id="trn_t0")
    for i in range(5):
        await sink.emit(log(i))
    task = asyncio.create_task(sink.flush())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await sink.close()
    assert [r.seq for r in await rows(ctx, s.id)] == [1, 2, 3, 4, 5]
