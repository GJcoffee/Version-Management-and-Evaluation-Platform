"""资产来源身份（source_id / 自动改名）与 agent 发布规范（conventions）：服务端对所有入口一致地强制。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from evalp import actions
from evalp.cli.main import app as cli_app
from evalp.client import EvalpClient, EvalpClientError
from evalp.mcp.server import build_server
from evalp.workspace_client import Workspace, WorkspaceError, find_project
from mcp import Client
from typer.testing import CliRunner

from tests.integration.p2a.conftest import API

TOML = """\
[server]
url = "http://test"

[agent]
name = "hiagent"
spec = "agent.yaml"
"""

SKILL_RULE = {"glob": "skills/*.json", "kind": "skill", "name": "$.name", "id": "$.id"}

AGENT = {
    "schema": "evalp/v1",
    "kind": "code",
    "transport": "http",
    "http": {"base_url": "http://127.0.0.1:9"},
    "source": {"capture_git": False},
    "params": {"hiagent_version": "0.5.3"},
    "snapshot": {"dir": "exports/current", "map": [SKILL_RULE]},
}


def text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def write_agent(project: Path, **over) -> None:
    (project / "agent.yaml").write_text(
        yaml.safe_dump({**AGENT, **over}, allow_unicode=True), encoding="utf-8"
    )


def write_skill(project: Path, filename: str, **data) -> None:
    path = project / "exports" / "current" / "skills" / filename
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    (d / "exports" / "current" / "skills").mkdir(parents=True)
    (d / ".evalp.toml").write_text(TOML, encoding="utf-8")
    write_agent(d)
    write_skill(d, "crawl-0.5.3.json", id="sk_1", name="Crawl Engine", steps=3)
    return d


@pytest.fixture
def ws(project: Path) -> Workspace:
    return Workspace(find_project(project))


async def list_assets(client, **params) -> list[dict]:
    return (await client.get(f"{API}/assets", params=params)).json()["items"]


async def set_conventions(client, **conv) -> dict:
    base = {
        "tag_from_param": None,
        "require_notes": False,
        "require_source_id": [],
        "snapshot_readonly": False,
    }
    r = await client.patch(f"{API}/agents/hiagent", json={"conventions": {**base, **conv}})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- 资产来源身份与自动改名


async def test_source_id_keeps_one_asset_across_renames_and_keeps_history(project, ws, evalp_client, client):
    v1 = (await actions.publish(evalp_client, ws, notes="v1", via="cli")).version
    (asset,) = await list_assets(client, kind="skill")
    assert (asset["name"], asset["display_name"], asset["source_id"]) == (
        "crawl-engine",
        "Crawl Engine",
        "sk_1",
    )

    # HiAgent 上把 skill 改名并改了内容：来源 ID 不变 -> 同一个资产，自动改名，追加修订
    write_skill(project, "crawl-0.5.4.json", id="sk_1", name="Crawl Engine Pro", steps=4)
    (project / "exports" / "current" / "skills" / "crawl-0.5.3.json").unlink()
    r = await actions.publish(evalp_client, ws, notes="v2", via="cli")
    v2 = r.version
    assert [u["renamed_from"] for u in r.push.uploaded] == ["skill/crawl-engine"]

    (renamed,) = await list_assets(client, kind="skill")
    assert renamed["id"] == asset["id"] and renamed["latest_rev"] == 2
    assert (renamed["name"], renamed["display_name"]) == ("crawl-engine-pro", "Crawl Engine Pro")
    assert renamed["former_names"] == ["crawl-engine"]
    revs = (await client.get(f"{API}/assets/{asset['id']}/revisions")).json()["items"]
    assert sorted(r["rev"] for r in revs) == [1, 2]  # 修订历史保留

    # 版本 diff：识别为改名（Skill 旧名 → 新名 · r1 → r2），带文本 diff
    d = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})).json()
    (c,) = d["changes"]
    assert c["op"] == "asset_changed" and c["path"] == "snapshot.files[skill/crawl-engine-pro]"
    assert c["asset"] == {"kind": "skill", "name": "crawl-engine-pro", "renamed_from": "crawl-engine"}
    assert c["left"] == {"rev": 1} and c["right"] == {"rev": 2} and '"steps": 4' in c["content_diff"]["text"]

    # 旧版本里固化的旧名字仍能解析（回滚到旧版本不会因为改名而失败）
    draft = (await client.post(f"{API}/versions/{v1['id']}/to-draft", json={"draft_name": "old"})).json()[
        "draft"
    ]
    v = (await client.post(f"{API}/drafts/{draft['id']}/validate")).json()
    assert v["ok"], v
    # 版本资产清单带显示名与来源 ID
    items = (await client.get(f"{API}/versions/{v2['id']}/assets")).json()["items"]
    assert (items[0]["name"], items[0]["display_name"], items[0]["source_id"]) == (
        "crawl-engine-pro",
        "Crawl Engine Pro",
        "sk_1",
    )


async def test_rename_without_content_change_is_still_reported(project, ws, evalp_client, client):
    write_agent(project, snapshot={**AGENT["snapshot"], "map": [{**SKILL_RULE, "name": "{stem_nover}"}]})
    v1 = (await actions.publish(evalp_client, ws, notes="v1", via="cli")).version
    skills = project / "exports" / "current" / "skills"
    (skills / "crawl-0.5.3.json").rename(
        skills / "search-0.5.3.json"
    )  # 内容一字未改，只是文件名（资产名）变了
    r = await actions.publish(evalp_client, ws, notes="v2", via="cli")
    assert r.push.skipped[0]["renamed_from"] == "skill/crawl" and not r.push.uploaded
    (asset,) = await list_assets(client, kind="skill")
    assert asset["name"] == "search" and asset["latest_rev"] == 1 and asset["former_names"] == ["crawl"]
    d = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": r.version["id"]})).json()
    (c,) = d["changes"]
    assert c["asset"]["renamed_from"] == "crawl" and c["left"] == c["right"] == {"rev": 1}
    assert "content_diff" not in c


async def test_push_adopts_existing_asset_with_same_name_and_rejects_conflicts(
    project, ws, evalp_client, client
):
    legacy = await client.post(
        f"{API}/assets",
        json={"kind": "skill", "name": "crawl-engine", "content": "{}", "content_type": "application/json"},
    )
    assert legacy.json()["asset"]["source_id"] is None
    await ws.push(evalp_client)  # 同名且没有来源 ID 的资产被认领，不新建
    (asset,) = await list_assets(client, kind="skill")
    assert asset["id"] == legacy.json()["asset"]["id"] and asset["source_id"] == "sk_1"

    # 另一个文件（来源 ID 不同）想用已有资产的名字：拒绝，不会合并两个资产
    (project / "exports" / "current" / "skills" / "crawl-0.5.3.json").unlink()
    write_skill(project, "other.json", id="sk_2", name="Crawl Engine")
    with pytest.raises(EvalpClientError) as e:
        await ws.push(evalp_client)
    assert e.value.code == "ASSET_NAME_CONFLICT" and "sk_2" in e.value.message and "sk_1" in e.value.message
    # 已有资产改名时撞上别的资产的名字，同样拒绝
    (project / "exports" / "current" / "skills" / "other.json").unlink()
    write_skill(project, "third.json", id="sk_3", name="Third")
    await ws.push(evalp_client)
    write_skill(project, "third.json", id="sk_3", name="Crawl Engine")
    with pytest.raises(EvalpClientError) as e:
        await ws.push(evalp_client)
    assert e.value.code == "ASSET_NAME_CONFLICT" and "改名为 crawl-engine" in e.value.message


async def test_duplicate_source_id_in_one_push_is_rejected(project, ws, evalp_client):
    write_skill(project, "copy.json", id="sk_1", name="Copy")
    with pytest.raises(EvalpClientError) as e:
        await ws.push(evalp_client)
    assert e.value.code == "VALIDATION_ERROR" and "来源 ID 相同" in e.value.message


# ---------------------------------------------------------------- 规范：设置与展示


async def test_conventions_default_set_and_validate(project, ws, evalp_client, client):
    await ws.push(evalp_client)
    a = (await client.get(f"{API}/agents/hiagent")).json()
    assert a["conventions"] == {
        "tag_from_param": None,
        "require_notes": False,
        "require_source_id": [],
        "snapshot_readonly": False,
    }
    out = await set_conventions(
        client, tag_from_param="hiagent_version", require_source_id=["skill", "skill", "mcp"]
    )
    assert out["conventions"]["require_source_id"] == ["skill", "mcp"]  # 去重
    assert (await client.get(f"{API}/agents/hiagent")).json()["conventions"][
        "tag_from_param"
    ] == "hiagent_version"
    bad = await client.patch(f"{API}/agents/hiagent", json={"conventions": {"require_source_id": ["nope"]}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    only_name = await client.patch(
        f"{API}/agents/hiagent", json={"description": "d"}
    )  # 不带 conventions：保持不变
    assert only_name.json()["conventions"]["tag_from_param"] == "hiagent_version"


# ---------------------------------------------------------------- 规范：发布（标签 / 说明）


async def test_tag_from_param_is_enforced_for_every_publish_entry(project, ws, evalp_client, client):
    await ws.push(evalp_client)
    await set_conventions(client, tag_from_param="hiagent_version", require_notes=True)

    # 没有标签、没有说明：一次说清所有违反的规范，details 带结构化信息
    with pytest.raises(EvalpClientError) as e:
        await actions.publish(evalp_client, ws, via="cli")
    err = e.value
    assert err.code == "CONVENTION_VIOLATION" and err.status == 400
    assert "v0.5.3" in err.message and "require_notes" in err.message and "tag_from_param" in err.message
    assert [v["convention"] for v in err.details["violations"]] == ["require_notes", "tag_from_param"]
    assert err.details["violations"][1]["expected_tag"] == "v0.5.3" and err.details["agent"] == "hiagent"

    with pytest.raises(EvalpClientError) as e:  # 标签不等于 v{params.hiagent_version}
        await actions.publish(evalp_client, ws, notes="n", tag="release-1", via="cli")
    assert e.value.code == "CONVENTION_VIOLATION" and "v0.5.3" in e.value.message

    versions = (await client.get(f"{API}/agents/hiagent/versions")).json()["items"]
    assert versions == []  # 被拒绝时没有创建版本

    r = await actions.publish(evalp_client, ws, notes="首发", tag="v0.5.3", via="cli")
    assert r.version["seq"] == 1 and r.version["tags"] == ["v0.5.3"]  # 标签与版本原子创建
    tags = (await client.get(f"{API}/agents/hiagent/tags")).json()
    assert [t["tag"] for t in tags] == ["v0.5.3"] and tags[0]["version_id"] == r.version["id"]

    # 同一版本号再发布（内容变了）：标签已存在，拒绝，且不创建版本
    write_skill(project, "crawl-0.5.3.json", id="sk_1", name="Crawl Engine", steps=4)
    with pytest.raises(EvalpClientError) as e:
        await actions.publish(evalp_client, ws, notes="又改了", tag="v0.5.3", via="cli")
    assert e.value.status == 409 and e.value.code == "CONVENTION_VIOLATION"
    assert "已存在于 agent「hiagent」的 v1" in e.value.message and "params.hiagent_version" in e.value.message
    assert len((await client.get(f"{API}/agents/hiagent/versions")).json()["items"]) == 1

    # 更新版本号后发布成功；内容完全相同的重复发布（existed）不算冲突
    write_agent(project, params={"hiagent_version": "0.5.4"})
    r2 = await actions.publish(evalp_client, ws, notes="升级", tag="v0.5.4", via="cli")
    assert r2.version["seq"] == 2 and r2.version["tags"] == ["v0.5.4"]
    again = await actions.publish(evalp_client, ws, notes="重复", tag="v0.5.4", via="cli")
    assert again.existed and again.version["id"] == r2.version["id"]


async def test_tag_from_param_requires_the_param_and_a_valid_tag(project, ws, evalp_client, client):
    await ws.push(evalp_client)
    await set_conventions(client, tag_from_param="hiagent_version")
    write_agent(project, params={})
    with pytest.raises(EvalpClientError) as e:
        await actions.publish(evalp_client, ws, tag="v0.5.3", via="cli")
    assert "params 里没有 hiagent_version" in e.value.message
    write_agent(project, params={"hiagent_version": 5})  # v5 会和版本号混淆
    with pytest.raises(EvalpClientError) as e:
        await actions.publish(evalp_client, ws, via="cli")
    assert "v5 不是合法的标签" in e.value.message


async def test_publish_with_tag_over_http_is_atomic_and_moves_existing_tag(project, ws, evalp_client, client):
    pushed = await ws.push(evalp_client)  # 没有任何规范：标签自由，已存在的标签移到新版本
    first = await client.post(
        f"{API}/drafts/{pushed.draft_id}/publish",
        json={"expected_revision": pushed.revision, "tag": "prod"},
    )
    assert first.status_code == 200 and first.json()["version"]["tags"] == ["prod"]
    write_skill(project, "crawl-0.5.3.json", id="sk_1", name="Crawl Engine", steps=9)
    pushed = await ws.push(evalp_client)
    second = await client.post(
        f"{API}/drafts/{pushed.draft_id}/publish",
        json={"expected_revision": pushed.revision, "tag": "prod"},
    )
    assert second.json()["version"]["tags"] == ["prod"]
    versions = (await client.get(f"{API}/agents/hiagent/versions")).json()["items"]
    assert {v["seq"]: v["tags"] for v in versions} == {1: [], 2: ["prod"]}
    bad = await client.post(
        f"{API}/drafts/{pushed.draft_id}/publish", json={"expected_revision": pushed.revision, "tag": "v12"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- 规范：快照 map 必须配置 id


async def test_require_source_id_rejects_push_and_publish_without_id(project, ws, evalp_client, client):
    await ws.push(evalp_client)
    await set_conventions(client, require_source_id=["skill", "mcp"])
    no_id = {k: v for k, v in SKILL_RULE.items() if k != "id"}
    mcp_rule = {"glob": "mcp/*.json", "kind": "mcp", "name": "{stem}"}
    write_agent(project, snapshot={**AGENT["snapshot"], "map": [no_id, mcp_rule]})
    with pytest.raises(EvalpClientError) as e:
        await ws.push(evalp_client)
    err = e.value
    assert err.code == "CONVENTION_VIOLATION" and err.status == 400
    assert [v["path"] for v in err.details["violations"]] == ["snapshot.map[0]", "snapshot.map[1]"]
    assert "必须配置 id" in err.message and "snapshot.map[1]" in err.message and "$.id" in err.message

    # 其他入口（网页编辑草稿）绕过 push 把 map 改成没有 id：发布时同样拒绝
    drafts = (await client.get(f"{API}/agents/hiagent/drafts")).json()
    spec = {**drafts[0]["spec"], "snapshot": {"dir": "exports/current", "map": [no_id], "files": {}}}
    saved = await client.put(
        f"{API}/drafts/{drafts[0]['id']}", json={"spec": spec, "expected_revision": drafts[0]["revision"]}
    )
    r = await client.post(
        f"{API}/drafts/{drafts[0]['id']}/publish",
        json={"expected_revision": saved.json()["draft"]["revision"]},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "CONVENTION_VIOLATION"

    # 规则配了 id（不需要 id 的类型不受限）就能推送
    write_agent(project, snapshot={**AGENT["snapshot"], "map": [SKILL_RULE, {**mcp_rule, "id": "$.id"}]})
    assert (await ws.push(evalp_client, force=True)).draft_id


async def test_directory_skill_with_frontmatter_id_satisfies_require_source_id(
    project, ws, evalp_client, client
):
    await ws.push(evalp_client)  # 先建 agent
    await set_conventions(client, require_source_id=["skill"])
    (project / "exports" / "current" / "skills" / "crawl-0.5.3.json").unlink()
    root = project / "exports" / "current" / "skills" / "image-gen-0.0.1"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nskill_id: sk-img-1\nname: 画图\n---\n画一只猫\n", encoding="utf-8")
    rule = {
        "glob": "skills/*", "kind": "skill", "name": "{stem_nover}", "id": "$.skill_id", "display": "$.name",
    }
    write_agent(project, snapshot={**AGENT["snapshot"], "map": [rule]})
    assert (await ws.push(evalp_client, force=True)).draft_id
    (asset,) = [a for a in await list_assets(client, kind="skill") if a["name"] == "image-gen"]
    assert (asset["name"], asset["source_id"], asset["display_name"]) == ("image-gen", "sk-img-1", "画图")

    (root / "SKILL.md").write_text("---\nname: 画图\n---\n画一只猫\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match=r"找不到 'skill_id'.*SKILL\.md 的 YAML frontmatter"):
        await ws.push(evalp_client, force=True)


# ---------------------------------------------------------------- 规范：快照资产只读


async def test_snapshot_readonly_blocks_every_write_except_snapshot_push(project, ws, evalp_client, client):
    await ws.push(evalp_client)
    (asset,) = await list_assets(client, kind="skill")
    aid = asset["id"]
    await client.post(f"{API}/assets", json={"kind": "prompt", "name": "free", "content": "x"})  # 非快照资产
    await set_conventions(client, snapshot_readonly=True)

    async def refused(r: httpx.Response) -> dict:
        assert r.status_code == 409, r.text
        body = r.json()["error"]
        assert body["code"] == "CONVENTION_VIOLATION"
        assert "snapshot_readonly" in body["message"] and "skill/crawl-engine" in body["message"]
        assert body["details"]["agents"] == ["hiagent"]
        return body

    await refused(await client.post(f"{API}/assets/{aid}/revisions", json={"content": '{"a": 1}'}))
    await refused(
        await client.post(
            f"{API}/assets", json={"kind": "skill", "name": "crawl-engine", "content": '{"a": 1}'}
        )
    )
    await refused(await client.patch(f"{API}/assets/{aid}", json={"description": "改"}))
    await refused(await client.delete(f"{API}/assets/{aid}"))
    await refused(await client.delete(f"{API}/assets/{aid}", params={"hard": True}))
    assert (
        await client.post(f"{API}/assets", json={"kind": "prompt", "name": "free", "content": "y"})
    ).status_code == 200

    # MCP upsert_asset：把服务端的中文说明原样返回给助手
    server = build_server(project_dir=project, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as c:
        r = await c.call_tool(
            "upsert_asset", {"kind": "skill", "name": "crawl-engine", "content": "手工改的"}
        )
        assert r.is_error and "snapshot_readonly" in text(r) and "只能通过快照推送" in text(r)

    # 快照推送本身照常更新
    write_skill(project, "crawl-0.5.3.json", id="sk_1", name="Crawl Engine", steps=7)
    r = await ws.push(evalp_client)
    assert r.uploaded and r.uploaded[0]["asset"] == "skill/crawl-engine"

    # 关闭规范后又可以手工写（规范关闭时保持原有行为）
    await set_conventions(client, snapshot_readonly=False)
    assert (
        await client.post(f"{API}/assets/{aid}/revisions", json={"content": '{"b": 2}'})
    ).status_code == 201


async def test_snapshot_readonly_also_blocks_non_snapshot_push_onto_a_snapshot_asset(
    project, ws, evalp_client, client
):
    await ws.push(evalp_client)
    await set_conventions(client, snapshot_readonly=True)
    data = b"{}"
    body = {
        "agent_name": "other",
        "agent_kind": "config",
        "spec": {
            "kind": "config",
            "model": {"provider": "p", "name": "m"},
            "prompts": {"system": "file:s.md"},
        },
        "files": [
            {
                "path": "s.md",
                "asset": "skill/crawl-engine",
                "hash": hashlib.sha256(data).hexdigest(),
                "content": "{}",
            }
        ],
    }
    r = await client.post(f"{API}/workspace/push", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONVENTION_VIOLATION"


# ---------------------------------------------------------------- CLI 与 MCP


def test_cli_conventions_set_merges_into_current_and_show_prints(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text("tok", encoding="utf-8")
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    current = {
        "tag_from_param": None,
        "require_notes": False,
        "require_source_id": [],
        "snapshot_readonly": False,
    }
    with respx.mock(base_url="http://127.0.0.1:7860/api/v1", assert_all_called=False) as m:
        m.get("/agents/hiagent").mock(
            side_effect=lambda req: httpx.Response(200, json={"conventions": current})
        )
        patch = m.patch("/agents/hiagent").mock(
            side_effect=lambda req: httpx.Response(
                200, json={"conventions": json.loads(req.content)["conventions"]}
            )
        )
        r = runner.invoke(
            cli_app,
            ["agent", "conventions", "set", "--agent", "hiagent", "--tag-from-param", "hiagent_version",
             "--require-notes", "--require-source-id", "skill, mcp", "--snapshot-readonly"],
        )  # fmt: skip
        assert r.exit_code == 0, r.output
        assert json.loads(patch.calls[0].request.content) == {
            "conventions": {
                "tag_from_param": "hiagent_version",
                "require_notes": True,
                "require_source_id": ["skill", "mcp"],
                "snapshot_readonly": True,
            }
        }
        assert "hiagent_version" in r.output and "skill, mcp" in r.output

        current.update(tag_from_param="hiagent_version", require_notes=True, require_source_id=["skill"])
        r = runner.invoke(cli_app, ["agent", "conventions", "set", "--agent", "hiagent", "--no-require-notes",
                                     "--tag-from-param", ""])  # fmt: skip
        sent = json.loads(patch.calls[1].request.content)["conventions"]
        assert (
            sent["tag_from_param"] is None
            and sent["require_notes"] is False
            and sent["require_source_id"] == ["skill"]
        )

        r = runner.invoke(cli_app, ["agent", "conventions", "show", "--agent", "hiagent"])
        assert r.exit_code == 0 and "require_notes" in r.output and "标签取自参数" in r.output

        r = runner.invoke(
            cli_app, ["agent", "conventions", "set", "--agent", "hiagent", "--require-source-id", "nope"]
        )
        assert r.exit_code != 0 and "未知的资产类型" in r.output
        r = runner.invoke(cli_app, ["agent", "conventions", "set", "--agent", "hiagent"])
        assert r.exit_code != 0 and "至少指定一项" in r.output
        assert len(patch.calls) == 2


def test_cli_publish_prints_the_server_explanation_and_exits_1(project, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text("tok", encoding="utf-8")
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(project)
    message = (
        "agent「hiagent」的发布规范不允许这次发布：\n规范 require_notes 要求发布说明必填：请写明这次改了什么"
    )
    with respx.mock(base_url="http://test/api/v1", assert_all_called=False) as m:
        m.post("/workspace/push").mock(
            return_value=httpx.Response(
                200,
                json={
                    "agent_id": "agt_1", "agent_created": False, "uploaded": [], "skipped": [],
                    "draft": {"id": "drf_1", "name": "main", "revision": 2, "spec": {}},
                },
            )
        )  # fmt: skip
        m.post("/drafts/drf_1/validate").mock(
            return_value=httpx.Response(200, json={"ok": True, "errors": [], "warnings": []})
        )
        m.post("/drafts/drf_1/publish").mock(
            return_value=httpx.Response(
                400, json={"error": {"code": "CONVENTION_VIOLATION", "message": message, "details": {}}}
            )
        )
        r = CliRunner().invoke(cli_app, ["publish"])
    assert r.exit_code == 1
    assert "规范 require_notes 要求发布说明必填" in r.output and "请写明这次改了什么" in r.output


async def test_mcp_get_conventions_and_publish_rejection(project, client):
    server = build_server(project_dir=project, client_factory=lambda p: EvalpClient(http=client))
    async with Client(server) as c:
        # agent 还没创建
        out = text(await c.call_tool("get_conventions", {}))
        assert "还没有在服务端创建" in out and "`$.`" in out
        await c.call_tool("push_draft", {})
        await set_conventions(
            client,
            tag_from_param="hiagent_version",
            require_notes=True,
            require_source_id=["skill"],
            snapshot_readonly=True,
        )
        out = text(await c.call_tool("get_conventions", {}))
        assert "tag_from_param" in out and "params.hiagent_version" in out and "`v0.5.3`" in out
        assert "require_source_id" in out and "snapshot_readonly" in out and "开启" in out
        assert f"`{SKILL_RULE['id']}`" in out and "违反 require_source_id" not in out  # 规则已配 id

        r = await c.call_tool("publish_version", {})
        assert r.is_error
        msg = text(r)
        assert "agent「hiagent」的发布规范不允许这次发布" in msg and "v0.5.3" in msg and "notes" in msg

        ok = text(await c.call_tool("publish_version", {"notes": "首发", "tag": "v0.5.3"}))
        assert "已发布版本 v1" in ok and "标签 `v0.5.3` 已指向 v1" in ok

        # 缺 id 的规则在 get_conventions 里被标出
        no_id = {k: v for k, v in SKILL_RULE.items() if k != "id"}
        write_agent(project, snapshot={**AGENT["snapshot"], "map": [no_id]})
        out = text(await c.call_tool("get_conventions", {}))
        assert "违反 require_source_id" in out
        r = await c.call_tool("push_draft", {})
        assert r.is_error and "snapshot.map[0]" in text(r)


def test_mcp_instructions_tell_assistants_to_call_get_conventions_first():
    from evalp.mcp.server import INSTRUCTIONS

    assert "发布、推送前先调用 get_conventions" in INSTRUCTIONS
