"""工作区同步：POST /workspace/push、/workspace/pull（详设 10）。"""

import base64
import hashlib

from evalp.store.models import Agent, Asset, AssetRevision
from sqlalchemy import func, select

from .conftest import API, TOOL_YAML


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def f(path: str, asset: str, content: str | None, **kw) -> dict:
    """工作区文件条目：content=None 表示只给哈希（hash 需要通过 hash= 传入）。"""
    h = kw.pop("hash", None) or sha(content or "")
    out = {"path": path, "asset": asset, "hash": h}
    if content is not None:
        out["content"] = content
    return {**out, **kw}


SPEC = {
    "kind": "config",
    "model": {"provider": "deepseek", "name": "deepseek-chat"},
    "prompts": {"system": "file:prompts/system.md", "variables": {"company": "示例公司"}},
    "tools": ["file:tools/query.yaml"],
    "knowledge": {"inject": ["file:knowledge/faq.md"]},
}
SYSTEM = "你是客服助手。\n"
FAQ = "# FAQ\n七天无理由退货。\n"


def files(system=SYSTEM, tool=TOOL_YAML, faq=FAQ) -> list[dict]:
    return [
        f("prompts/system.md", "prompt/cs-system", system),
        f("tools/query.yaml", "tool/query", tool),
        f("knowledge/faq.md", "knowledge/faq", faq),
    ]


def push_body(**over) -> dict:
    body = {"agent_name": "customer-support", "draft_name": "main", "spec": SPEC, "files": files()}
    body.update(over)
    return body


async def push(client, expect: int = 200, **over) -> dict:
    r = await client.post(f"{API}/workspace/push", json=push_body(**over))
    assert r.status_code == expect, r.text
    return r.json()


async def count(ctx, model) -> int:
    async with ctx.db.read() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# ---------------------------------------------------------------- push


async def test_first_push_creates_agent_assets_and_draft(client, ctx):
    out = await push(client)
    assert out["agent_created"] is True and out["agent_id"].startswith("agt_")
    d = out["draft"]
    assert d["name"] == "main" and d["revision"] == 1 and d["agent_id"] == out["agent_id"]
    # file: 引用被改写为 asset://…@latest；其他字段原样保存
    assert d["spec"]["prompts"]["system"] == "asset://prompt/cs-system@latest"
    assert d["spec"]["prompts"]["variables"] == {"company": "示例公司"}
    assert d["spec"]["tools"] == ["asset://tool/query@latest"]
    assert d["spec"]["knowledge"]["inject"] == ["asset://knowledge/faq@latest"]
    assert [(u["path"], u["asset"], u["rev"]) for u in out["uploaded"]] == [
        ("prompts/system.md", "prompt/cs-system", 1),
        ("tools/query.yaml", "tool/query", 1),
        ("knowledge/faq.md", "knowledge/faq", 1),
    ]
    assert out["skipped"] == []
    assert all(u["hash"] and u["revision_id"].startswith("arv_") for u in out["uploaded"])
    # 服务端状态
    assert (await client.get(f"{API}/agents/customer-support")).json()["kind"] == "config"
    cs = (await client.get(f"{API}/assets", params={"kind": "prompt"})).json()["items"][0]
    assert cs["name"] == "cs-system"
    rev = (await client.get(f"{API}/assets/{cs['id']}/revisions/latest")).json()
    assert rev["content"] == SYSTEM and rev["message"].startswith("push")


async def test_second_push_skips_unchanged_files(client, ctx):
    first = await push(client)
    second = await push(client, expected_revision=1)
    assert second["agent_created"] is False and second["agent_id"] == first["agent_id"]
    assert second["uploaded"] == [] and len(second["skipped"]) == 3
    assert {s["path"] for s in second["skipped"]} == {
        "prompts/system.md",
        "tools/query.yaml",
        "knowledge/faq.md",
    }
    assert second["skipped"][0]["rev"] == 1
    assert second["draft"]["revision"] == 2 and second["draft"]["id"] == first["draft"]["id"]
    assert await count(ctx, AssetRevision) == 3  # 没有新修订


