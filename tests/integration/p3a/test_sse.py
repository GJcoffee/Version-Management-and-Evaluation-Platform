"""SSE：回放 + 实时订阅不重复不丢失、Last-Event-ID / after_seq、idle、慢订阅者被断开。"""

import asyncio
import json
import random

import httpx
import pytest
from evalp.runtime.events import AgentEvent
from evalp.services.session import Idle

from tests.fixtures import sdk_agents
from tests.fixtures.fake_openai import StreamingASGITransport


@pytest.fixture
def sdk_server():
    handle = sdk_agents.handle.start_http(0)
    yield handle
    handle.close()


async def collect(svc, session_id, after_seq=0, *, until_idle=True):
    items = []
    async for item in svc.stream(session_id, after_seq):
        items.append(item)
    return items


def seqs(items):
    return [i.seq for i in items if isinstance(i, AgentEvent) and i.seq is not None]


async def wait_running(world, session_id):
    for _ in range(250):
        if world.sessions.running_turn_id(session_id):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("轮次没有开始")


async def test_replay_finished_session_then_idle(world, fake_openai):
    v = await world.config_version()
    fake_openai.enqueue("一", "二")
    s, _ = await world.chat(v, "问一")
    await world.sessions.run_turn(s.id, "问二")
    all_events = await world.events(s.id)
    items = await collect(world.sessions, s.id)
    assert isinstance(items[-1], Idle) and items[-1].last_seq == all_events[-1].seq
    assert seqs(items) == [e.seq for e in all_events]
    # after_seq：只给之后的
    tail = await collect(world.sessions, s.id, after_seq=5)
    assert seqs(tail) == [e.seq for e in all_events if e.seq > 5]
    # 已到末尾：直接 idle
    end = await collect(world.sessions, s.id, after_seq=all_events[-1].seq)
    assert len(end) == 1 and isinstance(end[0], Idle) and end[0].last_seq == all_events[-1].seq
    # 空会话
    empty = await world.session(v)
    items = await collect(world.sessions, empty.id)
    assert len(items) == 1 and isinstance(items[0], Idle) and items[0].last_seq == 0


async def test_live_stream_with_deltas(world, fake_openai):
    v = await world.config_version()
    fake_openai.slow_first_token(0.3, times=1)
    fake_openai.enqueue("你好，这是一段流式输出的回复内容")
    s = await world.session(v)
    turn = await world.sessions.run_turn(s.id, "hi", wait=False)
    items = await asyncio.wait_for(collect(world.sessions, s.id), 10)
    assert isinstance(items[-1], Idle)
    events = [i for i in items if isinstance(i, AgentEvent)]
    types = [e.type for e in events]
    assert types[0] == "turn.start" and types[-1] == "turn.end"
    assert "llm.delta" in types
    assert all(e.seq is None for e in events if e.type == "llm.delta")
    persisted = await world.events(s.id)
    assert [e.seq for e in events if e.seq is not None] == [e.seq for e in persisted]
    assert "llm.delta" not in [e.type for e in persisted]
    assert (await world.sessions.get_turn(turn.id)).status == "ok"


async def test_live_stream_from_middle_after_seq(world, fake_openai):
    v = await world.config_version()
    fake_openai.slow(0.3, times=1)
    fake_openai.enqueue("答")
    s = await world.session(v)
    await world.sessions.run_turn(s.id, "hi", wait=False)
    items = await asyncio.wait_for(collect(world.sessions, s.id, after_seq=2), 10)
    got = seqs(items)
    assert got and got[0] == 3 and got == list(range(3, got[-1] + 1))
    assert got[-1] == (await world.events(s.id))[-1].seq


async def test_replay_live_no_dup_no_loss_race(world, sdk_server):
    """订阅者在事件持续写入的不同时刻接入：每个订阅者看到的 seq 恰好是 1..N，无重复无缺口。"""
    v = await world.http_version(sdk_server.url)
    s = await world.session(v)
    n = 300
    turn = await world.sessions.run_turn(s.id, f"burst:{n}", wait=False)
    rnd = random.Random(7)

    async def late_subscriber(delay):
        await asyncio.sleep(delay)
        return await asyncio.wait_for(collect(world.sessions, s.id), 20)

    delays = [0.0, 0.0, *[rnd.uniform(0.0, 0.25) for _ in range(10)]]
    results = await asyncio.gather(*(late_subscriber(d) for d in delays))
    final = await world.events(s.id)
    total = [e.seq for e in final]
    assert total == list(range(1, len(total) + 1)) and len(total) > 2 * n
    for items in results:
        assert isinstance(items[-1], Idle)
        got = seqs(items)
        assert got == total, f"订阅者看到 {len(got)} 个事件，实际 {len(total)}"
    assert (await world.sessions.get_turn(turn.id)).status == "ok"
    assert world.ctx.bus.subscriber_count() == 0


async def test_resume_with_last_seq_has_no_gap(world, sdk_server):
    """中途断开后用最后收到的 seq 续传：两段拼起来恰好是完整序列。"""
    v = await world.http_version(sdk_server.url)
    s = await world.session(v)
    await world.sessions.run_turn(s.id, "burst:200", wait=False)
    first: list[int] = []
    async for item in world.sessions.stream(s.id, 0):
        if isinstance(item, AgentEvent) and item.seq is not None:
            first.append(item.seq)
            if len(first) == 40:
                break
    rest = await asyncio.wait_for(collect(world.sessions, s.id, after_seq=first[-1]), 20)
    total = [e.seq for e in await world.events(s.id)]
    assert first + seqs(rest) == total
    await asyncio.sleep(0.05)
    assert world.ctx.bus.subscriber_count() == 0


