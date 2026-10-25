"""历史重放（锚定模式）。"""

from __future__ import annotations

from .conftest import API


async def _source(cw, vs):
    """原会话：版本 P 上的两轮对话。"""
    s = await cw.session(vs["P"])
    t0 = await cw.sessions.run_turn(s.id, "你好")
    t1 = await cw.sessions.run_turn(s.id, "第二个问题")
    assert t0.status == "ok" and t1.status == "ok"
    return s, t0, t1


async def _replay(cw, src_id, target, **body):
    r = await cw.client.post(f"{API}/sessions/{src_id}/replay", json={"target_version_id": target, **body})
    assert r.status_code == 201, r.text
    out = r.json()
    await cw.compare.wait_replay(out["session_id"], 10)
    return out


async def test_replay_anchored(cw):
    vs = await cw.setup_versions()
    src, t0, t1 = await _source(cw, vs)
    cw.fake.requests.clear()

    out = await _replay(cw, src.id, await cw.ref(vs["T1"]))
    new_id = out["session_id"]

    # 新会话：mode=replay，来源与锚定信息正确；pair_index 对应原轮次 idx
    d = (await cw.client.get(f"{API}/sessions/{new_id}")).json()
    assert d["session"]["mode"] == "replay" and d["session"]["source_session_id"] == src.id
    assert d["session"]["replay_mode"] == "anchored" and d["session"]["version_id"] == vs["T1"]["id"]
    assert [t["pair_index"] for t in d["turns"]] == [0, 1]
    assert [t["anchored_from_turn_id"] for t in d["turns"]] == [t0.id, t1.id]
    assert all(t["status"] == "ok" for t in d["turns"])
    assert d["turns"][1]["input"]["content"] == "第二个问题"

    # 锚定上下文：第二轮的请求带着原会话第一轮的问答（而不是新会话自己的回复）
    second = [r for r in cw.fake.chat_requests if r.last_user == "第二个问题" and not r.has_tool_message]
    assert len(second) == 1
    texts = [m.get("content") for m in second[0].messages]
    assert "你好" in texts and "P答复：你好" in texts  # 原会话的回复，T1 自己会回复"T1答复…"
    assert not any(isinstance(t, str) and t.startswith("T1答复") for t in texts)
    # 第一轮没有前文
    first = [r for r in cw.fake.chat_requests if r.last_user == "你好" and not r.has_tool_message]
    assert [m["role"] for m in first[0].messages] == ["system", "user"]

    # 自动生成的对比：左 = 原会话，右 = 新会话
    cmp = (await cw.client.get(f"{API}/compare/{out['compare_id']}")).json()
    assert cmp["mode"] == "replay" and cmp["left"]["session_id"] == src.id
    assert cmp["right"]["session_id"] == new_id and cmp["right"]["version_id"] == vs["T1"]["id"]
    al = await cw.alignment(out["compare_id"])
    assert [t["pair"] for t in al["turns"]] == [0, 1]
    assert al["turns"][0]["left_turn_id"] == t0.id
    assert al["turns"][1]["left_turn_id"] == t1.id
    assert all(t["right_turn_id"] for t in al["turns"])
    assert [(s["op"], s["sig"]) for s in al["turns"][0]["steps"]][1] == ("insert", "tool:query_order")


async def test_replay_selected_turns_and_progress(cw):
    vs = await cw.setup_versions()
    src, t0, t1 = await _source(cw, vs)
    cw.fake.requests.clear()
    cw.fake.slow(0.3, times=None)
    r = await cw.client.post(
        f"{API}/sessions/{src.id}/replay", json={"target_version_id": vs["T2"]["id"], "turns": [1]}
    )
    assert r.status_code == 201
    out = r.json()
    # 立刻就能打开对比页：此时新会话的轮次还没完成，已有对比组和对齐结果
    assert cw.compare.replay_running(out["session_id"])
    early = await cw.alignment(out["compare_id"])
    assert [t["pair"] for t in early["turns"]] == [1] and early["turns"][0]["left_turn_id"] == t1.id
    await cw.compare.wait_replay(out["session_id"], 10)
    d = (await cw.client.get(f"{API}/sessions/{out['session_id']}")).json()
    assert len(d["turns"]) == 1 and d["turns"][0]["pair_index"] == 1
    assert d["turns"][0]["anchored_from_turn_id"] == t1.id
    # 只重放第 1 轮，但上下文仍含原会话第 0 轮
    req = [r for r in cw.fake.chat_requests if r.last_user == "第二个问题"][0]
    assert "P答复：你好" in [m.get("content") for m in req.messages]
    late = await cw.alignment(out["compare_id"])
    assert late["turns"][0]["right_turn_id"] == d["turns"][0]["id"]


async def test_replay_validation(cw):
    vs = await cw.setup_versions()
    src, _, _ = await _source(cw, vs)
    for body in (
        {"target_version_id": vs["T1"]["id"], "turns": [5]},
        {"target_version_id": vs["T1"]["id"], "mode": "free"},
    ):
        r = await cw.client.post(f"{API}/sessions/{src.id}/replay", json=body)
        assert r.status_code == 400, r.text
    r = await cw.client.post(f"{API}/sessions/ses_nope/replay", json={"target_version_id": vs["T1"]["id"]})
    assert r.status_code == 404
