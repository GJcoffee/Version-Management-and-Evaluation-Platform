"""数据集：导入、用例查询、修订 diff（详设 5.3、5.4、8.4）。"""

import json

from evalp.schemas.asset import CaseFilter
from evalp.services.dataset import DatasetService
from evalp.store.models import EvalCase
from evalp.utils.canonical import canonical_json, sha256_hex
from sqlalchemy import select

from .conftest import API


def jl(*rows) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


CASES = [
    {
        "case_key": "refund-001",
        "input": "我要退货，订单 A001",
        "expected": "说明退货流程",
        "tags": ["refund", "p0"],
        "metadata": {"variables": {"company": "测试公司"}},
    },
    {"case_key": "refund-002", "input": ["我要退货", "订单 A002"], "rubric": "提到 7 天", "tags": ["refund"]},
    {"case_key": "ship-001", "input": "什么时候发货", "tags": ["shipping", "p0"]},
]


async def import_(client, name="regression", content=None, fmt="jsonl", **extra):
    r = await client.post(
        f"{API}/datasets/import",
        json={
            "name": name,
            "content": content if content is not None else jl(*CASES),
            "format": fmt,
            **extra,
        },
    )
    return r


async def test_import_jsonl(client, ctx):
    r = await import_(client)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["case_count"] == 3 and out["warnings"] == [] and out["existed"] is False
    assert out["asset"]["kind"] == "dataset" and out["asset"]["name"] == "regression"
    assert out["revision"]["content_type"] == "application/jsonl"
    assert out["revision"]["meta"]["case_count"] == 3
    rev_id = out["revision"]["id"]
    async with ctx.db.read() as s:
        rows = (
            (
                await s.execute(
                    select(EvalCase).where(EvalCase.dataset_rev_id == rev_id).order_by(EvalCase.idx)
                )
            )
            .scalars()
            .all()
        )
    assert [c.case_key for c in rows] == ["refund-001", "refund-002", "ship-001"]
    assert [c.idx for c in rows] == [0, 1, 2]
    assert rows[0].input == {"turns": ["我要退货，订单 A001"]}
    assert rows[1].input == {"turns": ["我要退货", "订单 A002"]}  # 多轮
    assert rows[0].metadata_ == {"variables": {"company": "测试公司"}}
    assert rows[1].rubric == "提到 7 天" and rows[1].expected is None
    assert rows[0].tags == ["refund", "p0"]
    # case_hash（详设 8.4）
    expected = sha256_hex(
        canonical_json(
            {
                "input": {"turns": ["我要退货，订单 A001"]},
                "variables": {"company": "测试公司"},
                "tool_mocks": {},
            }
        )
    )
    assert rows[0].case_hash == expected


