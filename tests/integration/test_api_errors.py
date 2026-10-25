from evalp.errors import Conflict, DraftConflict, NotFound
from fastapi import APIRouter
from pydantic import BaseModel


class Body(BaseModel):
    n: int
    name: str


router = APIRouter(prefix="/_t")


@router.post("/v")
async def v(body: Body):
    return {}


@router.get("/nf")
async def nf():
    raise NotFound("没有这个东西", {"id": "x"})


@router.get("/dc")
async def dc():
    raise DraftConflict("草稿已被修改（当前 revision=8）", {"current_revision": 8})


@router.get("/cf")
async def cf():
    raise Conflict("重复", code="WHATEVER")


@router.get("/boom")
async def boom():
    raise RuntimeError("secret internals")


async def test_validation_error_format(make_client):
    async with make_client(router) as client:
        r = await client.post("/api/v1/_t/v", json={"n": "x"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["message"]
    paths = {e["path"] for e in err["details"]["errors"]}
    assert paths == {"body.n", "body.name"}
    assert all("input" not in e for e in err["details"]["errors"])  # 不回显输入


async def test_malformed_json_is_validation_error(make_client):
    async with make_client(router) as client:
        r = await client.post(
            "/api/v1/_t/v", content=b"{not json", headers={"Content-Type": "application/json"}
        )
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_evalp_errors(make_client):
    async with make_client(router) as client:
        r = await client.get("/api/v1/_t/nf")
        assert (r.status_code, r.json()) == (
            404,
            {"error": {"code": "NOT_FOUND", "message": "没有这个东西", "details": {"id": "x"}}},
        )
        r = await client.get("/api/v1/_t/dc")
        assert r.status_code == 409
        assert r.json()["error"] == {
            "code": "DRAFT_CONFLICT",
            "message": "草稿已被修改（当前 revision=8）",
            "details": {"current_revision": 8},
        }
        r = await client.get("/api/v1/_t/cf")
        assert (r.status_code, r.json()["error"]["code"]) == (409, "WHATEVER")


async def test_unhandled_exception_is_500_without_leak(make_client):
    async with make_client(router, raise_app_exceptions=False) as client:
        r = await client.get("/api/v1/_t/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internals" not in r.text


async def test_404_and_405(client):
    r = await client.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await client.delete("/api/v1/health")
    assert r.status_code == 405 and "error" in r.json()
