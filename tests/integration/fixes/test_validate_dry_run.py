"""草稿干跑校验：传入 spec 时不改变已保存内容与 revision。"""


async def test_validate_with_body_does_not_save(client):
    r = await client.post("/api/v1/agents", json={"name": "dry-run", "kind": "config"})
    assert r.status_code == 201, r.text
    draft = r.json()["draft"]
    bad = {"schema": "evalp/v1", "kind": "config", "model": {"provider": "nope", "name": "x"}}
    r = await client.post(f"/api/v1/drafts/{draft['id']}/validate", json={"spec": bad})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False
    r2 = await client.get(f"/api/v1/drafts/{draft['id']}")
    assert r2.json()["revision"] == draft["revision"]
    assert r2.json()["spec"] == draft["spec"]
    r3 = await client.post(f"/api/v1/drafts/{draft['id']}/validate")
    assert r3.status_code == 200
