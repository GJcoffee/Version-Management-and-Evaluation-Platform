"""重放：状态 / 所选轮次字段、SSE 不在轮次间隙发 idle、锚定模式轮内并发、自由模式。"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from evalp.errors import SessionBusy
from evalp.runtime.events import AgentEvent
from evalp.services.session import Idle
from evalp.store.models import CompareGroup

from tests.fixtures.fake_openai import StreamingASGITransport

from .conftest import API


async def _source(cw, vs, n=2):
    """原会话：版本 P 上的 n 轮对话。"""
    s = await cw.session(vs["P"])
    turns = [await cw.sessions.run_turn(s.id, f"问题{i}") for i in range(n)]
    assert all(t.status == "ok" for t in turns)
    return s, turns


async def _start(cw, src_id, target, **body):
    r = await cw.client.post(f"{API}/sessions/{src_id}/replay", json={"target_version_id": target, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _group(cw, cid):
    r = await cw.client.get(f"{API}/compare/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- CompareGroup 的重放字段


async def test_group_exposes_replay_status_and_turns(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    cw.fake.slow(0.3, times=None)
    out = await _start(cw, src.id, vs["T1"]["id"], turns=[2, 0])
    g = await _group(cw, out["compare_id"])
    assert g["mode"] == "replay" and g["replay_status"] == "running"
    assert g["replay_turns"] == [0, 2] and g["replay_mode"] == "anchored" and g["replay_error"] is None
    assert g["notes"] is None  # 重放元数据不再占用 notes
    await cw.compare.wait_replay(out["session_id"], 15)
    g = await _group(cw, out["compare_id"])
    assert g["replay_status"] == "completed" and g["replay_turns"] == [0, 2] and g["replay_error"] is None
    # 列表里同样带这些字段
    items = (await cw.client.get(f"{API}/compare")).json()["items"]
    assert items[0]["replay_status"] == "completed"
    # 非重放的对比这些字段为 null
    live = await cw.new_compare(vs["P"], vs["T1"])
    assert (live["replay_status"], live["replay_turns"], live["replay_mode"]) == (None, None, None)


async def test_agent_error_is_a_result_not_a_failed_replay(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    cw.fake.reset()
    cw.fake.add_rule(None, "ok")
    cw.fake.fail(400, times=None, message="boom")
    out = await _start(cw, src.id, vs["P"]["id"])
    await cw.compare.wait_replay(out["session_id"], 15)
    assert (await _group(cw, out["compare_id"]))["replay_status"] == "completed"
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert [t["status"] for t in d["turns"]] == ["error", "error"]


async def test_replay_failed_when_turn_cannot_start(cw, monkeypatch):
    """某一轮没能启动（例如历史读取出错）：对比组记为 failed 并带错误说明，其余轮次照常完成。"""
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    real = cw.sessions.get_history

    async def flaky(session_id, upto_idx=None):
        if upto_idx == 1:
            raise RuntimeError("读历史失败")
        return await real(session_id, upto_idx=upto_idx)

    monkeypatch.setattr(cw.sessions, "get_history", flaky)
    out = await _start(cw, src.id, vs["T1"]["id"])
    await cw.compare.wait_replay(out["session_id"], 15)
    g = await _group(cw, out["compare_id"])
    assert g["replay_status"] == "failed" and "第 2 轮" in g["replay_error"]
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert sorted(t["pair_index"] for t in d["turns"]) == [0, 2]


async def test_running_without_task_reads_as_failed(cw):
    """库里是 running 但后台任务已不在（服务重启）：读出来是 failed + 中断说明。"""
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 1)
    out = await _start(cw, src.id, vs["P"]["id"])
    await cw.compare.wait_replay(out["session_id"], 15)
    async with cw.ctx.db.write() as s:
        (await s.get(CompareGroup, out["compare_id"])).replay_status = "running"
    g = await _group(cw, out["compare_id"])
    assert g["replay_status"] == "failed" and "中断" in g["replay_error"]


async def test_legacy_notes_are_read_compatibly(cw):
    """迁移前的数据（replay_turns 在 notes JSON 里，没有新列）：兼容读取，notes 不当作备注返回。"""
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    out = await _start(cw, src.id, vs["P"]["id"], turns=[1])
    await cw.compare.wait_replay(out["session_id"], 15)
    async with cw.ctx.db.write() as s:
        row = await s.get(CompareGroup, out["compare_id"])
        row.replay_turns, row.notes = None, '{"replay_turns": [1]}'
    g = await _group(cw, out["compare_id"])
    assert g["replay_turns"] == [1] and g["notes"] is None
    al = await cw.alignment(out["compare_id"])
    assert [t["pair"] for t in al["turns"]] == [1]  # 对齐仍只含所选轮次


# ---------------------------------------------------------------- SSE 在重放期间不发 idle


async def _collect(svc, session_id):
    return [item async for item in svc.stream(session_id, 0)]


async def test_stream_does_not_idle_between_replay_turns(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    cw.fake.slow(0.25, times=None)
    for mode in ("free", "anchored"):
        out = await _start(cw, src.id, vs["P"]["id"], mode=mode)
        # 重放刚创建、第一轮还没开始时就连上：不能立刻 idle
        items = await asyncio.wait_for(_collect(cw.sessions, out["session_id"]), 20)
        assert isinstance(items[-1], Idle)
        assert sum(1 for i in items if isinstance(i, Idle)) == 1
        ends = [i for i in items if isinstance(i, AgentEvent) and i.type == "turn.end"]
        assert len(ends) == 3, mode
        # 收到 idle 时对比组已是终态
        assert (await _group(cw, out["compare_id"]))["replay_status"] == "completed"
        assert items[-1].last_seq == max(e.seq for e in items if isinstance(e, AgentEvent) and e.seq)


async def test_stream_idles_when_session_is_not_held(cw):
    """没有重放持有的普通会话行为不变：一轮结束就 idle。"""
    vs = await cw.setup_versions()
    s = await cw.session(vs["P"])
    await cw.sessions.run_turn(s.id, "你好")
    items = await _collect(cw.sessions, s.id)
    assert isinstance(items[-1], Idle)
    release = cw.sessions.hold_stream(s.id)
    got: list = []

    async def reader():
        async for i in cw.sessions.stream(s.id, 0):
            got.append(i)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.3)
    assert not task.done() and not any(isinstance(i, Idle) for i in got)  # 持有期间保持打开
    release()
    await asyncio.wait_for(task, 5)
    assert isinstance(got[-1], Idle)


async def test_http_stream_stays_open_during_replay(cw, app, token):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    cw.fake.slow(0.25, times=None)
    out = await _start(cw, src.id, vs["P"]["id"], mode="free")
    async with httpx.AsyncClient(
        transport=StreamingASGITransport(app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        r = await asyncio.wait_for(c.get(f"/api/v1/sessions/{out['session_id']}/stream"), 20)
    events = [ln[7:] for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert events.count("turn.end") == 2 and events.count("idle") == 1 and events[-1] == "idle"


# ---------------------------------------------------------------- 锚定模式：轮内并发


async def test_anchored_turns_run_concurrently_with_cap(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 6)
    cw.fake.slow(0.4, times=None)
    cw.fake.max_inflight = 0
    t0 = time.monotonic()
    out = await _start(cw, src.id, vs["P"]["id"])
    await cw.compare.wait_replay(out["session_id"], 30)
    elapsed = time.monotonic() - t0
    assert 2 <= cw.fake.max_inflight <= 4  # 并发，但不超过上限 4
    assert elapsed < 6 * 0.4  # 串行至少 2.4s
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert d["session"]["status"] == "idle"
    # Turn 行按原轮次顺序创建：idx、pair_index 一致且单调
    assert [t["idx"] for t in d["turns"]] == list(range(6))
    assert [t["pair_index"] for t in d["turns"]] == list(range(6))
    assert all(t["status"] == "ok" for t in d["turns"])
    # 同一会话的 seq 单调、不重复
    ev = (await cw.client.get(f"{API}/sessions/{out['session_id']}", params={"include": "events"})).json()
    seqs = [e["seq"] for e in ev["events"]]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1
    # 每轮的事件都带自己的 turn_id，且各以 turn.end 结束
    ends = [e for e in ev["events"] if e["type"] == "turn.end"]
    assert sorted(e["turn_id"] for e in ends) == sorted(t["id"] for t in d["turns"])
    # 对比组完成，对齐里每轮都有右侧
    assert (await _group(cw, out["compare_id"]))["replay_status"] == "completed"
    al = await cw.alignment(out["compare_id"])
    assert all(t["right_turn_id"] for t in al["turns"])


async def test_anchored_concurrent_context_still_from_source(cw):
    """并发不改变上下文：每轮仍使用原会话（P 的回复）作为历史。"""
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    cw.fake.requests.clear()
    cw.fake.slow(0.1, times=None)
    out = await _start(cw, src.id, vs["T1"]["id"])
    await cw.compare.wait_replay(out["session_id"], 20)
    last = [r for r in cw.fake.chat_requests if r.last_user == "问题2" and not r.has_tool_message][0]
    texts = [m.get("content") for m in last.messages]
    assert "P答复：问题0" in texts and "P答复：问题1" in texts
    assert not any(isinstance(t, str) and t.startswith("T1答复") for t in texts)


async def test_exclusive_turn_still_blocked_on_replay_session(cw):
    """重放会话上的普通（非并发）轮次与重放中的轮次互斥。"""
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 2)
    cw.fake.slow(0.4, times=None)
    out = await _start(cw, src.id, vs["P"]["id"])
    for _ in range(100):
        if cw.sessions.is_running(out["session_id"]):
            break
        await asyncio.sleep(0.02)
    with pytest.raises(SessionBusy):
        await cw.sessions.run_turn(out["session_id"], "插队", wait=False)
    await cw.compare.wait_replay(out["session_id"], 20)


# ---------------------------------------------------------------- 自由模式


async def test_free_mode_uses_own_replies_and_runs_serially(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    cw.fake.requests.clear()
    cw.fake.slow(0.15, times=None)
    cw.fake.max_inflight = 0
    out = await _start(cw, src.id, vs["T1"]["id"], mode="free")
    await cw.compare.wait_replay(out["session_id"], 30)
    assert cw.fake.max_inflight == 1  # 逐轮串行
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert d["session"]["replay_mode"] == "free" and d["session"]["source_session_id"] == src.id
    assert [t["pair_index"] for t in d["turns"]] == [0, 1, 2] and all(t["status"] == "ok" for t in d["turns"])
    assert all(t["anchored_from_turn_id"] is None for t in d["turns"])  # 自由模式没有锚定
    # 第 3 轮的上下文是新会话自己的回复（T1答复…），不是原会话的 "P答复…"
    last = [r for r in cw.fake.chat_requests if r.last_user == "问题2"][0]
    texts = [m.get("content") for m in last.messages]
    assert any(isinstance(t, str) and t.startswith("T1答复") for t in texts)
    assert not any(isinstance(t, str) and t.startswith("P答复") for t in texts)
    g = await _group(cw, out["compare_id"])
    assert g["replay_mode"] == "free" and g["replay_status"] == "completed"
    al = await cw.alignment(out["compare_id"])
    assert [t["pair"] for t in al["turns"]] == [0, 1, 2] and all(t["right_turn_id"] for t in al["turns"])


async def test_free_mode_selected_turns_only_see_selected_history(cw):
    vs = await cw.setup_versions()
    src, _ = await _source(cw, vs, 3)
    cw.fake.requests.clear()
    out = await _start(cw, src.id, vs["P"]["id"], mode="free", turns=[0, 2])
    await cw.compare.wait_replay(out["session_id"], 20)
    req = [r for r in cw.fake.chat_requests if r.last_user == "问题2"][0]
    users = [m.get("content") for m in req.messages if m["role"] == "user"]
    assert users == ["问题0", "问题2"]  # 没选的第 2 轮不在上下文里
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert [t["pair_index"] for t in d["turns"]] == [0, 2]


async def test_concurrent_turns_session_status_and_sink(cw):
    """同一会话的并发轮次：会话直到最后一个结束才回到空闲；seq 单调不重复；事件各归其轮。"""
    vs = await cw.setup_versions()
    s = await cw.session(vs["P"])
    cw.fake.slow(0.5, times=None)
    a = await cw.sessions.run_turn(s.id, "甲", history_override=[], concurrent=True, wait=False)
    b = await cw.sessions.run_turn(s.id, "乙", history_override=[], concurrent=True, wait=False)
    assert cw.sessions.running_turn_ids(s.id) == [a.id, b.id]
    await cw.sessions.cancel_turn(a.id)  # 甲先结束，乙还在跑
    assert (await cw.sessions.get_session(s.id)).status == "running"
    assert cw.sessions.is_running(s.id)
    done_b = await cw.sessions.wait_turn(b.id)
    assert done_b.status == "ok"
    await cw.wait_idle(s.id)
    detail = await cw.sessions.get(s.id, include_turns=True, include_events=True)
    assert detail.session.status == "idle" and detail.session.stats["turns"] == 2
    seqs = [e.seq for e in detail.events]
    assert seqs == list(range(1, len(seqs) + 1))
    by_turn: dict[str, list[str]] = {}
    for e in detail.events:
        by_turn.setdefault(e.turn_id, []).append(e.type)
    assert set(by_turn) == {a.id, b.id} and all(v[-1] == "turn.end" for v in by_turn.values())
