import httpx
import pytest
import respx
from evalp.services.provider import ProviderService

OUT_KEYS = {
    "id", "name", "base_url", "has_api_key", "headers", "models", "rpm_limit", "tpm_limit",
    "max_concurrency", "created_at", "updated_at",
}  # fmt: skip


async def create(client, **kw):
    body = {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", **kw}
    r = await client.post("/api/v1/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_minimal_defaults(client):
    p = await create(client)
    assert set(p) == OUT_KEYS
    assert p["id"].startswith("prv_")
    assert p["has_api_key"] is False
    assert (p["headers"], p["models"], p["rpm_limit"], p["tpm_limit"], p["max_concurrency"]) == (
        {},
        [],
        None,
        None,
        8,
    )
    assert p["created_at"] == p["updated_at"] and p["created_at"].endswith("Z")


async def test_create_full_and_secret_never_returned(client, ctx):
    models = [
        {"name": "deepseek-chat", "context_window": 65536, "price_in": 2.0, "price_out": 8.0},
        {"name": "deepseek-reasoner"},
    ]
    r = await client.post(
        "/api/v1/providers",
        json={
            "name": "ds", "base_url": "https://api.deepseek.com/v1/", "api_key": "sk-TOPSECRET",
            "headers": {"X-Org": "acme"}, "models": models, "rpm_limit": 60, "tpm_limit": 100000,
            "max_concurrency": 4,
        },
    )  # fmt: skip
    assert r.status_code == 201
    p = r.json()
    assert "TOPSECRET" not in r.text and "api_key" not in p
    assert p["has_api_key"] is True
    assert p["base_url"] == "https://api.deepseek.com/v1"  # 去掉末尾斜杠
    assert p["models"][0] == models[0]
    assert p["models"][1] == {
        "name": "deepseek-reasoner",
        "context_window": None,
        "price_in": None,
        "price_out": None,
    }
    assert (p["rpm_limit"], p["tpm_limit"], p["max_concurrency"], p["headers"]) == (
        60,
        100000,
        4,
        {"X-Org": "acme"},
    )
    # 所有读取接口都不含密钥
    for resp in (await client.get(f"/api/v1/providers/{p['id']}"), await client.get("/api/v1/providers")):
        assert "TOPSECRET" not in resp.text
    # 密钥只存在 secrets 中；库里只有引用
    assert await ctx.secrets.get(f"provider:{p['id']}") == "sk-TOPSECRET"
    assert await ctx.svc(ProviderService).get_api_key(p["id"]) == "sk-TOPSECRET"
    import sqlite3

    con = sqlite3.connect(ctx.config.db_path)
    assert "TOPSECRET" not in "".join(str(c) for row in con.execute("select * from providers") for c in row)
    assert con.execute("select api_key_ref from providers").fetchone() == (f"provider:{p['id']}",)
    con.close()
    assert b"TOPSECRET" not in ctx.config.secrets_path.read_bytes()


async def test_service_get_by_name(client, ctx):
    p = await create(client, api_key="k1")
    svc = ctx.svc(ProviderService)
    assert (await svc.get_by_name("deepseek")).id == p["id"]
    assert (await svc.find_by_name("nope")) is None
    from evalp.errors import NotFound

    with pytest.raises(NotFound):
        await svc.get_by_name("nope")
    assert await svc.get_api_key(p["id"]) == "k1"
    other = await create(client, name="nokey")
    assert await svc.get_api_key(other["id"]) is None
    assert ctx.svc(ProviderService) is svc  # 单例


async def test_get_list_and_404(client):
    a, b = await create(client, name="a"), await create(client, name="b")
    assert (await client.get(f"/api/v1/providers/{a['id']}")).json() == a
    items = (await client.get("/api/v1/providers")).json()["items"]
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    r = await client.get("/api/v1/providers/prv_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert (await client.patch("/api/v1/providers/prv_missing", json={"name": "x"})).status_code == 404
    assert (await client.delete("/api/v1/providers/prv_missing")).status_code == 404
    assert (await client.post("/api/v1/providers/prv_missing/test")).status_code == 404


async def test_duplicate_name_conflict(client):
    a = await create(client, name="a", api_key="k")
    r = await client.post("/api/v1/providers", json={"name": "a", "base_url": "http://x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    b = await create(client, name="b")
    r = await client.patch(f"/api/v1/providers/{b['id']}", json={"name": "a"})
    assert r.status_code == 409
    assert (await client.get(f"/api/v1/providers/{a['id']}")).json()["name"] == "a"
    assert len((await client.get("/api/v1/providers")).json()["items"]) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"base_url": "http://x"},
        {"name": "", "base_url": "http://x"},
        {"name": "n", "base_url": "ftp://x"},
        {"name": "n", "base_url": "not a url"},
        {"name": "n", "base_url": "http://x", "max_concurrency": 0},
        {"name": "n", "base_url": "http://x", "rpm_limit": -1},
        {"name": "n", "base_url": "http://x", "models": [{"name": "a"}, {"name": "a"}]},
        {"name": "n", "base_url": "http://x", "models": [{"context_window": 1}]},
        {"name": "n", "base_url": "http://x", "headers": {"a": 1}},
        {"name": "n", "base_url": "http://x", "unknown": 1},
    ],
)
async def test_create_validation(client, body):
    r = await client.post("/api/v1/providers", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR" and r.json()["error"]["details"]["errors"]


async def test_patch_is_partial(client, ctx):
    p = await create(client, api_key="k", headers={"X": "1"}, rpm_limit=10, models=[{"name": "m"}])
    r = await client.patch(f"/api/v1/providers/{p['id']}", json={"name": "renamed"})
    assert r.status_code == 200
    q = r.json()
    assert q["name"] == "renamed" and q["has_api_key"] is True
    for k in ("base_url", "headers", "models", "rpm_limit", "max_concurrency", "created_at"):
        assert q[k] == p[k], k
    assert q["updated_at"] >= p["updated_at"]
    assert await ctx.svc(ProviderService).get_api_key(p["id"]) == "k"  # 未提供 api_key -> 不变

    r = await client.patch(
        f"/api/v1/providers/{p['id']}",
        json={
            "base_url": "http://other/v1",
            "headers": {},
            "models": [{"name": "m2", "price_in": 1}],
            "tpm_limit": 5,
        },
    )
    q = r.json()
    assert (q["base_url"], q["headers"], q["tpm_limit"], q["rpm_limit"]) == ("http://other/v1", {}, 5, 10)
    assert q["models"] == [{"name": "m2", "context_window": None, "price_in": 1.0, "price_out": None}]

    # 显式 null 表示不限
    q = (await client.patch(f"/api/v1/providers/{p['id']}", json={"rpm_limit": None})).json()
    assert q["rpm_limit"] is None and q["tpm_limit"] == 5


async def test_patch_api_key_set_replace_delete(client, ctx):
    p = await create(client)
    svc = ctx.svc(ProviderService)
    assert p["has_api_key"] is False
    q = (await client.patch(f"/api/v1/providers/{p['id']}", json={"api_key": "k1"})).json()
    assert q["has_api_key"] is True and await svc.get_api_key(p["id"]) == "k1"
    q = (await client.patch(f"/api/v1/providers/{p['id']}", json={"api_key": "k2"})).json()
    assert await svc.get_api_key(p["id"]) == "k2"
    r = await client.patch(f"/api/v1/providers/{p['id']}", json={"api_key": ""})
    assert r.json()["has_api_key"] is False
    assert await svc.get_api_key(p["id"]) is None
    assert await ctx.secrets.get(f"provider:{p['id']}") is None


async def test_patch_validation(client):
    p = await create(client)
    for body in ({"base_url": "nope"}, {"max_concurrency": 0}, {"bogus": 1}):
        r = await client.patch(f"/api/v1/providers/{p['id']}", json=body)
        assert r.status_code == 400, body


async def test_delete_removes_secret(client, ctx):
    p = await create(client, api_key="k")
    r = await client.delete(f"/api/v1/providers/{p['id']}")
    assert r.status_code == 204 and r.content == b""
    assert (await client.get(f"/api/v1/providers/{p['id']}")).status_code == 404
    assert await ctx.secrets.get(f"provider:{p['id']}") is None


# ---------------------------------------------------------------- POST /providers/{id}/test


@respx.mock
async def test_connectivity_ok(client):
    p = await create(client, base_url="https://llm.example/v1/", api_key="sk-abc", headers={"X-Org": "acme"})
    route = respx.get("https://llm.example/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "m1"}, {"id": "m2"}]})
    )
    r = await client.post(f"/api/v1/providers/{p['id']}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["models"] == ["m1", "m2"] and isinstance(body["latency_ms"], int)
    assert "error" not in body
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-abc" and sent.headers["x-org"] == "acme"
    assert sent.extensions["timeout"]["read"] == 10


@respx.mock
async def test_connectivity_without_key_sends_no_auth(client):
    p = await create(client, base_url="https://llm.example/v1")
    route = respx.get("https://llm.example/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    body = (await client.post(f"/api/v1/providers/{p['id']}/test")).json()
    assert body["ok"] is True and body["models"] == []
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_connectivity_non_standard_body_still_ok(client):
    p = await create(client, base_url="https://llm.example/v1")
    respx.get("https://llm.example/v1/models").mock(return_value=httpx.Response(200, text="<html>hi</html>"))
    body = (await client.post(f"/api/v1/providers/{p['id']}/test")).json()
    assert body["ok"] is True and "models" not in body


@respx.mock
async def test_connectivity_http_error(client):
    p = await create(client, base_url="https://llm.example/v1", api_key="sk-abc")
    respx.get("https://llm.example/v1/models").mock(
        return_value=httpx.Response(401, text="invalid key sk-abc")
    )
    r = await client.post(f"/api/v1/providers/{p['id']}/test")
    assert r.status_code == 200  # 测试接口本身成功，结果在 body 中
    body = r.json()
    assert body["ok"] is False and body["error"].startswith("HTTP 401")
    assert "sk-abc" not in r.text and "models" not in body


@respx.mock
async def test_connectivity_network_errors(client):
    p = await create(client, base_url="https://llm.example/v1")
    respx.get("https://llm.example/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    body = (await client.post(f"/api/v1/providers/{p['id']}/test")).json()
    assert body["ok"] is False and "refused" in body["error"]
    respx.get("https://llm.example/v1/models").mock(side_effect=httpx.ReadTimeout("slow"))
    body = (await client.post(f"/api/v1/providers/{p['id']}/test")).json()
    assert body["ok"] is False and "超时" in body["error"]
