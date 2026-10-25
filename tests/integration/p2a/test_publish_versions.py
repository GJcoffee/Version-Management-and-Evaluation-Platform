"""发布（详设 4.4）、版本、标签、to-draft、版本 diff。"""

import asyncio
import copy
import os
import subprocess
from pathlib import Path

import pytest
from evalp.services.asset import AssetService
from evalp.services.version import VersionService
from evalp.store.models import Version, VersionAssetRef
from sqlalchemy import select

from .conftest import API, TOOL_YAML, config_spec


@pytest.fixture
async def world(api):
    """一个 provider + 三类资产 + 一个引用它们的 agent。"""
    await api.provider()
    prompt = await api.asset("prompt", "sys", "你是客服。\n请礼貌。\n")
    tool = await api.asset("tool", "q", TOOL_YAML)
    kn = await api.asset("knowledge", "faq", "# FAQ\n退货 7 天")
    spec = config_spec(
        prompts={"system": "asset://prompt/sys@latest", "variables": {"company": "A"}},
        tools=[
            "asset://tool/q@latest",
            {
                "builtin": "knowledge_search",
                "with": {"sources": ["asset://knowledge/faq@latest"], "top_k": 3},
            },
        ],
        knowledge={"inject": ["asset://knowledge/faq@latest"]},
    )
    out = await api.agent("bot", spec=spec)
    return {
        "prompt": prompt,
        "tool": tool,
        "kn": kn,
        "agent": out["agent"],
        "draft": out["draft"],
        "spec": spec,
    }


async def versions_of(client, agent="bot"):
    return (await client.get(f"{API}/agents/{agent}/versions")).json()["items"]


# ---------------------------------------------------------------- 发布


async def test_publish_creates_version_with_resolved_spec(api, client, ctx, world):
    pub = await api.publish(world["draft"], notes="首发", via="cli")
    v = pub["version"]
    assert pub["existed"] is False
    assert v["id"].startswith("ver_") and v["seq"] == 1 and len(v["spec_hash"]) == 64
    assert v["notes"] == "首发" and v["created_via"] == "cli" and v["parent_version_id"] is None
    assert v["from_draft_id"] == world["draft"]["id"] and v["source"] == {} and v["tags"] == []
    spec = v["spec"]
    # 引用被固化为 {"$asset": {kind,name,rev,hash}}，没有 @latest
    sysp = spec["prompts"]["system"]["$asset"]
    assert sysp == {
        "kind": "prompt",
        "name": "sys",
        "rev": 1,
        "hash": world["prompt"]["revision"]["blob_hash"],
    }
    assert spec["tools"][0]["$asset"]["hash"] == world["tool"]["revision"]["blob_hash"]
    assert spec["tools"][1]["with"]["sources"][0]["$asset"]["name"] == "faq"
    assert spec["knowledge"]["inject"][0]["$asset"]["rev"] == 1
    assert "@latest" not in str(spec)
    # 补齐了默认值
    assert spec["runtime"]["max_steps"] == 12 and spec["model"]["params"]["max_tokens"] == 4096
    # asset refs
    refs = {(a["spec_path"], a["name"], a["rev"]) for a in v["assets"]}
    assert refs == {
        ("prompts.system", "sys", 1),
        ("tools[0]", "q", 1),
        ("tools[1].with.sources[0]", "faq", 1),
        ("knowledge.inject[0]", "faq", 1),
    }
    async with ctx.db.read() as s:
        rows = (
            (await s.execute(select(VersionAssetRef).where(VersionAssetRef.version_id == v["id"])))
            .scalars()
            .all()
        )
    assert len(rows) == 4
    # 草稿的 base_version_id 指向新版本，revision 不变
    d = await api.draft(world["draft"]["id"])
    assert d["base_version_id"] == v["id"] and d["revision"] == world["draft"]["revision"]
    assert (await client.get(f"{API}/agents/bot")).json()["latest_version"]["id"] == v["id"]


