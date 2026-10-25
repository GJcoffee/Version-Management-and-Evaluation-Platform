"""详设 15.2 验收标准 A1（草稿与版本）、A2（资产）。"""

import copy

import pytest
from evalp.ids import new_id
from evalp.store.models import EvalBatch, EvalRun, VersionAssetRef
from sqlalchemy import func, select

from .conftest import API, TOOL_YAML, config_spec

JSONL_V1 = '{"case_key":"a","input":"你好"}\n{"case_key":"b","input":"再见"}\n'
JSONL_V2 = '{"case_key":"a","input":"你好呀"}\n{"case_key":"c","input":"新问题"}\n'


# ---------------------------------------------------------------- A1


async def test_a1_draft_and_version(api, client, ctx):
    """相同内容重复发布只得到一个版本；修改资产但没有重新发布时，已有版本的哈希不变；
    发布后，从版本创建的草稿能还原出等价的 Spec。"""
    await api.provider()
    prompt = await api.asset("prompt", "cs-system", "你是客服助手。")
    await api.asset("tool", "query-order", TOOL_YAML)
    spec = config_spec(
        prompts={"system": "asset://prompt/cs-system@latest", "variables": {"company": "示例公司"}},
        tools=["asset://tool/query-order@latest"],
    )
    out = await api.agent("customer-support", spec=spec)
    draft = out["draft"]

    # 1. 相同内容重复发布只得到一个版本
    results = [await api.publish(draft) for _ in range(3)]
    assert [r["existed"] for r in results] == [False, True, True]
    assert len({r["version"]["id"] for r in results}) == 1
    # 换一种写法（显式默认值、不同的显示字段、字段顺序）仍然是同一个版本
    reordered = {
        "prompts": {"variables": {"company": "示例公司"}, "system": "asset://prompt/cs-system@latest"},
        "tools": ["asset://tool/query-order@latest"],
        "model": {"name": "deepseek-chat", "provider": "deepseek", "params": {"temperature": 0.3}},
        "kind": "config",
        "notes": "换了个备注",
        "runtime": {"max_steps": 12},
    }
    saved = (await api.save(draft, reordered))["draft"]
    assert (await api.publish(saved))["existed"] is True
    versions = (await client.get(f"{API}/agents/customer-support/versions")).json()["items"]
    assert len(versions) == 1
    v1 = results[0]["version"]

    # 2. 修改资产但没有重新发布：已有版本的哈希（和内容）不变
    await api.revision(prompt["asset"]["id"], "你是全新的客服助手。")
    still = (await client.get(f"{API}/versions/{v1['id']}")).json()
    assert still["spec_hash"] == v1["spec_hash"] and still["spec"] == v1["spec"]
    assert still["spec"]["prompts"]["system"]["$asset"]["rev"] == 1
    # 重新发布才会产生新版本
    v2 = await api.publish(saved)
    assert v2["existed"] is False and v2["version"]["spec_hash"] != v1["spec_hash"]

    # 3. 从版本创建的草稿能还原出等价的 Spec
    for version in (v1, v2["version"]):
        restored = (
            await client.post(f"{API}/versions/{version['id']}/to-draft", json={"draft_name": "restore"})
        ).json()["draft"]
        check = (await client.post(f"{API}/drafts/{restored['id']}/validate")).json()
        assert check["ok"] is True
        assert check["resolved_preview"] == version["spec"]  # 固化后与版本 Spec 完全一致
        again = await api.publish(restored)
        assert again["existed"] is True and again["version"]["id"] == version["id"]


# ---------------------------------------------------------------- A2


CONTENTS = {
    "prompt": ("你是客服。\n", "你是新的客服。\n请礼貌。\n"),
    "tool": (TOOL_YAML, TOOL_YAML.replace("已发货", "已签收")),
    "knowledge": ("# FAQ\n退货 7 天\n", "# FAQ\n退货 15 天\n"),
    "dataset": (JSONL_V1, JSONL_V2),
    "file": ("config=1\n", "config=2\n"),
}


