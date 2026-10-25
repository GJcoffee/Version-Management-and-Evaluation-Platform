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