async def test_publish_same_content_twice_gives_one_version(api, client, world):
    first = await api.publish(world["draft"])
    second = await api.publish(world["draft"])
    assert second["existed"] is True and second["version"]["id"] == first["version"]["id"]
    assert len(await versions_of(client)) == 1


async def test_publish_ignores_display_fields_and_explicit_defaults(api, client, world):
    first = await api.publish(world["draft"])
    spec = copy.deepcopy(world["spec"])
    spec.update({"name": "展示名", "description": "说明", "notes": "备注"})
    spec["runtime"] = {
        "max_steps": 12,
        "turn_timeout_s": 180,
        "parallel_tool_calls": True,
        "tool_timeout_s": 30,
    }
    spec["model"]["params"] = {"temperature": 0.3, "top_p": 1, "max_tokens": 4096}
    spec["schema"] = "evalp/v1"
    saved = (await api.save(world["draft"], spec))["draft"]
    again = await api.publish(saved, notes="另一个备注")
    assert again["existed"] is True and again["version"]["id"] == first["version"]["id"]
    assert again["version"]["notes"] is None  # 版本不可变，备注不会被改写
    # 草稿被重新指向已有版本
    assert (await api.draft(world["draft"]["id"]))["base_version_id"] == first["version"]["id"]


async def test_publish_changed_spec_makes_new_version_with_parent(api, client, world):
    v1 = (await api.publish(world["draft"]))["version"]
    spec = copy.deepcopy(world["spec"])
    spec["model"]["params"] = {"temperature": 0.7}
    d2 = (await api.save(world["draft"], spec))["draft"]
    v2 = (await api.publish(d2))["version"]
    assert v2["seq"] == 2 and v2["parent_version_id"] == v1["id"] and v2["spec_hash"] != v1["spec_hash"]
    items = await versions_of(client)
    assert [i["seq"] for i in items] == [2, 1]
    assert "spec" not in items[0]  # 列表是摘要


async def test_new_asset_revision_makes_new_version_but_old_unchanged(api, client, ctx, world):
    v1 = (await api.publish(world["draft"]))["version"]
    await api.revision(world["prompt"]["asset"]["id"], "你是新的客服。\n")
    # 不重新发布：旧版本内容与哈希都不变
    again = await ctx.svc(VersionService).get_resolved(v1["id"])
    assert again.spec_hash == v1["spec_hash"] and again.spec == v1["spec"]
    pin = again.spec["prompts"]["system"]["$asset"]
    assert pin["rev"] == 1
    assert (
        await ctx.svc(AssetService).read_text(again.spec["prompts"]["system"])
    ) == "你是客服。\n请礼貌。\n"
    # 重新发布同一份草稿：@latest 现在是 rev 2 -> 新版本
    pub = await api.publish(world["draft"])
    assert pub["existed"] is False and pub["version"]["seq"] == 2
    assert pub["version"]["spec"]["prompts"]["system"]["$asset"]["rev"] == 2
    assert pub["version"]["spec_hash"] != v1["spec_hash"]


async def test_pinned_rev_does_not_follow_latest(api, world):
    spec = copy.deepcopy(world["spec"])
    spec["prompts"]["system"] = "asset://prompt/sys@1"
    d = (await api.save(world["draft"], spec))["draft"]
    v1 = (await api.publish(d))["version"]
    await api.revision(world["prompt"]["asset"]["id"], "新内容")
    v2 = await api.publish(d)
    assert v2["existed"] is True and v2["version"]["id"] == v1["id"]


async def test_publish_requires_current_revision(api, client, world):
    d = world["draft"]
    await api.save(d, {**d["spec"], "notes": "x"})  # revision -> 2
    r = await client.post(f"{API}/drafts/{d['id']}/publish", json={"expected_revision": 1})
    assert r.status_code == 409
    assert (
        r.json()["error"]["code"] == "DRAFT_CONFLICT"
        and r.json()["error"]["details"]["current_revision"] == 2
    )
    assert await versions_of(client) == []