async def test_import_csv_with_column_map(client):
    csv_text = "问题,参考答案,标签(;分隔)\n我要退货,说明退货流程,refund;p0\n什么时候发货,,shipping\n"
    r = await import_(
        client,
        "csv-ds",
        csv_text,
        "csv",
        column_map={"input": "问题", "expected": "参考答案", "tags": "标签(;分隔)"},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["case_count"] == 2 and out["revision"]["content_type"] == "text/csv"
    assert out["warnings"][0]["code"] == "MISSING_CASE_KEY" and out["warnings"][0]["count"] == 2
    assert out["revision"]["meta"]["column_map"]["input"] == "问题"
    cases = (await client.get(f"{API}/datasets/{out['revision']['id']}/cases")).json()["items"]
    assert cases[0]["input"] == {"turns": ["我要退货"]} and cases[0]["tags"] == ["refund", "p0"]
    assert cases[0]["expected"] == "说明退货流程" and cases[1]["expected"] is None
    assert cases[0]["case_key"] == sha256_hex("我要退货")[:12]


async def test_import_csv_requires_input_mapping(client):
    r = await import_(client, "csv-ds", "a,b\n1,2\n", "csv")
    assert r.status_code == 422 and r.json()["error"]["code"] == "DATASET_PARSE_ERROR"


async def test_missing_case_key_generates_key_and_warns(client):
    r = await import_(client, "nokey", jl({"input": "你好"}, {"case_key": "k", "input": "x"}))
    out = r.json()
    (w,) = out["warnings"]
    assert w["code"] == "MISSING_CASE_KEY" and w["lines"] == [1] and "跨修订" in w["message"]
    assert out["revision"]["meta"]["schema_warnings"] == out["warnings"]
    cases = (await client.get(f"{API}/datasets/{out['revision']['id']}/cases")).json()["items"]
    assert cases[0]["case_key"] == sha256_hex("你好")[:12]


async def test_parse_error_lists_bad_lines(client):
    text = '{"case_key": "ok", "input": "x"}\n{bad json\n{"case_key": "k"}\n[1]\n'
    r = await import_(client, "broken", text)
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "DATASET_PARSE_ERROR"
    assert e["details"]["lines"] == [2, 3, 4]
    assert [x["line"] for x in e["details"]["errors"]] == [2, 3, 4]
    assert (await client.get(f"{API}/assets")).json()["items"] == []  # 失败不创建资产


async def test_import_name_and_format_validation(client):
    assert (await import_(client, "Bad Name")).status_code == 400
    r = await client.post(f"{API}/datasets/import", json={"name": "x", "content": "x", "format": "xml"})
    assert r.status_code == 400


async def test_reimport_same_content_is_existing_revision(client):
    first = (await import_(client)).json()
    r = await import_(client)
    assert r.status_code == 200
    again = r.json()
    assert again["existed"] is True and again["revision"]["id"] == first["revision"]["id"]
    assert again["case_count"] == 3


async def test_import_new_revision_for_same_name(client):
    first = (await import_(client)).json()
    r = await import_(client, content=jl(*CASES, {"case_key": "new-1", "input": "新问题"}))
    out = r.json()
    assert r.status_code == 200 and out["existed"] is False and out["created"] is False
    assert out["revision"]["rev"] == 2 and out["case_count"] == 4
    assert out["asset"]["id"] == first["asset"]["id"] and out["asset"]["latest_rev"] == 2


async def test_cases_endpoint_filters_and_pagination(client):
    rev = (await import_(client)).json()["revision"]["id"]
    url = f"{API}/datasets/{rev}/cases"
    allc = (await client.get(url)).json()
    assert [c["case_key"] for c in allc["items"]] == ["refund-001", "refund-002", "ship-001"]
    assert set(allc["items"][0]) == {
        "id",
        "dataset_rev_id",
        "case_key",
        "idx",
        "input",
        "expected",
        "rubric",
        "tags",
        "metadata",
        "case_hash",
    }
    p1 = (await client.get(url, params={"limit": 2})).json()
    assert [c["case_key"] for c in p1["items"]] == ["refund-001", "refund-002"] and p1["next_cursor"]
    p2 = (await client.get(url, params={"limit": 2, "cursor": p1["next_cursor"]})).json()
    assert [c["case_key"] for c in p2["items"]] == ["ship-001"] and p2["next_cursor"] is None
    by_tag = (await client.get(url, params={"tag": "p0"})).json()["items"]
    assert [c["case_key"] for c in by_tag] == ["refund-001", "ship-001"]
    any_tag = (await client.get(url, params=[("tag", "shipping"), ("tag", "refund")])).json()["items"]
    assert len(any_tag) == 3
    by_key = (await client.get(url, params=[("case_key", "ship-001"), ("case_key", "nope")])).json()["items"]
    assert [c["case_key"] for c in by_key] == ["ship-001"]
    both = (await client.get(url, params={"tag": "p0", "case_key": "refund-002"})).json()["items"]
    assert both == []
    assert [c["case_key"] for c in (await client.get(url, params={"q": "refund"})).json()["items"]] == [
        "refund-001",
        "refund-002",
    ]
    assert (await client.get(f"{API}/datasets/arv_nope/cases")).status_code == 404


async def test_cases_of_non_dataset_revision_is_404(api, client):
    p = await api.asset("prompt", "p", "x")
    assert (await client.get(f"{API}/datasets/{p['revision']['id']}/cases")).status_code == 404


async def test_dataset_diff_by_case_key(client):
    v1 = (await import_(client)).json()
    edited = [dict(c) for c in CASES]
    edited[0]["expected"] = "新的期望"  # 只改 expected -> 修改
    edited[2]["input"] = "发货需要多久"  # 改 input -> 修改
    edited = edited[:3] + [{"case_key": "new-1", "input": "新增"}]
    edited.pop(1)  # 删除 refund-002
    v2 = (await import_(client, content=jl(*edited))).json()
    r = await client.get(
        f"{API}/assets/diff", params={"left": v1["revision"]["id"], "right": v2["revision"]["id"]}
    )
    d = r.json()
    assert d["type"] == "dataset" and d["identical"] is False
    assert d["summary"] == {
        "added": 1,
        "removed": 1,
        "modified": 2,
        "unchanged": 0,
        "left_count": 3,
        "right_count": 3,
    }
    assert [x["case_key"] for x in d["added"]] == ["new-1"]
    assert [x["case_key"] for x in d["removed"]] == ["refund-002"]
    mod = {m["case_key"]: m for m in d["modified"]}
    assert mod["refund-001"]["changed_fields"] == ["expected"]
    assert mod["refund-001"]["left"] == {"expected": "说明退货流程"} and mod["refund-001"]["right"] == {
        "expected": "新的期望"
    }
    assert mod["ship-001"]["changed_fields"] == ["input"]
    assert d["truncated"] is False


async def test_dataset_diff_unchanged_cases_counted(client):
    v1 = (await import_(client)).json()
    v2 = (await import_(client, content=jl(*CASES[:2]))).json()
    d = (
        await client.get(
            f"{API}/assets/diff", params={"left": v1["revision"]["id"], "right": v2["revision"]["id"]}
        )
    ).json()
    assert d["summary"]["unchanged"] == 2 and d["summary"]["removed"] == 1 and d["summary"]["modified"] == 0


# ---------------------------------------------------------------- 供评测模块使用的服务方法


async def test_service_list_cases_and_filter(client, ctx):
    rev = (await import_(client)).json()["revision"]["id"]
    svc = ctx.svc(DatasetService)
    cases = await svc.list_cases(rev)
    assert [c.case_key for c in cases] == ["refund-001", "refund-002", "ship-001"]
    assert [c.case_key for c in await svc.list_cases(rev, {"tags": ["p0"]})] == ["refund-001", "ship-001"]
    assert [c.case_key for c in await svc.list_cases(rev, CaseFilter(case_keys=["refund-002"]))] == [
        "refund-002"
    ]
    assert [c.case_key for c in await svc.list_cases(rev, {"tags": [], "case_keys": []})] == [
        c["case_key"] for c in CASES
    ]
    assert await svc.case_count(rev) == 3
    c = await svc.get_case(cases[0].id)
    assert c.case_hash == cases[0].case_hash and c.metadata == {"variables": {"company": "测试公司"}}


async def test_service_resolve_dataset_ref(client, ctx):
    first = (await import_(client)).json()
    second = (await import_(client, content=jl(*CASES[:2]))).json()
    svc = ctx.svc(DatasetService)
    latest = await svc.resolve_dataset("asset://dataset/regression@latest")
    assert latest.revision_id == second["revision"]["id"] and latest.rev == 2
    pinned = await svc.resolve_dataset("asset://dataset/regression@1")
    assert pinned.revision_id == first["revision"]["id"] and pinned.hash == first["revision"]["blob_hash"]
    assert pinned.pin == {"$asset": {"kind": "dataset", "name": "regression", "rev": 1, "hash": pinned.hash}}
    assert len(await svc.list_cases(pinned.revision_id)) == 3
    import pytest
    from evalp.errors import SpecInvalid

    for bad in ("asset://dataset/missing@latest", "asset://dataset/regression@9", "asset://prompt/x@1"):
        with pytest.raises(SpecInvalid):
            await svc.resolve_dataset(bad)