async def test_only_changed_files_make_new_revisions(client):
    await push(client)
    out = await push(client, files=files(system="你是新的客服助手。\n"), expected_revision=1)
    assert [(u["path"], u["rev"], u["existed"]) for u in out["uploaded"]] == [("prompts/system.md", 2, False)]
    assert {s["path"] for s in out["skipped"]} == {"tools/query.yaml", "knowledge/faq.md"}


async def test_unchanged_file_may_omit_content(client):
    """content 为空表示客户端认为服务端已有该 hash 的内容；服务端校验。"""
    await push(client)
    fs = [
        f("prompts/system.md", "prompt/cs-system", None, hash=sha(SYSTEM)),
        f("tools/query.yaml", "tool/query", None, hash=sha(TOOL_YAML)),
        f("knowledge/faq.md", "knowledge/faq", None, hash=sha(FAQ)),
    ]
    out = await push(client, files=fs, expected_revision=1)
    assert out["uploaded"] == [] and len(out["skipped"]) == 3


async def test_missing_content_is_an_error_listing_files_and_nothing_is_written(client, ctx):
    fs = files()
    fs[0] = f("prompts/system.md", "prompt/cs-system", None, hash=sha("服务端没有的内容"))
    fs[2] = f("knowledge/faq.md", "knowledge/faq", None, hash=sha("也没有"))
    r = await client.post(f"{API}/workspace/push", json=push_body(files=fs))
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "CONTENT_MISSING"
    missing = err["details"]["missing"]
    assert [m["path"] for m in missing] == ["prompts/system.md", "knowledge/faq.md"]
    assert missing[0] == {
        "path": "prompts/system.md",
        "asset": "prompt/cs-system",
        "hash": sha("服务端没有的内容"),
    }
    # 事务整体回滚：agent、资产、草稿都没有创建
    assert (
        await count(ctx, Agent) == 0 and await count(ctx, Asset) == 0 and await count(ctx, AssetRevision) == 0
    )


async def test_hash_only_reuses_older_revision_and_moves_latest(client, ctx):
    await push(client)
    await push(client, files=files(system="第二版\n"), expected_revision=1)  # prompt 现在是 rev 2
    out = await push(
        client,
        files=[
            f("prompts/system.md", "prompt/cs-system", None, hash=sha(SYSTEM)),
            *files(system="第二版\n")[1:],
        ],
        expected_revision=2,
    )
    (u,) = out["uploaded"]
    assert u["rev"] == 1 and u["existed"] is True  # 回到 rev 1
    a = (await client.get(f"{API}/assets", params={"kind": "prompt"})).json()["items"][0]
    assert a["latest_rev"] == 1


async def test_hash_only_can_reuse_blob_from_another_asset(client):
    await push(client)
    fs = files()
    fs.append(f("prompts/copy.md", "prompt/cs-copy", None, hash=sha(SYSTEM)))  # 同内容的另一个资产
    out = await push(client, files=fs, expected_revision=1)
    assert [(u["path"], u["rev"], u["asset_created"]) for u in out["uploaded"]] == [
        ("prompts/copy.md", 1, True)
    ]


async def test_hash_mismatch_is_rejected(client, ctx):
    fs = files()
    fs[0]["hash"] = sha("别的内容")
    r = await client.post(f"{API}/workspace/push", json=push_body(files=fs))
    assert r.status_code == 400 and "不一致" in r.json()["error"]["message"]
    assert await count(ctx, Agent) == 0


async def test_invalid_file_content_rolls_back_everything(client, ctx):
    bad_tool = TOOL_YAML.replace("type: mock", "type: grpc")
    r = await client.post(f"{API}/workspace/push", json=push_body(files=files(tool=bad_tool)))
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["details"]["file"] == "tools/query.yaml"
    assert err["message"].startswith("tools/query.yaml")
    assert await count(ctx, Agent) == 0 and await count(ctx, AssetRevision) == 0  # 第一个文件的修订也回滚