async def test_publish_invalid_spec_is_spec_invalid_and_writes_nothing(api, client, ctx, world):
    spec = copy.deepcopy(world["spec"])
    spec["tools"] = ["asset://tool/missing@latest"]
    spec["model"] = {"provider": "nope", "name": "m"}
    d = (await api.save(world["draft"], spec))["draft"]
    r = await client.post(f"{API}/drafts/{d['id']}/publish", json={"expected_revision": d["revision"]})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "SPEC_INVALID"
    assert {e["path"] for e in err["details"]["errors"]} == {"model.provider", "tools[0]"}
    assert await versions_of(client) == []
    async with ctx.db.read() as s:
        assert (await s.execute(select(VersionAssetRef))).scalars().all() == []


async def test_publish_structure_error_is_validation_error(api, client, world):
    d = (await api.save(world["draft"], {**world["spec"], "runtime": {"max_steps": 0}}))["draft"]
    r = await client.post(f"{API}/drafts/{d['id']}/publish", json={"expected_revision": d["revision"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"]["errors"][0]["path"] == "runtime.max_steps"


async def test_publish_asset_deleted_after_save_fails(api, client, world):
    await client.delete(f"{API}/assets/{world['kn']['asset']['id']}")
    r = await client.post(f"{API}/drafts/{world['draft']['id']}/publish", json={"expected_revision": 1})
    assert r.status_code == 400 and "已被删除" in str(r.json()["error"]["details"]["errors"])


async def test_publish_is_atomic_when_a_step_fails(ctx, world, monkeypatch):
    """发布过程中任何一步抛异常，都不会留下半个版本。"""
    from evalp.services import draft as draft_mod

    real = draft_mod.canonicalize

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(draft_mod, "canonicalize", boom)
    with pytest.raises(RuntimeError):
        await ctx.svc(draft_mod.DraftService).publish(world["draft"]["id"], 1)
    monkeypatch.setattr(draft_mod, "canonicalize", real)
    async with ctx.db.read() as s:
        assert (await s.execute(select(Version))).scalars().all() == []


async def test_concurrent_identical_publishes_make_one_version(api, client, world):
    d = world["draft"]
    rs = await asyncio.gather(
        *[
            client.post(f"{API}/drafts/{d['id']}/publish", json={"expected_revision": d["revision"]})
            for _ in range(6)
        ]
    )
    assert all(r.status_code == 200 for r in rs)
    assert sorted(r.json()["existed"] for r in rs) == [False] + [True] * 5
    assert len({r.json()["version"]["id"] for r in rs}) == 1
    assert len(await versions_of(client)) == 1


async def test_same_spec_in_different_agents_gives_separate_versions(api, client, world):
    other = await api.agent("bot2", spec=world["spec"])
    a = await api.publish(world["draft"])
    b = await api.publish(other["draft"])
    assert a["version"]["spec_hash"] == b["version"]["spec_hash"] and a["version"]["id"] != b["version"]["id"]
    assert b["version"]["seq"] == 1 and b["existed"] is False


async def test_publish_via_validation(api, client, world):
    r = await client.post(
        f"{API}/drafts/{world['draft']['id']}/publish", json={"expected_revision": 1, "via": "robot"}
    )
    assert r.status_code == 400
    r = await client.post(f"{API}/drafts/drf_nope/publish", json={"expected_revision": 1})
    assert r.status_code == 404


# ---------------------------------------------------------------- 版本查询 / 标签


async def test_get_version_by_id_or_reference(api, client, world):
    v = (await api.publish(world["draft"]))["version"]
    for ref in (v["id"], "bot:v1", "bot:latest"):
        got = (await client.get(f"{API}/versions/{ref}")).json()
        assert got["id"] == v["id"] and got["spec"] == v["spec"] and got["assets"]
    for bad in ("bot:v9", "bot:nope", "nobody:v1", "ver_nope"):
        assert (await client.get(f"{API}/versions/{bad}")).status_code == 404
    assert (await client.get(f"{API}/versions/justaname")).status_code == 400


async def test_version_list_pagination_and_tags(api, client, world):
    d = world["draft"]
    ids = []
    for i in range(3):
        d = (await api.save(d, {**world["spec"], "runtime": {"max_steps": 5 + i}}))["draft"]
        ids.append((await api.publish(d))["version"]["id"])
    await client.post(f"{API}/versions/{ids[0]}/tags", json={"tag": "prod"})
    await client.post(f"{API}/versions/{ids[0]}/tags", json={"tag": "stable"})
    page = (await client.get(f"{API}/agents/bot/versions", params={"limit": 2})).json()
    assert [v["seq"] for v in page["items"]] == [3, 2] and page["next_cursor"]
    rest = (
        await client.get(f"{API}/agents/bot/versions", params={"limit": 2, "cursor": page["next_cursor"]})
    ).json()
    assert [v["seq"] for v in rest["items"]] == [1]
    assert rest["items"][0]["tags"] == ["prod", "stable"] and page["items"][0]["tags"] == []
    detail = (await client.get(f"{API}/versions/{ids[0]}")).json()
    assert detail["tags"] == ["prod", "stable"]
    tags = (await client.get(f"{API}/agents/bot/tags")).json()
    assert [(t["tag"], t["version_id"]) for t in tags] == [("prod", ids[0]), ("stable", ids[0])]


async def test_tag_upsert_moves_tag_between_versions(api, client, world):
    v1 = (await api.publish(world["draft"]))["version"]
    d = (await api.save(world["draft"], {**world["spec"], "runtime": {"max_steps": 3}}))["draft"]
    v2 = (await api.publish(d))["version"]
    r = await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": "prod"})
    assert r.status_code == 200 and r.json()["version_id"] == v1["id"]
    r = await client.post(f"{API}/versions/{v2['id']}/tags", json={"tag": "prod"})  # 移动
    assert r.json()["version_id"] == v2["id"]
    assert (await client.get(f"{API}/versions/{v1['id']}")).json()["tags"] == []
    assert (await client.get(f"{API}/versions/{v2['id']}")).json()["tags"] == ["prod"]
    assert (await client.get(f"{API}/versions/bot:prod")).json()["id"] == v2["id"]
    # 重复设置同一个标签是幂等的
    assert (await client.post(f"{API}/versions/{v2['id']}/tags", json={"tag": "prod"})).status_code == 200


async def test_tag_delete_and_validation(api, client, world):
    v1 = (await api.publish(world["draft"]))["version"]
    await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": "prod"})
    assert (await client.delete(f"{API}/versions/{v1['id']}/tags/nope")).status_code == 404
    assert (await client.delete(f"{API}/versions/{v1['id']}/tags/prod")).status_code == 204
    assert (await client.get(f"{API}/versions/bot:prod")).status_code == 404
    for bad in ("v12", "latest", "has space", "", "a" * 65, "x:y"):
        r = await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": bad})
        assert r.status_code == 400, bad
    # 标签只在 agent 内唯一
    other = await api.agent("bot2", spec=world["spec"])
    ov = (await api.publish(other["draft"]))["version"]
    await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": "prod"})
    r = await client.post(f"{API}/versions/{ov['id']}/tags", json={"tag": "prod"})
    assert r.status_code == 200
    assert (await client.get(f"{API}/versions/bot:prod")).json()["id"] == v1["id"]
    assert (await client.get(f"{API}/versions/bot2:prod")).json()["id"] == ov["id"]


# ---------------------------------------------------------------- to-draft（回滚）


async def test_to_draft_roundtrip_gives_equivalent_spec(api, client, world):
    v = (await api.publish(world["draft"]))["version"]
    # 之后草稿和资产都改了
    await api.revision(world["prompt"]["asset"]["id"], "完全不同的提示词")
    cur = (await api.save(world["draft"], {**world["spec"], "runtime": {"max_steps": 2}}))["draft"]
    r = await client.post(f"{API}/versions/{v['id']}/to-draft", json={})
    assert r.status_code == 200
    d = r.json()["draft"]
    assert d["id"] == cur["id"] and d["name"] == "main" and d["revision"] == cur["revision"] + 1
    assert d["base_version_id"] == v["id"]
    # @rev 保持原样：引用是 asset://kind/name@rev，而不是 @latest
    assert d["spec"]["prompts"]["system"] == "asset://prompt/sys@1"
    assert d["spec"]["tools"][0] == "asset://tool/q@1"
    assert d["spec"]["tools"][1]["with"]["sources"] == ["asset://knowledge/faq@1"]
    # 再发布 -> 与原版本等价 -> 同一个版本
    pub = await api.publish(d)
    assert pub["existed"] is True and pub["version"]["id"] == v["id"]


async def test_to_draft_creates_named_draft(api, client, world):
    v = (await api.publish(world["draft"]))["version"]
    r = await client.post(f"{API}/versions/bot:v1/to-draft", json={"draft_name": "rollback"})
    d = r.json()["draft"]
    assert d["name"] == "rollback" and d["revision"] == 1 and d["base_version_id"] == v["id"]
    assert d["id"] != world["draft"]["id"]
    # 再次执行 -> 覆盖同一个草稿
    d2 = (await client.post(f"{API}/versions/{v['id']}/to-draft", json={"draft_name": "rollback"})).json()[
        "draft"
    ]
    assert d2["id"] == d["id"] and d2["revision"] == 2
    assert (
        await client.post(f"{API}/versions/{v['id']}/to-draft", json={"draft_name": "bad name"})
    ).status_code == 400
    assert (await client.post(f"{API}/versions/ver_nope/to-draft", json={})).status_code == 404


async def test_create_draft_from_version(api, client, world):
    v = (await api.publish(world["draft"]))["version"]
    r = await client.post(f"{API}/agents/bot/drafts", json={"name": "from-v1", "from_version_id": v["id"]})
    d = r.json()["draft"]
    assert d["base_version_id"] == v["id"] and d["spec"]["prompts"]["system"] == "asset://prompt/sys@1"
    other = await api.agent("other", spec=world["spec"])
    ov = (await api.publish(other["draft"]))["version"]
    r = await client.post(f"{API}/agents/bot/drafts", json={"name": "x", "from_version_id": ov["id"]})
    assert r.status_code == 404


# ---------------------------------------------------------------- diff


async def test_version_diff_matches_design_doc_shape(api, client, world):
    v1 = (await api.publish(world["draft"]))["version"]
    await api.revision(world["prompt"]["asset"]["id"], "你是客服。\n请礼貌。\n请简洁。\n")
    await api.revision(world["tool"]["asset"]["id"], TOOL_YAML.replace("已发货", "已签收"))
    spec = copy.deepcopy(world["spec"])
    spec["model"]["params"] = {"temperature": 0.7}
    d = (await api.save(world["draft"], spec))["draft"]
    v2 = (await api.publish(d))["version"]
    r = await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})
    assert r.status_code == 200
    diff = r.json()
    assert diff["left"] == {"id": v1["id"], "seq": 1, "spec_hash": v1["spec_hash"]}
    assert diff["right"] == {"id": v2["id"], "seq": 2, "spec_hash": v2["spec_hash"]}
    ch = {c["path"]: c for c in diff["changes"]}
    assert ch["model.params.temperature"] == {
        "path": "model.params.temperature",
        "op": "replace",
        "left": 0.3,
        "right": 0.7,
    }
    t = ch["tools[q]"]
    assert t["op"] == "asset_changed" and t["left"] == {"rev": 1} and t["right"] == {"rev": 2}
    assert t["content_diff"]["format"] == "unified"
    assert "已签收" in t["content_diff"]["text"] and "已发货" in t["content_diff"]["text"]
    p = ch["prompts.system"]
    assert p["op"] == "asset_changed" and "+请简洁。" in p["content_diff"]["text"]
    assert diff["summary"] == {"add": 0, "remove": 0, "replace": 1, "asset_changed": 2}


async def test_version_diff_reference_forms(api, client, world):
    v1 = (await api.publish(world["draft"]))["version"]
    d = (await api.save(world["draft"], {**world["spec"], "runtime": {"max_steps": 3}}))["draft"]
    v2 = (await api.publish(d))["version"]
    await client.post(f"{API}/versions/{v1['id']}/tags", json={"tag": "prod"})
    forms = [
        (v1["id"], v2["id"]),
        ("bot:v1", "bot:v2"),
        ("bot:prod", "bot:latest"),
        (v1["id"], "bot:v2"),
    ]
    for left, right in forms:
        diff = (await client.get(f"{API}/versions/diff", params={"left": left, "right": right})).json()
        assert (diff["left"]["seq"], diff["right"]["seq"]) == (1, 2)
        assert [c["path"] for c in diff["changes"]] == ["runtime.max_steps"]
        assert diff["changes"][0]["left"] == 12 and diff["changes"][0]["right"] == 3
    same = (await client.get(f"{API}/versions/diff", params={"left": "bot:v1", "right": "bot:v1"})).json()
    assert same["changes"] == []
    assert (
        await client.get(f"{API}/versions/diff", params={"left": "bot:v1", "right": "bot:v7"})
    ).status_code == 404
    assert (await client.get(f"{API}/versions/diff", params={"left": "bot:v1"})).status_code == 400


async def test_version_diff_tools_matched_by_name(api, client, world):
    await api.asset("tool", "extra", TOOL_YAML.replace("query_order", "extra_tool"))
    v1 = (await api.publish(world["draft"]))["version"]
    spec = copy.deepcopy(world["spec"])
    spec["tools"] = ["asset://tool/extra@latest", *spec["tools"]]  # 在最前面插入
    d = (await api.save(world["draft"], spec))["draft"]
    v2 = (await api.publish(d))["version"]
    diff = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})).json()
    assert [(c["path"], c["op"]) for c in diff["changes"]] == [("tools[extra]", "add")]