@pytest.mark.parametrize("kind", list(CONTENTS))
async def test_a2_every_kind_has_revisions_diff_and_references(api, client, kind):
    v1, v2 = CONTENTS[kind]
    first = await api.asset(kind, f"my-{kind}", v1)
    aid = first["asset"]["id"]
    second = await api.revision(aid, v2)
    assert (first["revision"]["rev"], second["revision"]["rev"]) == (1, 2)
    revs = (await client.get(f"{API}/assets/{aid}/revisions")).json()["items"]
    assert [r["rev"] for r in revs] == [2, 1]
    # diff
    diff = (
        await client.get(
            f"{API}/assets/diff", params={"left": first["revision"]["id"], "right": second["revision"]["id"]}
        )
    ).json()
    assert diff["kind"] == kind and diff["identical"] is False
    if kind == "dataset":
        assert diff["type"] == "dataset"
        assert (
            diff["summary"]["added"] == 1
            and diff["summary"]["removed"] == 1
            and diff["summary"]["modified"] == 1
        )
    else:
        assert diff["type"] == "text" and diff["content_diff"]["text"].startswith("@@")
    # 引用查询（尚无人引用）
    refs = (await client.get(f"{API}/assets/{aid}/references")).json()
    assert refs["versions"] == [] and refs["evals"] == [] and refs["in_use"] is False
    # 没有引用时可以硬删除
    r = await client.delete(f"{API}/assets/{aid}", params={"hard": "true"})
    assert r.status_code == 200


async def test_a2_referenced_assets_cannot_be_hard_deleted(api, client, ctx):
    await api.provider()
    created = {k: await api.asset(k, f"my-{k}", CONTENTS[k][0]) for k in CONTENTS}
    spec = config_spec(
        prompts={"system": "asset://prompt/my-prompt@latest"},
        tools=[
            "asset://tool/my-tool@latest",
            {"builtin": "knowledge_search", "with": {"sources": ["asset://knowledge/my-knowledge@latest"]}},
        ],
    )
    out = await api.agent("bot", spec=spec)
    version = (await api.publish(out["draft"]))["version"]
    # 数据集被评测引用；file 没有 Spec 位置可以引用，这里直接写入引用行来验证规则
    async with ctx.db.write() as s:
        s.add(EvalBatch(id="evb_x", name="b", created_via="api", created_at="2026-01-01T00:00:00.000Z"))
        await s.flush()
        s.add(
            EvalRun(
                id=new_id("evr"),
                batch_id="evb_x",
                agent_id=version["agent_id"],
                version_id=version["id"],
                dataset_rev_id=created["dataset"]["revision"]["id"],
                evaluators=[],
                evaluators_hash="h",
                status="done",
                created_at="2026-01-01T00:00:00.000Z",
            )
        )
        s.add(
            VersionAssetRef(
                version_id=version["id"],
                asset_revision_id=created["file"]["revision"]["id"],
                spec_path="params.file",
            )
        )
    for kind, res in created.items():
        aid = res["asset"]["id"]
        refs = (await client.get(f"{API}/assets/{aid}/references")).json()
        assert refs["in_use"] is True, kind
        assert refs["evals"] if kind == "dataset" else refs["versions"], kind
        r = await client.delete(f"{API}/assets/{aid}", params={"hard": "true"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "ASSET_IN_USE", kind
        assert (await client.get(f"{API}/assets/{aid}")).status_code == 200
        # 软删除始终可以
        r = await client.delete(f"{API}/assets/{aid}")
        assert r.status_code == 200 and r.json()["deleted"] == "soft"
    # 软删除后，版本里的引用与内容仍然完整
    got = (await client.get(f"{API}/versions/{version['id']}")).json()
    assert got["spec"] == version["spec"]
    async with ctx.db.read() as s:
        n = (await s.execute(select(func.count()).select_from(VersionAssetRef))).scalar_one()
    assert n >= 4


async def test_a2_new_version_picks_up_new_revision_only_after_republish(api, client):
    await api.provider()
    a = await api.asset("knowledge", "faq", "v1")
    spec = config_spec(knowledge={"inject": ["asset://knowledge/faq@latest"]})
    out = await api.agent("bot", spec=spec)
    v1 = (await api.publish(out["draft"]))["version"]
    await api.revision(a["asset"]["id"], "v2")
    refs = (await client.get(f"{API}/assets/{a['asset']['id']}/references")).json()
    assert [(v["seq"], v["rev"]) for v in refs["versions"]] == [(1, 1)]
    v2 = (await api.publish(out["draft"]))["version"]
    refs = (await client.get(f"{API}/assets/{a['asset']['id']}/references")).json()
    assert sorted((v["seq"], v["rev"]) for v in refs["versions"]) == [(1, 1), (2, 2)]
    assert (await client.get(f"{API}/assets/{a['asset']['id']}/references", params={"rev": 2})).json()[
        "versions"
    ][0]["version_id"] == v2["id"]
    assert copy.deepcopy(v1)["seq"] == 1
