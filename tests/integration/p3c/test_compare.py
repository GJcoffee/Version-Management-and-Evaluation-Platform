"""会话对比（live / history）与对齐。"""

from __future__ import annotations

from .conftest import API


async def test_live_compare_alignment(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"], title="P vs T1")
    assert cmp["id"].startswith("cmp_") and cmp["mode"] == "live" and cmp["title"] == "P vs T1"
    left, right = cmp["left"], cmp["right"]
    assert left["version_id"] == vs["P"]["id"] and right["version_seq"] == vs["T1"]["seq"]
    assert left["session_id"] != right["session_id"] and left["agent_name"] and right["agent_name"]

    p0 = await cw.pair(cmp["id"], "查订单")
    assert p0["pair_index"] == 0 and p0["left_turn_id"] != p0["right_turn_id"]
    await cw.wait_idle(left["session_id"], right["session_id"])
    p1 = await cw.pair(cmp["id"], "再问一次")
    assert p1["pair_index"] == 1
    await cw.wait_idle(left["session_id"], right["session_id"])

    al = await cw.alignment(cmp["id"])
    assert [t["pair"] for t in al["turns"]] == [0, 1]
    t0 = al["turns"][0]
    assert (t0["left_turn_id"], t0["right_turn_id"]) == (p0["left_turn_id"], p0["right_turn_id"])
    # P: [llm, final]；T1: [llm, tool, llm, final]。LCS 取最长连续匹配 [llm, final]，T1 前面多出 llm 与工具
    assert [(s["op"], s["sig"]) for s in t0["steps"]] == [
        ("insert", "llm"),
        ("insert", "tool:query_order"),
        ("equal", "llm"),
        ("equal", "final"),
    ]
    assert (
        t0["steps"][1]["right"] == "s2" and t0["steps"][2]["left"] == "s1" and t0["steps"][2]["right"] == "s3"
    )
    assert t0["metrics"]["left"]["tool_calls"] == 0 and t0["metrics"]["right"]["tool_calls"] == 1
    assert t0["metrics"]["left"]["llm_calls"] == 1 and t0["metrics"]["right"]["llm_calls"] == 2
    assert t0["metrics"]["right"]["tokens"] > t0["metrics"]["left"]["tokens"] > 0
    assert t0["final_text"]["left"] == "P答复：查订单" and t0["final_text"]["right"].startswith("T1答复")
    assert t0["tool_args_diff"] == []

    # 两侧会话各有两轮，pair_index 一致
    for side in (left, right):
        d = (await cw.client.get(f"{API}/sessions/{side['session_id']}")).json()
        assert [t["pair_index"] for t in d["turns"]] == [0, 1]

    # 列表与详情
    got = (await cw.client.get(f"{API}/compare/{cmp['id']}")).json()
    assert got == cmp
    items = (await cw.client.get(f"{API}/compare")).json()["items"]
    assert [i["id"] for i in items] == [cmp["id"]]


async def test_live_compare_tool_args_diff_and_ref(cw):
    vs = await cw.setup_versions()
    # 版本也可以写成 agent:vN
    r = await cw.client.post(
        f"{API}/compare",
        json={"kind": "session", "mode": "live", "left": {"version_id": await cw.ref(vs["T1"])},
              "right": {"version_id": await cw.ref(vs["T2"])}},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    cmp = r.json()
    assert cmp["left"]["version_id"] == vs["T1"]["id"]
    await cw.pair(cmp["id"], "查订单")
    await cw.wait_idle(cmp["left"]["session_id"], cmp["right"]["session_id"])
    t0 = (await cw.alignment(cmp["id"]))["turns"][0]
    assert [s["op"] for s in t0["steps"]] == ["equal"] * 4
    assert len(t0["tool_args_diff"]) == 1
    d = t0["tool_args_diff"][0]
    assert (d["left"], d["right"]) == ("s2", "s2")
    assert d["changes"][0]["path"] == "order_id"
    assert (d["changes"][0]["left"], d["changes"][0]["right"]) == ("A001", "A002")


async def test_history_compare(cw):
    vs = await cw.setup_versions()
    s1 = await cw.session(vs["T1"])
    s2 = await cw.session(vs["P"])
    for text in ("第一问", "第二问"):
        await cw.sessions.run_turn(s1.id, text)
    await cw.sessions.run_turn(s2.id, "第一问")
    r = await cw.client.post(
        f"{API}/compare",
        json={"kind": "session", "mode": "history", "left": {"session_id": s1.id},
              "right": {"session_id": s2.id}},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    cmp = r.json()
    assert cmp["mode"] == "history" and cmp["left"]["session_id"] == s1.id
    turns = (await cw.alignment(cmp["id"]))["turns"]
    assert [t["pair"] for t in turns] == [0, 1]
    assert turns[0]["right_turn_id"] is not None and turns[0]["tool_args_diff"] == []
    assert turns[1]["right_turn_id"] is None and turns[1]["left_turn_id"] is not None  # 只有一侧的轮次
    assert all(s["op"] == "delete" for s in turns[1]["steps"]) and turns[1]["metrics"]["right"] is None
    # history 模式不能双栏发送
    r = await cw.client.post(f"{API}/compare/{cmp['id']}/turns", json={"input": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    # 会话不存在
    r = await cw.client.post(
        f"{API}/compare",
        json={"kind": "session", "mode": "history", "left": {"session_id": s1.id},
              "right": {"session_id": "ses_nope"}},
    )  # fmt: skip
    assert r.status_code == 404


async def test_pair_busy_returns_409(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"])
    lid, rid = cmp["left"]["session_id"], cmp["right"]["session_id"]

    # 两侧都在执行：第二次发送 409
    cw.fake.slow(0.4, times=2)
    await cw.pair(cmp["id"], "慢一点")
    r = await cw.client.post(f"{API}/compare/{cmp['id']}/turns", json={"input": "又来"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_BUSY"
    await cw.wait_idle(lid, rid)

    # 只有一侧忙：409，且另一侧不会被启动
    cw.fake.reset()
    cw.fake.slow(0.4, times=1)
    cw.fake.add_rule(None, "ok")
    await cw.sessions.run_turn(rid, "占住右侧", wait=False)
    r = await cw.client.post(f"{API}/compare/{cmp['id']}/turns", json={"input": "再发"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_BUSY"
    assert r.json()["error"]["details"]["session_id"] == rid
    await cw.wait_idle(lid, rid)
    left_turns = (await cw.client.get(f"{API}/sessions/{lid}")).json()["turns"]
    assert len(left_turns) == 1  # 只有第一次成功的那一轮


# ---------------------------------------------------------------- 标题 / 备注编辑


async def test_patch_title_and_notes(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"], title="旧标题")
    assert cmp["notes"] is None
    url = f"{API}/compare/{cmp['id']}"
    r = await cw.client.patch(url, json={"title": "  新标题  ", "notes": "第一行\n第二行"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["title"] == "新标题" and got["notes"] == "第一行\n第二行"
    assert (await cw.client.get(url)).json() == got
    # 只改出现的字段
    r = await cw.client.patch(url, json={"notes": "只改备注"})
    assert r.json()["title"] == "新标题" and r.json()["notes"] == "只改备注"
    # null / 空白清空
    r = await cw.client.patch(url, json={"title": None, "notes": "   "})
    assert r.json()["title"] is None and r.json()["notes"] is None
    # 校验
    for body in ({}, {"title": "x" * 121}, {"notes": "x" * 2001}, {"bogus": 1}):
        r = await cw.client.patch(url, json=body)
        assert r.status_code == 400, (body, r.text)
    assert (await cw.client.patch(f"{API}/compare/cmp_nope", json={"title": "x"})).status_code == 404
    # 列表里也是新值
    assert (await cw.client.get(f"{API}/compare")).json()["items"][0]["notes"] is None


async def test_patch_replay_group_keeps_replay_fields(cw):
    vs = await cw.setup_versions()
    src = await cw.session(vs["P"])
    await cw.sessions.run_turn(src.id, "你好")
    r = await cw.client.post(
        f"{API}/sessions/{src.id}/replay", json={"target_version_id": vs["T1"]["id"], "turns": [0]}
    )
    out = r.json()
    await cw.compare.wait_replay(out["session_id"], 10)
    r = await cw.client.patch(f"{API}/compare/{out['compare_id']}", json={"notes": "我的备注"})
    g = r.json()
    assert g["notes"] == "我的备注" and g["replay_turns"] == [0] and g["replay_status"] == "completed"
    assert len((await cw.alignment(out["compare_id"]))["turns"]) == 1


# ---------------------------------------------------------------- 单侧发送（分叉）


async def _fork(cw, cmp_id, text, side):
    r = await cw.client.post(f"{API}/compare/{cmp_id}/turns", json={"input": text, "side": side})
    assert r.status_code == 202, r.text
    return r.json()


async def test_single_side_send_forks_without_pairing(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"])
    lid, rid = cmp["left"]["session_id"], cmp["right"]["session_id"]
    p0 = await cw.pair(cmp["id"], "查订单")
    await cw.wait_idle(lid, rid)

    f = await _fork(cw, cmp["id"], "只问左边", "left")
    assert f["side"] == "left" and f["pair_index"] is None
    assert f["left_turn_id"] and f["right_turn_id"] is None
    await cw.wait_idle(lid, rid)
    f2 = await _fork(cw, cmp["id"], "只问右边", "right")
    assert f2["left_turn_id"] is None and f2["right_turn_id"] and f2["pair_index"] is None
    await cw.wait_idle(lid, rid)

    # 之后再双侧发送：pair_index 接着往下数（单侧消息不占用）
    p1 = await cw.pair(cmp["id"], "回到双侧")
    assert p1["pair_index"] == 1 and p1["side"] == "both"
    await cw.wait_idle(lid, rid)

    left = (await cw.client.get(f"{API}/sessions/{lid}")).json()["turns"]
    right = (await cw.client.get(f"{API}/sessions/{rid}")).json()["turns"]
    assert [t["pair_index"] for t in left] == [0, None, 1]
    assert [t["pair_index"] for t in right] == [0, None, 1]
    assert [t["input"]["content"] for t in left] == ["查订单", "只问左边", "回到双侧"]
    assert [t["input"]["content"] for t in right] == ["查订单", "只问右边", "回到双侧"]

    al = await cw.alignment(cmp["id"])
    assert [t["pair"] for t in al["turns"]] == [0, 1]  # 单侧消息不参与对齐
    forks = al["forks"]
    assert [(x["side"], x["input"], x["after_pair"]) for x in forks] == [
        ("left", "只问左边", 0),
        ("right", "只问右边", 0),
    ]
    assert forks[0]["turn_id"] == f["left_turn_id"] and forks[1]["turn_id"] == f2["right_turn_id"]
    assert forks[0]["final_text"] and forks[0]["status"] == "ok"
    # 单侧消息进入该侧之后的上下文
    assert p0["pair_index"] == 0
    last_left = [r for r in cw.fake.chat_requests if r.last_user == "回到双侧"]
    assert any("只问左边" in [m.get("content") for m in r.messages] for r in last_left)


async def test_single_side_busy_only_checks_that_side(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"])
    lid, rid = cmp["left"]["session_id"], cmp["right"]["session_id"]
    cw.fake.slow(0.5, times=1)
    cw.fake.add_rule(None, "ok")
    await cw.sessions.run_turn(rid, "占住右侧", wait=False)
    # 右侧忙：只发左侧可以；只发右侧 409
    ok = await _fork(cw, cmp["id"], "左边", "left")
    assert ok["left_turn_id"]
    r = await cw.client.post(f"{API}/compare/{cmp['id']}/turns", json={"input": "右边", "side": "right"})
    assert r.status_code == 409 and r.json()["error"]["details"]["session_id"] == rid
    await cw.wait_idle(lid, rid)
    r = await cw.client.post(f"{API}/compare/{cmp['id']}/turns", json={"input": "x", "side": "middle"})
    assert r.status_code == 400


async def test_fork_rejected_for_non_live(cw):
    vs = await cw.setup_versions()
    s1, s2 = await cw.session(vs["P"]), await cw.session(vs["T1"])
    r = await cw.client.post(
        f"{API}/compare",
        json={"kind": "session", "mode": "history",
              "left": {"session_id": s1.id}, "right": {"session_id": s2.id}},  # fmt: skip
    )
    r = await cw.client.post(f"{API}/compare/{r.json()['id']}/turns", json={"input": "x", "side": "left"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------- alignment 缓存


async def test_alignment_cache_hits_and_invalidates(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"])
    lid, rid = cmp["left"]["session_id"], cmp["right"]["session_id"]
    svc = cw.compare
    loads = 0
    real = svc._load_turns

    async def counting(session_id):
        nonlocal loads
        loads += 1
        return await real(session_id)

    svc._load_turns = counting
    # 空会话也可以缓存
    first = await cw.alignment(cmp["id"])
    assert first == {"turns": [], "forks": []}
    await cw.alignment(cmp["id"])
    assert loads == 2 and svc.align_cache_hits == 1

    await cw.pair(cmp["id"], "查订单")
    await cw.wait_idle(lid, rid)
    a1 = await cw.alignment(cmp["id"])
    n = loads
    a2 = await cw.alignment(cmp["id"])
    assert a2 == a1 and loads == n  # 命中，没有重算
    assert len(a1["turns"]) == 1 and a1["turns"][0]["final_text"]["left"]

    # 新的一轮 -> 失效，重新计算
    await cw.pair(cmp["id"], "再问")
    await cw.wait_idle(lid, rid)
    a3 = await cw.alignment(cmp["id"])
    assert loads == n + 2 and [t["pair"] for t in a3["turns"]] == [0, 1]


async def test_alignment_not_cached_while_running(cw):
    vs = await cw.setup_versions()
    cmp = await cw.new_compare(vs["P"], vs["T1"])
    lid, rid = cmp["left"]["session_id"], cmp["right"]["session_id"]
    cw.fake.slow(0.5, times=None)
    await cw.pair(cmp["id"], "慢")
    h0 = cw.compare.align_cache_hits
    running1 = await cw.alignment(cmp["id"])
    running2 = await cw.alignment(cmp["id"])
    assert cw.compare.align_cache_hits == h0  # 运行中不命中缓存
    assert running1["turns"] and running2["turns"]
    await cw.wait_idle(lid, rid)
    done = await cw.alignment(cmp["id"])
    assert done["turns"][0]["status"] == {"left": "ok", "right": "ok"}  # 不会把运行中的半截结果留在缓存里
    assert done["turns"][0]["final_text"]["left"]


async def test_alignment_cache_lru_bounded_and_replay_not_cached_midway(cw, monkeypatch):
    from evalp.services import compare as compare_mod

    vs = await cw.setup_versions()
    src = await cw.session(vs["P"])
    await cw.sessions.run_turn(src.id, "你好")
    await cw.sessions.run_turn(src.id, "再见")
    cw.fake.slow(0.3, times=None)
    r = await cw.client.post(f"{API}/sessions/{src.id}/replay", json={"target_version_id": vs["T1"]["id"]})
    out = r.json()
    h0 = cw.compare.align_cache_hits
    mid1 = await cw.alignment(out["compare_id"])
    mid2 = await cw.alignment(out["compare_id"])
    assert cw.compare.align_cache_hits == h0
    assert len(mid1["turns"]) == 2 and mid2
    await cw.compare.wait_replay(out["session_id"], 10)
    final = await cw.alignment(out["compare_id"])
    assert all(t["right_turn_id"] and t["final_text"]["right"] for t in final["turns"])
    assert (await cw.alignment(out["compare_id"])) == final and cw.compare.align_cache_hits == h0 + 1
    # 容量上限：超出后淘汰最久未用的
    monkeypatch.setattr(compare_mod, "ALIGN_CACHE_SIZE", 1)
    other = await cw.new_compare(vs["P"], vs["T1"])
    await cw.alignment(other["id"])
    assert len(cw.compare._align_cache) == 1