async def test_conflict_when_draft_changed_elsewhere(client, ctx):
    first = await push(client)
    d = first["draft"]
    # 有人在 UI 里改了草稿
    r = await client.put(
        f"{API}/drafts/{d['id']}",
        json={"spec": {**d["spec"], "runtime": {"max_steps": 3}}, "expected_revision": 1},
    )
    assert r.status_code == 200
    revisions_before = await count(ctx, AssetRevision)
    r = await client.post(
        f"{API}/workspace/push", json=push_body(files=files(system="本地新改的提示词\n"), expected_revision=1)
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "DRAFT_CONFLICT" and err["details"]["current_revision"] == 2
    # 返回服务端草稿与本地 Spec 的 diff
    paths = {c["path"]: c for c in err["details"]["diff"]["changes"]}
    assert paths["runtime"]["op"] == "remove" and paths["runtime"]["left"] == {"max_steps": 3}  # 左侧是服务端
    assert err["details"]["server_spec"]["runtime"]["max_steps"] == 3
    # 冲突时没有产生新的资产修订，草稿也没被覆盖
    assert await count(ctx, AssetRevision) == revisions_before
    assert (await client.get(f"{API}/drafts/{d['id']}")).json()["revision"] == 2


async def test_force_overrides_conflict(client):
    first = await push(client)
    d = first["draft"]
    await client.put(
        f"{API}/drafts/{d['id']}", json={"spec": {**d["spec"], "notes": "ui"}, "expected_revision": 1}
    )
    out = await push(client, force=True, expected_revision=1)
    assert out["draft"]["revision"] == 3 and "notes" not in out["draft"]["spec"]


async def test_existing_draft_requires_expected_revision_unless_forced(client):
    await push(client)
    r = await client.post(f"{API}/workspace/push", json=push_body())
    assert r.status_code == 409 and r.json()["error"]["details"]["current_revision"] == 1
    assert (await push(client, force=True))["draft"]["revision"] == 2


async def test_unmapped_file_reference_is_rejected(client, ctx):
    spec = {**SPEC, "tools": ["file:tools/other.yaml"]}
    r = await client.post(f"{API}/workspace/push", json=push_body(spec=spec))
    assert r.status_code == 400
    assert r.json()["error"]["details"]["errors"][0]["path"] == "tools/other.yaml"
    assert await count(ctx, Agent) == 0


async def test_asset_refs_and_pinned_refs_pass_through(client, api):
    await api.asset("tool", "existing", TOOL_YAML.replace("query_order", "existing_tool"))
    spec = {**SPEC, "tools": ["file:tools/query.yaml", "asset://tool/existing@1"]}
    out = await push(client, spec=spec)
    assert out["draft"]["spec"]["tools"] == ["asset://tool/query@latest", "asset://tool/existing@1"]


async def test_files_not_referenced_by_spec_are_still_uploaded(client):
    fs = [
        *files(),
        f("datasets/regression.jsonl", "dataset/cs-regression", '{"case_key":"a","input":"你好"}\n'),
    ]
    out = await push(client, files=fs)
    ds = [u for u in out["uploaded"] if u["asset"] == "dataset/cs-regression"][0]
    assert ds["rev"] == 1
    a = (await client.get(f"{API}/assets", params={"kind": "dataset"})).json()["items"][0]
    assert a["latest"]["meta"]["case_count"] == 1


async def test_files_validation(client):
    dup_path = [*files(), f("prompts/system.md", "prompt/other", "x")]
    dup_asset = [*files(), f("prompts/other.md", "prompt/cs-system", "x")]
    bad_asset = [f("prompts/x.md", "prompt", "x")]
    bad_name = [f("prompts/x.md", "prompt/Bad_Name", "x")]
    bad_kind = [f("prompts/x.md", "model/x", "x")]
    for fs in (dup_path, dup_asset, bad_asset, bad_name, bad_kind):
        r = await client.post(f"{API}/workspace/push", json=push_body(files=fs))
        assert r.status_code == 400, fs
    bad_hash = [{"path": "a", "asset": "prompt/a", "hash": "xyz", "content": "x"}]
    assert (await client.post(f"{API}/workspace/push", json=push_body(files=bad_hash))).status_code == 400


async def test_base64_binary_file(client, ctx):
    data = bytes(range(256))
    fs = [
        {
            "path": "files/blob.bin",
            "asset": "file/blob",
            "hash": sha(data),
            "content": base64.b64encode(data).decode(),
            "encoding": "base64",
        }
    ]
    out = await push(client, files=fs, spec={"kind": "config", "model": {"provider": "p", "name": "m"}})
    assert out["uploaded"][0]["rev"] == 1
    a = (await client.get(f"{API}/assets", params={"kind": "file"})).json()["items"][0]
    assert await ctx.blobs.get_bytes(a["latest"]["blob_hash"]) == data
    bad = [{"path": "f", "asset": "file/f", "hash": sha("x"), "content": "!!!", "encoding": "base64"}]
    assert (await client.post(f"{API}/workspace/push", json=push_body(files=bad))).status_code == 400


async def test_agent_kind_handling(client):
    code_spec = {
        "kind": "code",
        "transport": "http",
        "http": {"base_url": "http://x"},
        "source": {"capture_git": False},
    }
    out = await push(client, agent_name="coder", spec=code_spec, files=[])
    assert out["agent_created"] and out["draft"]["spec"]["kind"] == "code"
    assert (await client.get(f"{API}/agents/coder")).json()["kind"] == "code"
    # agent_kind 与 spec.kind 不一致 / 与已有 agent 不一致
    r = await client.post(
        f"{API}/workspace/push", json=push_body(agent_name="x1", agent_kind="code", files=[])
    )
    assert r.status_code == 400
    r = await client.post(
        f"{API}/workspace/push",
        json=push_body(agent_name="coder", spec=code_spec, agent_kind="config", files=[], force=True),
    )
    assert r.status_code == 400
    # spec 没写 kind 时用 agent_kind
    out = await push(
        client,
        agent_name="coder2",
        agent_kind="code",
        spec={"transport": "http", "http": {"base_url": "http://y"}},
        files=[],
    )
    assert out["draft"]["spec"]["kind"] == "code"
    # 已有 config agent 推 code spec -> 拒绝
    await push(client)
    r = await client.post(f"{API}/workspace/push", json=push_body(spec=code_spec, files=[], force=True))
    assert r.status_code == 400


async def test_push_to_named_draft_also_creates_main(client):
    out = await push(client, draft_name="experiment")
    assert out["draft"]["name"] == "experiment"
    drafts = (await client.get(f"{API}/agents/customer-support/drafts")).json()
    assert sorted(d["name"] for d in drafts) == ["experiment", "main"]
    main = [d for d in drafts if d["name"] == "main"][0]
    assert main["spec"]["kind"] == "config"
    # 同一个 agent 的另一个草稿
    out2 = await push(client, draft_name="other", force=True)
    assert (
        out2["agent_created"] is False and out2["draft"]["name"] == "other" and out2["draft"]["revision"] == 1
    )


async def test_push_then_publish_end_to_end(api, client):
    await api.provider()
    out = await push(client)
    pub = await api.publish(out["draft"])
    v = pub["version"]
    assert v["spec"]["prompts"]["system"]["$asset"]["name"] == "cs-system"
    assert {a["name"] for a in v["assets"]} == {"cs-system", "query", "faq"}
    # 本地改文件后再次 push + publish -> 新版本；旧版本不变
    out2 = await push(
        client, files=files(system="第二版提示词\n"), expected_revision=out["draft"]["revision"]
    )
    pub2 = await api.publish(out2["draft"])
    assert pub2["version"]["seq"] == 2
    assert pub2["version"]["spec"]["prompts"]["system"]["$asset"]["rev"] == 2
    # 回到原来的文件内容 -> 回到同一个版本（改回旧内容也会让 @latest 指回旧修订）
    out3 = await push(client, files=files(), expected_revision=out2["draft"]["revision"])
    pub3 = await api.publish(out3["draft"])
    assert pub3["existed"] is True and pub3["version"]["id"] == v["id"]


# ---------------------------------------------------------------- pull

ASSET_MAP = {
    "prompt/cs-system": "prompts/system.md",
    "tool/query": "tools/query.yaml",
    "knowledge/faq": "knowledge/faq.md",
}


async def test_pull_returns_draft_and_latest_asset_contents(client):
    await push(client)
    await push(client, files=files(system="第二版\n"), expected_revision=1)
    r = await client.post(
        f"{API}/workspace/pull",
        json={"agent_name": "customer-support", "draft_name": "main", "asset_map": ASSET_MAP},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["draft"]["spec"]["prompts"]["system"] == "asset://prompt/cs-system@latest"
    assert out["draft"]["revision"] == 2 and out["missing"] == []
    by_path = {a["path"]: a for a in out["assets"]}
    assert set(by_path) == set(ASSET_MAP.values())
    p = by_path["prompts/system.md"]
    assert p["content"] == "第二版\n" and p["rev"] == 2 and p["hash"] == sha("第二版\n")
    assert (
        p["asset"] == "prompt/cs-system" and p["encoding"] == "utf-8" and p["content_type"] == "text/markdown"
    )
    # local_spec：引用还原为 file:
    assert out["local_spec"]["prompts"]["system"] == "file:prompts/system.md"
    assert out["local_spec"]["tools"] == ["file:tools/query.yaml"]
    assert out["local_spec"]["knowledge"]["inject"] == ["file:knowledge/faq.md"]
    assert out["local_spec"]["prompts"]["variables"] == {"company": "示例公司"}


async def test_pull_keeps_unmapped_and_pinned_refs(client, api):
    await api.asset("tool", "existing", TOOL_YAML.replace("query_order", "existing_tool"))
    spec = {**SPEC, "tools": ["file:tools/query.yaml", "asset://tool/existing@1"]}
    await push(client, spec=spec)
    r = await client.post(
        f"{API}/workspace/pull",
        json={
            "agent_name": "customer-support",
            "asset_map": {"tool/query": "tools/query.yaml", "tool/existing": "tools/existing.yaml"},
        },
    )
    out = r.json()
    assert out["local_spec"]["tools"] == ["file:tools/query.yaml", "asset://tool/existing@1"]  # @1 保持原样
    assert {a["asset"] for a in out["assets"]} == {"tool/query", "tool/existing"}


async def test_pull_reports_missing_assets_and_errors(client):
    await push(client)
    r = await client.post(
        f"{API}/workspace/pull",
        json={
            "agent_name": "customer-support",
            "asset_map": {"prompt/nope": "p.md", "prompt/cs-system": "prompts/system.md"},
        },
    )
    out = r.json()
    assert out["missing"] == ["prompt/nope"] and [a["asset"] for a in out["assets"]] == ["prompt/cs-system"]
    assert (await client.post(f"{API}/workspace/pull", json={"agent_name": "nobody"})).status_code == 404
    assert (
        await client.post(
            f"{API}/workspace/pull", json={"agent_name": "customer-support", "draft_name": "nope"}
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{API}/workspace/pull", json={"agent_name": "customer-support", "asset_map": {"bad": "x"}}
        )
    ).status_code == 400


async def test_pull_skips_soft_deleted_assets_and_encodes_binary(client):
    data = bytes([0xFF, 0xFE, 0x00, 0x01])
    fs = [
        {
            "path": "files/b.bin",
            "asset": "file/b",
            "hash": sha(data),
            "content": base64.b64encode(data).decode(),
            "encoding": "base64",
        },
        *files(),
    ]
    await push(client, files=fs)
    r = await client.post(
        f"{API}/workspace/pull",
        json={
            "agent_name": "customer-support",
            "asset_map": {"file/b": "files/b.bin", "prompt/cs-system": "p.md"},
        },
    )
    out = {a["asset"]: a for a in r.json()["assets"]}
    assert out["file/b"]["encoding"] == "base64" and base64.b64decode(out["file/b"]["content"]) == data
    aid = (await client.get(f"{API}/assets", params={"kind": "prompt"})).json()["items"][0]["id"]
    await client.delete(f"{API}/assets/{aid}")
    r = await client.post(
        f"{API}/workspace/pull",
        json={"agent_name": "customer-support", "asset_map": {"prompt/cs-system": "p.md"}},
    )
    assert r.json()["missing"] == ["prompt/cs-system"]


async def test_push_pull_push_roundtrip_is_stable(client):
    """pull 回来的 local_spec 与文件内容再 push，不产生新修订。"""
    await push(client)
    pulled = (
        await client.post(
            f"{API}/workspace/pull", json={"agent_name": "customer-support", "asset_map": ASSET_MAP}
        )
    ).json()
    fs = [f(a["path"], a["asset"], a["content"]) for a in pulled["assets"]]
    out = await push(
        client, spec=pulled["local_spec"], files=fs, expected_revision=pulled["draft"]["revision"]
    )
    assert out["uploaded"] == [] and len(out["skipped"]) == 3
    assert out["draft"]["spec"] == pulled["draft"]["spec"]