async def test_slow_subscriber_disconnected_and_can_resume(world, sdk_server):
    world.ctx.bus.capacity = 20
    v = await world.http_version(sdk_server.url)
    s = await world.session(v)
    await world.sessions.run_turn(s.id, "burst:200", wait=False)
    got: list[int] = []
    ended_with_idle = False
    async for item in world.sessions.stream(s.id, 0):
        if isinstance(item, Idle):
            ended_with_idle = True
            break
        if item.seq is not None:
            got.append(item.seq)
            if len(got) == 1:
                await asyncio.sleep(0.5)  # 不消费，队列溢出
    # 被总线断开时流直接结束且没有 idle，已收到的序列没有重复
    assert got == sorted(set(got))
    if not ended_with_idle:
        # 断线重连后从库里补齐
        for _ in range(250):
            if not world.sessions.is_running(s.id):
                break
            await asyncio.sleep(0.02)
        world.ctx.bus.capacity = 1000
        rest = await collect(world.sessions, s.id, after_seq=got[-1] if got else 0)
        total = [e.seq for e in await world.events(s.id)]
        assert got + seqs(rest) == total
    assert world.ctx.bus.subscriber_count() == 0


async def test_stream_unknown_session(world):
    from evalp.errors import NotFound

    with pytest.raises(NotFound):
        await world.sessions.get_session("ses_nope")


# ---------------------------------------------------------------- HTTP 层


def parse_sse(text: str) -> list[dict]:
    frames = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        f: dict = {}
        for line in block.split("\n"):
            k, _, val = line.partition(": ")
            f[k] = val
        if "data" in f:
            f["json"] = json.loads(f["data"])
        frames.append(f)
    return frames


@pytest.fixture
async def stream_client(app, token):
    async with httpx.AsyncClient(
        transport=StreamingASGITransport(app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


async def test_http_stream_replay_and_idle(world, stream_client, fake_openai):
    v = await world.config_version()
    fake_openai.enqueue("你好")
    s, _ = await world.chat(v, "hi")
    persisted = await world.events(s.id)
    r = await stream_client.get(f"/api/v1/sessions/{s.id}/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(r.text)
    assert frames[-1]["event"] == "idle" and frames[-1]["json"] == {
        "status": "idle",
        "last_seq": persisted[-1].seq,
    }
    body = frames[:-1]
    assert [int(f["id"]) for f in body] == [e.seq for e in persisted]
    assert [f["event"] for f in body] == [e.type for e in persisted]
    assert body[0]["json"]["type"] == "turn.start" and body[0]["json"]["seq"] == 1
    # after_seq 与 Last-Event-ID（取较大者）
    r = await stream_client.get(f"/api/v1/sessions/{s.id}/stream", params={"after_seq": 2})
    assert [int(f["id"]) for f in parse_sse(r.text) if "id" in f][0] == 3
    r = await stream_client.get(
        f"/api/v1/sessions/{s.id}/stream", params={"after_seq": 2}, headers={"Last-Event-ID": "4"}
    )
    assert [int(f["id"]) for f in parse_sse(r.text) if "id" in f][0] == 5
    r = await stream_client.get(
        f"/api/v1/sessions/{s.id}/stream", headers={"Last-Event-ID": str(persisted[-1].seq)}
    )
    assert [f["event"] for f in parse_sse(r.text)] == ["idle"]
    r = await stream_client.get("/api/v1/sessions/ses_nope/stream")
    assert r.status_code == 404


async def test_http_stream_error_event_is_named_agent_error(world, stream_client, fake_openai):
    """业务事件 error 在 SSE 线上叫 agent.error（避开 EventSource.onerror）；库里和 data.type 仍是 error。"""
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.fail(400, times=1, message="坏请求")
    turn = await world.sessions.run_turn(s.id, "会失败", wait=True)
    assert turn.status == "error"
    persisted = await world.events(s.id)
    assert "error" in [e.type for e in persisted]  # 落库类型不变
    frames = parse_sse((await stream_client.get(f"/api/v1/sessions/{s.id}/stream")).text)
    names = [f["event"] for f in frames]
    assert "error" not in names and names.count("agent.error") == 1
    f = next(f for f in frames if f["event"] == "agent.error")
    assert f["json"]["type"] == "error" and f["json"]["payload"]["code"] and "id" in f
    assert names[-1] == "idle" and names[-2] == "turn.end"
    # 其余事件的帧名与类型一致
    assert [n for n in names if n != "agent.error"][:-1] == [e.type for e in persisted if e.type != "error"]


async def test_http_stream_live_matches_db(world, stream_client, sdk_server):
    v = await world.http_version(sdk_server.url)
    s = await world.session(v)
    r = await stream_client.post(f"/api/v1/sessions/{s.id}/turns", json={"input": "burst:150"})
    assert r.status_code == 202
    body = r.json()
    assert body["turn"]["status"] == "running" and body["turn_id"] == body["turn"]["id"]
    assert body["stream_url"].startswith(f"/api/v1/sessions/{s.id}/stream")
    resp = await asyncio.wait_for(stream_client.get(body["stream_url"]), 20)
    frames = parse_sse(resp.text)
    assert frames[-1]["event"] == "idle"
    ids = [int(f["id"]) for f in frames if "id" in f]
    assert ids == [e.seq for e in await world.events(s.id)]