# ---------------------------------------------------------------- 供其他模块使用


async def test_get_resolved_for_runtime(api, ctx, world):
    v = (await api.publish(world["draft"]))["version"]
    rv = await ctx.svc(VersionService).get_resolved(v["id"])
    assert (rv.id, rv.seq, rv.agent_name, rv.agent_kind, rv.spec_hash) == (
        v["id"],
        1,
        "bot",
        "config",
        v["spec_hash"],
    )
    model = rv.spec_model()
    assert model.kind == "config" and model.model.provider == "deepseek"
    pin = rv.spec["tools"][0]
    assets = ctx.svc(AssetService)
    assert "query_order" in await assets.read_text(pin)  # 传 {"$asset": …}
    assert "query_order" in await assets.read_text(pin["$asset"])  # 传内层字典
    assert (await assets.read_bytes(rv.spec["prompts"]["system"])).decode() == "你是客服。\n请礼貌。\n"
    # 读取版本引用的资产修订
    resolved = await assets.resolve_ref("asset://prompt/sys@latest")
    assert resolved.hash == rv.spec["prompts"]["system"]["$asset"]["hash"]
    from evalp.errors import NotFound

    with pytest.raises(NotFound):
        await ctx.svc(VersionService).get_resolved("ver_nope")
    assert await ctx.svc(VersionService).resolve_ref("bot:v1") == v["id"]


