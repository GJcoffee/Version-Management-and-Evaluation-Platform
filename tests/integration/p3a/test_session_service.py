"""SessionService：忙碌互斥、启动恢复、列表过滤、历史拼接、取消、统计与状态。"""

import asyncio

import pytest
from evalp.errors import NotFound, SessionBusy, ValidationFailed
from evalp.ids import new_id
from evalp.runtime.messages import Message
from evalp.services.session import SessionService
from evalp.store.models import Event, Session, Turn
from evalp.utils.time import now_iso


async def wait_running(world, session_id, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if world.sessions.running_turn_id(session_id):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("轮次没有开始执行")


# ---------------------------------------------------------------- 创建 / 基础


async def test_create_and_get(world):
    v = await world.config_version(name="alpha")
    s = await world.session(v, title="标题", variables={"a": 1})
    assert s.id.startswith("ses_") and s.status == "idle" and s.mode == "chat"
    assert s.agent_name == "alpha" and s.version_seq == v["seq"] and s.variables == {"a": 1}
    assert s.stats["turns"] == 0 and s.last_seq == 0
    detail = await world.sessions.get(s.id)
    assert detail.turns == [] and detail.events is None
    with pytest.raises(NotFound):
        await world.sessions.get("ses_nope")
    with pytest.raises(NotFound):
        await world.sessions.create(version_id="ver_nope", mode="chat")
    with pytest.raises(ValidationFailed):
        await world.sessions.create(version_id=v["id"], mode="weird")
    with pytest.raises(ValidationFailed):
        await world.sessions.create(version_id=v["id"], mode="replay", replay_mode="x")


async def test_title_from_first_input_and_stats(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.enqueue("一", "二")
    t1 = await world.sessions.run_turn(s.id, "这是第一句话")
    t2 = await world.sessions.run_turn(s.id, "第二句")
    assert (t1.idx, t2.idx) == (0, 1)
    detail = await world.sessions.get(s.id, include_events=True)
    assert detail.session.title and "第一句话" in detail.session.title
    st = detail.session.stats
    assert st["turns"] == 2 and st["llm_calls"] == 2
    assert st["prompt_tokens"] == t1.usage["prompt_tokens"] + t2.usage["prompt_tokens"]
    assert st["cost"] == pytest.approx(t1.cost + t2.cost)
    assert detail.session.last_seq == detail.events[-1].seq == len(detail.events)
    assert detail.session.status == "idle"
    # 用户给了标题时不被覆盖
    s2 = await world.session(v, title="我的标题")
    await world.sessions.run_turn(s2.id, "内容")
    assert (await world.sessions.get_session(s2.id)).title == "我的标题"


async def test_error_turn_sets_session_error_and_recovers(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.fail(400, times=1, message="bad request")
    t = await world.sessions.run_turn(s.id, "会出错")
    assert t.status == "error" and t.error is not None and t.error.code
    detail = await world.sessions.get(s.id)
    assert detail.session.status == "error" and detail.session.stats["turns"] == 1
    # 失败的轮次不进入历史
    assert await world.sessions.get_history(s.id) == []
    fake_openai.enqueue("好了")
    t2 = await world.sessions.run_turn(s.id, "再试")
    assert t2.status == "ok" and (await world.sessions.get(s.id)).session.status == "idle"
    hist = await world.sessions.get_history(s.id)
    assert [m.role for m in hist] == ["user", "assistant"] and hist[0].content == "再试"


# ---------------------------------------------------------------- 忙碌 / 取消


async def test_session_busy(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(0.6, times=1)
    fake_openai.enqueue("慢回复", "快回复")
    t1 = await world.sessions.run_turn(s.id, "第一句", wait=False)
    assert t1.status == "running" and t1.finished_at is None
    await wait_running(world, s.id)
    assert (await world.sessions.get_session(s.id)).status == "running"
    with pytest.raises(SessionBusy) as ei:
        await world.sessions.run_turn(s.id, "插队")
    assert ei.value.code == "SESSION_BUSY"
    # 其他会话不受影响
    other, t_other = await world.chat(v, "别的会话")
    assert t_other.status == "ok" and other.id != s.id
    for _ in range(200):
        done = await world.sessions.get_turn(t1.id)
        if done.status != "running":
            break
        await asyncio.sleep(0.02)
    assert done.status == "ok" and done.output.final.content
    t2 = await world.sessions.run_turn(s.id, "第二句")
    assert t2.status == "ok" and t2.idx == 1


async def test_session_busy_via_api(world, client, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(0.5, times=1)
    fake_openai.enqueue("a", "b")
    r1 = await client.post(f"/api/v1/sessions/{s.id}/turns", json={"input": "一"})
    assert r1.status_code == 202, r1.text
    await wait_running(world, s.id)
    r2 = await client.post(f"/api/v1/sessions/{s.id}/turns", json={"input": "二"})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "SESSION_BUSY"
    await world.sessions.cancel_turn(r1.json()["turn_id"])


async def test_cancel_turn(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(30)
    turn = await world.sessions.run_turn(s.id, "很慢", wait=False)
    await wait_running(world, s.id)
    await asyncio.sleep(0.1)
    await world.sessions.cancel_turn(turn.id)
    t = await world.sessions.get_turn(turn.id)
    assert t.status == "cancelled" and t.error is None and t.finished_at
    types = await world.types(s.id)
    assert types[0] == "turn.start" and types[-1] == "turn.end"
    end = (await world.events(s.id))[-1]
    assert end.payload["status"] == "cancelled"
    detail = await world.sessions.get(s.id)
    assert detail.session.status == "idle" and detail.session.stats["turns"] == 1
    assert not world.sessions.is_running(s.id)
    # 已结束的轮次再取消不报错；不存在的报 NotFound
    await world.sessions.cancel_turn(turn.id)
    with pytest.raises(NotFound):
        await world.sessions.cancel_turn("trn_nope")
    # 取消后可以继续对话，且被取消的轮次不进历史
    fake_openai.clear_faults()
    fake_openai.enqueue("继续")
    t2 = await world.sessions.run_turn(s.id, "好了吗")
    assert t2.status == "ok" and t2.idx == 1
    assert [m.content for m in await world.sessions.get_history(s.id)] == ["好了吗", "继续"]


async def test_wait_caller_cancelled_cancels_turn(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(30)
    task = asyncio.create_task(world.sessions.run_turn(s.id, "很慢"))
    await wait_running(world, s.id)
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(200):
        if not world.sessions.is_running(s.id):
            break
        await asyncio.sleep(0.02)
    assert not world.sessions.is_running(s.id)
    turns = (await world.sessions.get(s.id)).turns
    assert [t.status for t in turns] == ["cancelled"]
    assert (await world.sessions.get(s.id)).session.status == "idle"


async def test_aclose_cancels_running(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(30)
    turn = await world.sessions.run_turn(s.id, "很慢", wait=False)
    await wait_running(world, s.id)
    await asyncio.sleep(0.1)
    await world.sessions.aclose()
    assert (await world.sessions.get_turn(turn.id)).status == "cancelled"


async def test_run_turn_missing_session(world):
    v = await world.config_version()
    s = await world.session(v)
    with pytest.raises(NotFound):
        await world.sessions.run_turn("ses_nope", "你好")
    assert (await world.sessions.get(s.id)).turns == []


# ---------------------------------------------------------------- 历史 / 重放参数


async def test_history_upto_override_and_anchor(world, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.enqueue("答一", "答二", "答三")
    for text in ("问一", "问二", "问三"):
        await world.sessions.run_turn(s.id, text)
    full = await world.sessions.get_history(s.id)
    assert [m.content for m in full] == ["问一", "答一", "问二", "答二", "问三", "答三"]
    assert [m.content for m in await world.sessions.get_history(s.id, upto_idx=0)] == []
    assert [m.content for m in await world.sessions.get_history(s.id, upto_idx=2)] == [
        "问一", "答一", "问二", "答二",
    ]  # fmt: skip
    turns = (await world.sessions.get(s.id)).turns

    # 锚定重放：另一个会话以 history_override 代替自身历史，并记录锚点轮次
    replay = await world.sessions.create(version_id=v["id"], mode="replay", source_session_id=s.id)
    prefix = await world.sessions.get_history(s.id, upto_idx=1)
    fake_openai.reset()
    fake_openai.enqueue("重放答二")
    t = await world.sessions.run_turn(
        replay.id,
        "问二",
        history_override=prefix,
        anchored_from_turn_id=turns[1].id,
        pair_index=7,
    )
    assert t.status == "ok" and t.anchored_from_turn_id == turns[1].id and t.pair_index == 7
    roles = [(m["role"], m["content"]) for m in fake_openai.chat_requests[0].messages]
    assert roles[1:] == [("user", "问一"), ("assistant", "答一"), ("user", "问二")]
    # 持久化的字段
    again = await world.sessions.get_turn(t.id)
    assert again.anchored_from_turn_id == turns[1].id and again.pair_index == 7
    # 重放会话自己的历史只含自己的轮次（override 不进入存储）
    own = await world.sessions.get_history(replay.id)
    assert [m.content for m in own] == ["问二", "重放答二"]
    # 事件里也能看出是用了 override（turn.start 的 history_len）
    start = (await world.events(replay.id))[0]
    assert start.type == "turn.start" and start.payload["history_len"] == 2


async def test_input_message_object_and_variables_per_turn(world, fake_openai):
    v = await world.config_version(
        {"prompts": {"system": "语言={{ variables.lang }}", "variables": {"lang": "zh"}}}
    )
    s = await world.session(v, variables={"lang": "en"})
    fake_openai.enqueue("a", "b")
    await world.sessions.run_turn(s.id, Message(role="user", content="hi"))
    assert fake_openai.chat_requests[0].system == "语言=en"
    await world.sessions.run_turn(s.id, "hi", variables={"lang": "fr"})
    assert fake_openai.chat_requests[1].system == "语言=fr"


# ---------------------------------------------------------------- 启动恢复


async def _insert_running_turn(ctx, session_id, idx=0):
    tid = new_id("trn")
    async with ctx.db.write() as s:
        sess = await s.get(Session, session_id)
        sess.status = "running"
        s.add(
            Turn(
                id=tid,
                session_id=session_id,
                idx=idx,
                status="running",
                input={"role": "user", "content": "遗留"},
                started_at=now_iso(),
            )
        )
        await s.flush()
        s.add(
            Event(
                session_id=session_id,
                seq=sess.last_seq + 1,
                turn_id=tid,
                ts=now_iso(),
                type="turn.start",
                payload={},
            )
        )
        sess.last_seq += 1
    return tid


async def test_startup_recovery(world, ctx):
    v = await world.config_version()
    s = await world.session(v)
    s_idle = await world.session(v)
    tid = await _insert_running_turn(ctx, s.id)
    svc = SessionService(ctx)  # 模拟进程重启后的新服务实例
    assert await svc.recover() == 1
    assert await svc.recover() == 0  # 幂等
    t = await svc.get_turn(tid)
    assert t.status == "error" and t.error.code == "INTERRUPTED" and t.error.retriable is True
    assert t.finished_at
    detail = await svc.get(s.id, include_events=True)
    assert detail.session.status == "idle"
    assert [e.type for e in detail.events] == ["turn.start", "error", "turn.end"]
    assert [e.seq for e in detail.events] == [1, 2, 3] and detail.session.last_seq == 3
    assert detail.events[-1].payload["status"] == "error"
    assert detail.events[1].payload["code"] == "INTERRUPTED"
    assert (await svc.get_session(s_idle.id)).status == "idle"
    # 恢复后会话可以继续对话，seq 接着往下
    ctx.svc(SessionService)  # 单例仍可用
    r = await svc.run_turn(s.id, "继续")
    assert r.idx == 1 and r.status in ("ok", "error")
    events = await svc.get_events(s.id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


async def test_recovery_runs_lazily_on_first_use(world, ctx):
    v = await world.config_version()
    s = await world.session(v)
    tid = await _insert_running_turn(ctx, s.id)
    svc = SessionService(ctx)
    await svc.create(version_id=v["id"], mode="chat")  # 第一次使用时自动恢复
    assert (await svc.get_turn(tid)).error.code == "INTERRUPTED"


async def test_recovery_does_not_touch_live_turns(world, ctx, fake_openai):
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.slow(0.5, times=1)
    fake_openai.enqueue("正常")
    turn = await world.sessions.run_turn(s.id, "进行中", wait=False)
    await wait_running(world, s.id)
    world.sessions._recovered = False
    assert await world.sessions.recover() == 0
    for _ in range(200):
        if (await world.sessions.get_turn(turn.id)).status != "running":
            break
        await asyncio.sleep(0.02)
    assert (await world.sessions.get_turn(turn.id)).status == "ok"
    assert (await world.sessions.get_session(s.id)).status == "idle"


# ---------------------------------------------------------------- 列表


async def test_list_sessions_filters_and_pagination(world, fake_openai):
    va = await world.config_version(name="agent-a")
    vb = await world.config_version(name="agent-b")
    s1 = await world.session(va, title="退款咨询")
    s2 = await world.session(vb, title="物流查询")
    s3 = await world.session(va, title="退款 100%_特殊")
    ev = await world.sessions.create(version_id=va["id"], mode="eval", title="评测会话")
    jd = await world.sessions.create(version_id=va["id"], mode="judge", title="裁判")
    rp = await world.sessions.create(version_id=va["id"], mode="replay", title="重放")

    async def ids(**kw):
        page = await world.sessions.list_sessions(**kw)
        return [x.id for x in page.items]

    default = await ids()
    assert ev.id not in default and jd.id not in default
    assert default[:4] == [rp.id, s3.id, s2.id, s1.id]  # 新的在前
    assert await ids(mode="eval") == [ev.id]
    assert set(await ids(mode=["eval", "judge"])) == {ev.id, jd.id}
    assert await ids(mode="replay") == [rp.id]
    assert rp.id in default  # 缺省包含 replay（向后兼容）；调用方用 mode=chat 过滤掉重放会话
    assert rp.id not in await ids(mode="chat") and {s1.id, s2.id, s3.id} <= set(await ids(mode="chat"))
    assert set(await ids(agent_id="agent-b")) == {s2.id}
    assert s1.id in await ids(agent_id=va["agent_id"])
    assert await ids(version_id=vb["id"]) == [s2.id]
    assert await ids(q="退款") == [s3.id, s1.id]
    assert await ids(q="%_") == [s3.id]  # LIKE 通配符被转义
    assert await ids(q="不存在") == []
    with pytest.raises(ValidationFailed):
        await ids(mode="nope")
    # 分页
    p1 = await world.sessions.list_sessions(limit=2)
    assert len(p1.items) == 2 and p1.next_cursor
    p2 = await world.sessions.list_sessions(limit=2, cursor=p1.next_cursor)
    assert [x.id for x in p1.items + p2.items] == default[:4]
    first = p1.items[0]
    assert first.agent_name and first.version_seq


async def test_list_sessions_api(world, client):
    v = await world.config_version(name="lister")
    s = await world.session(v, title="关键字甲")
    await world.sessions.create(version_id=v["id"], mode="eval", title="关键字乙")
    r = await client.get("/api/v1/sessions", params={"q": "关键字"})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["items"]] == [s.id]
    r = await client.get("/api/v1/sessions", params={"mode": "eval,chat", "agent_id": "lister"})
    assert len(r.json()["items"]) == 2
    r = await client.get("/api/v1/sessions", params={"mode": "bogus"})
    assert r.status_code == 400


async def test_next_turn_right_after_polling_finished_turn(world, fake_openai):
    """客户端轮询到轮次结束后立即发下一轮：不应因为 turn.end 还在落库而报 SESSION_BUSY。"""
    v = await world.config_version()
    s = await world.session(v)
    fake_openai.add_rule(match=None, reply="好")
    for i in range(15):
        turn = await world.sessions.run_turn(s.id, f"第{i}句", wait=False)
        while (await world.sessions.get_turn(turn.id)).status == "running":
            await asyncio.sleep(0)
    assert (await world.sessions.get(s.id)).session.stats["turns"] >= 14
