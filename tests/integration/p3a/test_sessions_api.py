"""会话 REST 接口：创建（version_id / agent+tag）、详情 include、轮次 202 / 200、取消、错误码。"""

import asyncio

from tests.fixtures.fake_openai import Call

API = "/api/v1"


async def wait_turn(world, turn_id, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        t = await world.sessions.get_turn(turn_id)
        if t.status != "running":
            return t
        await asyncio.sleep(0.02)
    raise AssertionError("轮次没有结束")


async def test_create_by_version_id_and_by_agent_tag(world, client):
    v1 = await world.config_version(name="svc")
    r = await client.post(
        f"{API}/sessions", json={"version_id": v1["id"], "title": "甲", "variables": {"k": 1}}
    )
    assert r.status_code == 201, r.text
    s = r.json()["session"]
    assert s["version_id"] == v1["id"] and s["title"] == "甲" and s["variables"] == {"k": 1}
    assert s["mode"] == "chat" and s["status"] == "idle" and s["agent_name"] == "svc"
    # agent + tag：latest（缺省）、vN、自定义标签
    for tag, expect in ((None, v1["id"]), ("latest", v1["id"]), (f"v{v1['seq']}", v1["id"])):
        body = {"agent": "svc"} | ({"tag": tag} if tag else {})
        r = await client.post(f"{API}/sessions", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["session"]["version_id"] == expect
    assert (await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": "prod"})).status_code == 200
    r = await client.post(f"{API}/sessions", json={"agent": "svc", "tag": "prod"})
    assert r.json()["session"]["version_id"] == v1["id"]
    # 错误
    assert (await client.post(f"{API}/sessions", json={})).status_code == 400
    assert (
        await client.post(f"{API}/sessions", json={"version_id": v1["id"], "agent": "svc"})
    ).status_code == 400
    assert (await client.post(f"{API}/sessions", json={"tag": "prod"})).status_code == 400
    assert (await client.post(f"{API}/sessions", json={"version_id": "ver_nope"})).status_code == 404
    assert (await client.post(f"{API}/sessions", json={"agent": "svc", "tag": "nope"})).status_code == 404
    assert (await client.post(f"{API}/sessions", json={"agent": "nope"})).status_code == 404


async def test_turn_wait_true_returns_200_and_detail_include(world, client, fake_openai):
    v = await world.config_version()
    s = (await client.post(f"{API}/sessions", json={"version_id": v["id"]})).json()["session"]
    fake_openai.enqueue("你好呀")
    r = await client.post(f"{API}/sessions/{s['id']}/turns", json={"input": "你好", "wait": True})
    assert r.status_code == 200, r.text
    body = r.json()
    t = body["turn"]
    assert t["status"] == "ok" and t["idx"] == 0 and t["output"]["final"]["content"] == "你好呀"
    assert t["input"]["content"] == "你好" and t["usage"]["llm_calls"] == 1 and t["latency_ms"] is not None
    assert t["error"] is None and t["anchored_from_turn_id"] is None and t["finished_at"]
    assert body["turn_id"] == t["id"]
    # ?wait=true 覆盖请求体
    fake_openai.enqueue("二")
    r = await client.post(f"{API}/sessions/{s['id']}/turns?wait=true", json={"input": "再来"})
    assert r.status_code == 200 and r.json()["turn"]["idx"] == 1
    # 详情 include
    d = (await client.get(f"{API}/sessions/{s['id']}")).json()
    assert len(d["turns"]) == 2 and d["events"] is None and d["session"]["stats"]["turns"] == 2
    d = (await client.get(f"{API}/sessions/{s['id']}", params={"include": ""})).json()
    assert d["turns"] == [] and d["events"] is None
    d = (await client.get(f"{API}/sessions/{s['id']}", params={"include": "turns,events"})).json()
    assert d["events"][0]["type"] == "turn.start" and d["events"][-1]["type"] == "turn.end"
    assert [e["seq"] for e in d["events"]] == list(range(1, len(d["events"]) + 1))
    assert (await client.get(f"{API}/sessions/{s['id']}", params={"include": "bogus"})).status_code == 400
    assert (await client.get(f"{API}/sessions/ses_nope")).status_code == 404


async def test_turn_202_cancel_and_error_not_http_error(world, client, fake_openai):
    v = await world.config_version()
    s = (await client.post(f"{API}/sessions", json={"version_id": v["id"]})).json()["session"]
    fake_openai.slow(30)
    r = await client.post(f"{API}/sessions/{s['id']}/turns", json={"input": "慢"})
    assert r.status_code == 202
    body = r.json()
    assert body["turn"]["status"] == "running" and body["stream_url"].endswith("after_seq=0")
    await asyncio.sleep(0.2)
    r = await client.post(f"{API}/turns/{body['turn_id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await client.post(f"{API}/turns/{body['turn_id']}/cancel")).json()["status"] == "cancelled"
    assert (await client.post(f"{API}/turns/trn_nope/cancel")).status_code == 404
    # 执行失败是 200 + status=error，不是 HTTP 错误
    fake_openai.clear_faults()
    fake_openai.fail(400, times=1, message="坏请求")
    r = await client.post(f"{API}/sessions/{s['id']}/turns", json={"input": "会失败", "wait": True})
    assert r.status_code == 200
    t = r.json()["turn"]
    assert t["status"] == "error" and t["error"]["code"] and "retriable" in t["error"]
    assert (await client.post(f"{API}/sessions/ses_nope/turns", json={"input": "x"})).status_code == 404
    assert (await client.post(f"{API}/sessions/{s['id']}/turns", json={})).status_code == 400


async def test_turn_variables_and_tool_mocks(world, client, fake_openai):
    await world.api.asset(
        "tool",
        "q",
        "name: query_order\ndescription: d\n"
        "parameters: {type: object, properties: {order_id: {type: string}}}\n"
        "impl: {type: mock, responses: [{default: {status: 原始}}]}\n",
    )
    v = await world.config_version(
        {
            "tools": ["asset://tool/q@latest"],
            "prompts": {"system": "渠道={{ variables.ch }}", "variables": {"ch": "web"}},
        }
    )
    s = (await client.post(f"{API}/sessions", json={"version_id": v["id"]})).json()["session"]
    fake_openai.add_rule(match={"has_tool": True}, reply="完成")
    fake_openai.add_rule(match="订单", reply=[Call("query_order", {"order_id": "A1"}, id="c1")])
    r = await client.post(
        f"{API}/sessions/{s['id']}/turns",
        json={
            "input": "查订单",
            "wait": True,
            "variables": {"ch": "app"},
            "tool_mocks": {"query_order": [{"match": {"order_id": "A1"}, "output": {"status": "被覆盖"}}]},
        },
    )
    assert r.status_code == 200, r.text
    assert fake_openai.chat_requests[0].system == "渠道=app"
    ev = (await client.get(f"{API}/sessions/{s['id']}", params={"include": "events"})).json()["events"]
    result = next(e for e in ev if e["type"] == "tool.result")
    assert result["payload"]["output"] == {"status": "被覆盖"} and result["payload"]["mocked"] is True