async def test_spec_schema_endpoint(client):
    r = await client.get(f"{API}/spec/schema")
    assert r.status_code == 200
    schema = r.json()
    assert "$defs" in schema and "evalp/v1" in str(schema)
    assert (await client.get(f"{API}/spec/schema")).json() == schema


# ---------------------------------------------------------------- 代码型 agent 与 git


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.io",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.io",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def agent_repo(tmp_path) -> Path:
    r = tmp_path / "agent-code"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "agent.py").write_text("print('v1')\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def code_spec(repo: Path, **source) -> dict:
    return {
        "kind": "code",
        "transport": "http",
        "http": {"base_url": "http://127.0.0.1:8001"},
        "source": {"repo": str(repo), **source},
        "params": {"retrieval_top_k": 8},
    }


async def test_code_agent_publish_without_git(api, client):
    out = await api.agent("coder", kind="code")  # 模板：capture_git=false
    pub = await api.publish(out["draft"])
    v = pub["version"]
    assert v["spec"]["kind"] == "code" and v["spec"]["source"]["git"] is None and v["source"] == {}
    assert v["assets"] == []
    assert (await api.publish(out["draft"]))["existed"] is True


async def test_code_agent_publish_captures_git(api, client, agent_repo):
    out = await api.agent("coder", kind="code", spec=code_spec(agent_repo))
    commit = git(agent_repo, "rev-parse", "HEAD").strip()
    v1 = (await api.publish(out["draft"]))["version"]
    assert v1["source"] == {"git_commit": commit, "git_branch": "main", "dirty": False, "patch_blob": None}
    assert v1["spec"]["source"]["git"] == {"commit": commit, "patch_hash": None}
    # 同一个 commit 再发布 -> 同一个版本
    assert (await api.publish(out["draft"]))["existed"] is True
    # 新的提交 -> 新版本，即使 Spec 本身没变
    (agent_repo / "agent.py").write_text("print('v2')\n", encoding="utf-8")
    git(agent_repo, "commit", "-q", "-am", "v2")
    v2 = await api.publish(out["draft"])
    assert v2["existed"] is False and v2["version"]["seq"] == 2
    assert v2["version"]["source"]["git_commit"] == git(agent_repo, "rev-parse", "HEAD").strip()
    assert v2["version"]["spec_hash"] != v1["spec_hash"]


async def test_code_agent_dirty_rejected_unless_allowed(api, client, ctx, agent_repo):
    out = await api.agent("coder", kind="code", spec=code_spec(agent_repo))
    (agent_repo / "agent.py").write_text("print('dirty')\n", encoding="utf-8")
    r = await client.post(f"{API}/drafts/{out['draft']['id']}/publish", json={"expected_revision": 1})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "GIT_DIRTY" and "agent.py" in str(err["details"]["changed_files"])
    assert await versions_of(client, "coder") == []

    d = (await api.save(out["draft"], code_spec(agent_repo, allow_dirty=True)))["draft"]
    v1 = (await api.publish(d))["version"]
    h = v1["source"]["patch_blob"]
    assert v1["source"]["dirty"] is True and len(h) == 64
    assert v1["spec"]["source"]["git"]["patch_hash"] == h
    assert b"+print('dirty')" in await ctx.blobs.get_bytes(h)
    assert await ctx.blobs.refcount(h) == 1
    # 补丁相同 -> 同一个版本；补丁不同 -> 新版本（补丁哈希参与 spec_hash）
    assert (await api.publish(d))["existed"] is True
    (agent_repo / "agent.py").write_text("print('dirty 2')\n", encoding="utf-8")
    v2 = await api.publish(d)
    assert v2["existed"] is False and v2["version"]["source"]["patch_blob"] != h


async def test_code_agent_not_a_repo_is_spec_invalid(api, client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    out = await api.agent("coder", kind="code", spec=code_spec(plain))
    r = await client.post(f"{API}/drafts/{out['draft']['id']}/publish", json={"expected_revision": 1})
    assert r.status_code == 400 and r.json()["error"]["code"] == "SPEC_INVALID"
    assert "capture_git" in r.json()["error"]["message"]


async def test_code_agent_to_draft_roundtrip(api, client, agent_repo):
    out = await api.agent("coder", kind="code", spec=code_spec(agent_repo))
    v = (await api.publish(out["draft"]))["version"]
    d = (await client.post(f"{API}/versions/{v['id']}/to-draft", json={})).json()["draft"]
    again = await api.publish(d)
    assert again["existed"] is True and again["version"]["id"] == v["id"]


async def test_subprocess_agent_publish(api):
    spec = {
        "kind": "code",
        "transport": "subprocess",
        "subprocess": {
            "command": ["python", "-m", "my_agent.entry"],
            "cwd": "/work",
            "env": {"LOG_LEVEL": "info"},
        },
        "source": {"capture_git": False},
    }
    out = await api.agent("sub", kind="code", spec=spec)
    v = (await api.publish(out["draft"]))["version"]
    assert (
        v["spec"]["subprocess"]["command"] == ["python", "-m", "my_agent.entry"] and v["spec"]["http"] is None
    )
