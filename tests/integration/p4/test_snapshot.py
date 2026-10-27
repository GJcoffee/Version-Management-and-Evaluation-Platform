# Synthetic fixtures authored by hand; no recorded agent data.
"""代码型 agent 的版本快照（snapshot）：push 上传目录、固化到版本、diff 逐文件显示文本差异。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from evalp import actions
from evalp.workspace_client import Workspace, WorkspaceError, find_project, snapshot_asset

from tests.integration.p2a.conftest import API

TOML = """\
[server]
url = "http://test"

[agent]
name = "hiagent"
spec = "agent.yaml"
"""

AGENT_YAML = """\
schema: evalp/v1
kind: code
transport: http
http:
  base_url: http://127.0.0.1:9
source:
  capture_git: false
snapshot:
  dir: exports/current
"""

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0xFF, 0xFE, 0x00])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    snap = d / "exports" / "current"
    (snap / "platform").mkdir(parents=True)
    (snap / "skills").mkdir()
    (d / ".evalp.toml").write_text(TOML, encoding="utf-8")
    (d / "agent.yaml").write_text(AGENT_YAML, encoding="utf-8")
    (snap / "platform" / "harness-agent.json").write_text(
        json.dumps({"SystemPrompt": "你是助手。\n只回答问题。"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (snap / "skills" / "画图 skill.json").write_text('{"name": "draw"}', encoding="utf-8")
    (snap / "logo.png").write_bytes(PNG)
    return d


def snap_ws(project: Path) -> Workspace:
    return Workspace(find_project(project))


def test_snapshot_asset_name_is_valid_and_unique():
    a, b = snapshot_asset("hiagent", "a/b.json"), snapshot_asset("hiagent", "a-b.json")
    assert a != b and a.startswith("file/snap-hiagent-a-b-json-")
    long = snapshot_asset("x" * 80, "很长/" + "y" * 100 + ".json")
    assert len(long.split("/", 1)[1]) <= 64
    assert snapshot_asset("hiagent", "画图.json").startswith("file/snap-hiagent-json-")


async def test_push_uploads_all_files_and_fills_snapshot(project, evalp_client, client):
    ws = snap_ws(project)
    r = await ws.push(evalp_client)
    assert r.agent_created and len(r.uploaded) == 3
    assert r.validation and r.validation["ok"], r.validation
    draft = (await client.get(f"{API}/drafts/{r.draft_id}")).json()
    files = draft["spec"]["snapshot"]["files"]
    assert set(files) == {"platform/harness-agent.json", "skills/画图 skill.json", "logo.png"}
    assert all(v.startswith("asset://file/snap-hiagent-") and v.endswith("@latest") for v in files.values())
    # 第二次 push 没有变化
    again = await ws.push(evalp_client)
    assert again.unchanged


async def test_publish_pins_files_and_diff_shows_text_changes(project, evalp_client, client):
    ws = snap_ws(project)
    v1 = (await actions.publish(evalp_client, ws, notes="v1", tag=None, via="cli")).version
    pins = v1["spec"]["snapshot"]["files"]
    assert pins["logo.png"]["$asset"]["kind"] == "file"
    assert v1["spec"]["snapshot"]["dir"] == "exports/current"

    snap = project / "exports" / "current"
    (snap / "platform" / "harness-agent.json").write_text(
        json.dumps({"SystemPrompt": "你是助手。\n多回答一点。"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (snap / "logo.png").write_bytes(PNG + b"\x01")
    (snap / "skills" / "new.json").write_text("{}", encoding="utf-8")
    (snap / "skills" / "画图 skill.json").unlink()
    v2 = (await actions.publish(evalp_client, ws, notes="v2", tag=None, via="cli")).version
    assert v2["seq"] == v1["seq"] + 1

    d = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})).json()
    by_path = {c["path"]: c for c in d["changes"]}
    prompt = by_path[f"snapshot.files[{snapshot_asset('hiagent', 'platform/harness-agent.json')}]"]
    assert prompt["op"] == "asset_changed"
    assert '-  "SystemPrompt": "你是助手。\\n只回答问题。"' in prompt["content_diff"]["text"]
    assert "多回答一点" in prompt["content_diff"]["text"]
    logo = by_path[f"snapshot.files[{snapshot_asset('hiagent', 'logo.png')}]"]
    assert "content_diff" not in logo and logo["op"] == "asset_changed"  # 二进制文件只标记变化
    assert by_path[f"snapshot.files[{snapshot_asset('hiagent', 'skills/new.json')}]"]["op"] == "add"
    assert by_path[f"snapshot.files[{snapshot_asset('hiagent', 'skills/画图 skill.json')}]"]["op"] == "remove"


async def test_publish_without_changes_returns_existing_version(project, evalp_client):
    ws = snap_ws(project)
    first = await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")
    second = await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")
    assert second.existed and second.version["id"] == first.version["id"]


async def test_pull_does_not_write_snapshot_files_into_agent_yaml(project, evalp_client):
    ws = snap_ws(project)
    await ws.push(evalp_client)
    await ws.pull(evalp_client)
    assert yaml.safe_load((project / "agent.yaml").read_text(encoding="utf-8"))["snapshot"] == {
        "dir": "exports/current"
    }


async def test_snapshot_shorthand_string_keeps_form_across_push_and_pull(project, evalp_client):
    (project / "agent.yaml").write_text(
        AGENT_YAML.replace("snapshot:\n  dir: exports/current", "snapshot: exports/current"), encoding="utf-8"
    )
    ws = snap_ws(project)
    r = await ws.push(evalp_client)
    assert len(r.uploaded) == 3
    pulled = await ws.pull(evalp_client)  # 本地没有未推送的修改，pull 不会报冲突
    assert "agent.yaml" in pulled.unchanged
    assert (
        yaml.safe_load((project / "agent.yaml").read_text(encoding="utf-8"))["snapshot"] == "exports/current"
    )


async def test_missing_snapshot_dir_is_reported(project, evalp_client):
    (project / "agent.yaml").write_text(
        AGENT_YAML.replace("exports/current", "exports/nope"), encoding="utf-8"
    )
    with pytest.raises(WorkspaceError, match="snapshot.dir"):
        await snap_ws(project).push(evalp_client)


# ---------------------------------------------------------------- 按规则入库（snapshot.map）

MAPPED_YAML = (
    AGENT_YAML
    + """\
  map:
    - {glob: "system-prompt.md", kind: prompt, name: "{agent}-system"}
    - {glob: "skills/*.json", kind: skill, name: "{stem_nover}"}
    - {glob: "resources/database*.json", kind: database, name: "{agent}-{stem}"}
    - {glob: "resources/tables/*.json", kind: database, name: "{agent}-table-{stem}"}
    - {glob: "resources/knowledge-*.json", kind: kb_config, name: "{agent}-{stem}"}
    - {glob: "platform/*.json", kind: app_config, name: "{agent}-{stem}"}
"""
)

EXPORT = {
    "system-prompt.md": "你是爬虫助手。\n",
    "skills/crawl-engine-0.5.3.json": '{"name": "crawl-engine", "steps": 3}',
    "resources/database.json": '{"Name": "db"}',
    "resources/database-tables.json": '{"Tables": ["t1"]}',
    "resources/tables/t1.json": '{"Columns": []}',
    "resources/knowledge-base.json": '{"Name": "kb", "TopK": 5}',
    "platform/app-config.json": '{"Model": "doubao"}',
    "logo.png": PNG,
}


def write_export(project: Path, files: dict) -> None:
    snap = project / "exports" / "current"
    for rel, data in files.items():
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            (snap / rel).write_bytes(data)
        else:
            (snap / rel).write_text(data, encoding="utf-8")


@pytest.fixture
def mapped_project(tmp_path: Path) -> Path:
    d = tmp_path / "mapped"
    (d / "exports" / "current").mkdir(parents=True)
    (d / ".evalp.toml").write_text(TOML, encoding="utf-8")
    (d / "agent.yaml").write_text(MAPPED_YAML, encoding="utf-8")
    write_export(d, EXPORT)
    return d


async def assets_of(client, kind: str) -> dict[str, dict]:
    items = (await client.get(f"{API}/assets", params={"kind": kind})).json()["items"]
    return {a["name"]: a for a in items}


async def test_map_rules_store_snapshot_files_as_typed_assets(mapped_project, evalp_client, client):
    ws = snap_ws(mapped_project)
    r = await ws.push(evalp_client)
    assert len(r.uploaded) == len(EXPORT)
    expected = {
        "prompt": {"hiagent-system"},
        "skill": {"crawl-engine"},
        "database": {"hiagent-database", "hiagent-database-tables", "hiagent-table-t1"},
        "kb_config": {"hiagent-knowledge-base"},
        "app_config": {"hiagent-app-config"},
    }
    for kind, names in expected.items():
        assert set(await assets_of(client, kind)) == names, kind
    skill = (await assets_of(client, "skill"))["crawl-engine"]
    assert skill["latest"]["content_type"] == "application/json"
    # 所有快照入库的资产都标记来源（只读）：包括没命中规则、存为 file 的文件
    assert skill["latest"]["meta"]["origin"] == "snapshot"
    assert (await assets_of(client, "prompt"))["hiagent-system"]["latest"]["meta"]["origin"] == "snapshot"
    (logo,) = (await assets_of(client, "file")).values()
    assert logo["name"] == snapshot_asset("hiagent", "logo.png").split("/")[1]
    assert logo["latest"]["meta"]["origin"] == "snapshot"
    draft = (await client.get(f"{API}/drafts/{r.draft_id}")).json()
    assert draft["spec"]["snapshot"]["files"]["skills/crawl-engine-0.5.3.json"] == (
        "asset://skill/crawl-engine@latest"
    )
    assert len(draft["spec"]["snapshot"]["map"]) == 6
    again = await ws.push(evalp_client)
    assert again.unchanged


async def test_first_matching_rule_wins(mapped_project, evalp_client, client):
    (mapped_project / "agent.yaml").write_text(
        AGENT_YAML
        + """\
  map:
    - {glob: "resources/database*.json", kind: database, name: "{agent}-{stem}"}
    - {glob: "resources/*.json", kind: kb_config, name: "{agent}-other-{stem}"}
""",
        encoding="utf-8",
    )
    await snap_ws(mapped_project).push(evalp_client)
    assert set(await assets_of(client, "database")) == {"hiagent-database", "hiagent-database-tables"}
    assert set(await assets_of(client, "kb_config")) == {"hiagent-other-knowledge-base"}


async def test_skill_upgrade_is_new_revision_of_same_asset_and_diff_shows_type_and_name(
    mapped_project, evalp_client, client
):
    ws = snap_ws(mapped_project)
    v1 = (await actions.publish(evalp_client, ws, notes="v1", tag=None, via="cli")).version
    old = mapped_project / "exports" / "current" / "skills" / "crawl-engine-0.5.3.json"
    old.rename(old.with_name("crawl-engine-0.5.4.json"))
    write_export(mapped_project, {"skills/crawl-engine-0.5.4.json": '{"name": "crawl-engine", "steps": 4}'})
    v2 = (await actions.publish(evalp_client, ws, notes="v2", tag=None, via="cli")).version
    assert v2["seq"] == 2

    skills = await assets_of(client, "skill")
    assert list(skills) == ["crawl-engine"] and skills["crawl-engine"]["latest_rev"] == 2

    d = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})).json()
    assert [(c["path"], c["op"]) for c in d["changes"]] == [
        ("snapshot.files[skill/crawl-engine]", "asset_changed")
    ]
    change = d["changes"][0]
    assert change["asset"] == {"kind": "skill", "name": "crawl-engine"}
    assert (change["left"], change["right"]) == ({"rev": 1}, {"rev": 2})
    assert '"steps": 4' in change["content_diff"]["text"]
    # 版本详情里的 spec_path 与 diff 的路径一致，前端据此打开该资产的内容对比
    detail = (await client.get(f"{API}/versions/{v2['id']}")).json()
    assert "snapshot.files[skill/crawl-engine]" in {a["spec_path"] for a in detail["assets"]}


async def test_version_assets_list_origin_and_where_each_asset_last_changed(
    mapped_project, evalp_client, client
):
    ws = snap_ws(mapped_project)
    v1 = (await actions.publish(evalp_client, ws, notes="v1", tag=None, via="cli")).version
    write_export(mapped_project, {"skills/crawl-engine-0.5.3.json": '{"name": "crawl-engine", "steps": 4}'})
    v2 = (await actions.publish(evalp_client, ws, notes="v2", tag=None, via="cli")).version
    write_export(mapped_project, {"platform/app-config.json": '{"Model": "deepseek"}'})
    v3 = (await actions.publish(evalp_client, ws, notes="v3", tag=None, via="cli")).version

    out = (await client.get(f"{API}/versions/{v3['id']}/assets")).json()
    assert out["version"]["seq"] == 3
    items = {(i["kind"], i["name"]): i for i in out["items"]}
    assert len(items) == len(EXPORT)
    skill = items[("skill", "crawl-engine")]
    assert (skill["rev"], skill["changed_in_seq"], skill["previous_rev"]) == (2, 2, 1)
    assert skill["changed_in_version_id"] == v2["id"] and skill["origin"] == "snapshot"
    assert skill["spec_paths"] == ["snapshot.files[skill/crawl-engine]"]
    cfg = items[("app_config", "hiagent-app-config")]
    assert (cfg["rev"], cfg["changed_in_seq"], cfg["previous_rev"]) == (2, 3, 1)
    kb = items[("kb_config", "hiagent-knowledge-base")]
    assert (kb["rev"], kb["changed_in_seq"], kb["previous_rev"]) == (1, 1, None)
    assert [i["kind"] for i in out["items"]] == sorted(i["kind"] for i in out["items"])
    # 选更早的版本：看到当时的修订
    old = (await client.get(f"{API}/versions/{v1['id']}/assets")).json()["items"]
    assert {i["rev"] for i in old} == {1}


async def test_invalid_map_rules_are_reported(mapped_project, evalp_client):
    (mapped_project / "agent.yaml").write_text(
        AGENT_YAML + '  map:\n    - {glob: "*.json", kind: nope, name: x}\n', encoding="utf-8"
    )
    with pytest.raises(WorkspaceError, match=r"snapshot.map\[0\] 不合法: kind"):
        await snap_ws(mapped_project).push(evalp_client)
    (mapped_project / "agent.yaml").write_text(
        AGENT_YAML + '  map:\n    - {glob: "skills/*.json", kind: skill, name: "same"}\n', encoding="utf-8"
    )
    write_export(mapped_project, {"skills/other.json": "{}"})
    with pytest.raises(WorkspaceError, match="映射到同一个资产 skill/same"):
        await snap_ws(mapped_project).push(evalp_client)


async def test_skill_directory_snapshot_preserves_structure_and_upgrade_identity(
    project, evalp_client, client,
):
    from evalp.utils.skill_package import SkillPackage

    (project / "agent.yaml").write_text(
        AGENT_YAML + '  map:\n    - {glob: "skills/*", kind: skill, name: "{stem_nover}"}\n',
        encoding="utf-8",
    )
    # 旧测试元数据继续走 JSON；新的demo-skill整体作为一个修订。
    (project / "exports/current/skills/画图 skill.json").unlink()
    root = project / "exports/current/skills/demo-skill-0.0.1"
    (root / "references/deep/empty").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: 虚构图片生成\n---\n# 初版", encoding="utf-8")
    (root / "references/deep/data.bin").write_bytes(PNG)
    ws = snap_ws(project)
    before = ws.collect_files(ws.load_spec())
    package_file = next(f for f in before if f.asset.startswith("skill/"))
    assert package_file.path == "exports/current/skills/demo-skill-0.0.1"
    assert package_file.payload(True)["content_type"] == "application/zip"
    assert len(before) == 3
    v1 = (await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")).version
    skills = await assets_of(client, "skill")
    assert len(skills) == 1
    skill = next(iter(skills.values()))
    assert skill["display_name"] == "虚构图片生成"
    assert skill["latest"]["meta"]["origin"] == "snapshot"
    blob = (await client.get(f"{API}/assets/revisions/{skill['latest']['id']}/raw")).content
    package = SkillPackage(blob)
    assert package.read("demo-skill-0.0.1/references/deep/data.bin") == PNG
    assert "demo-skill-0.0.1/references/deep/empty" in package.nodes
    assert (await ws.push(evalp_client)).unchanged
    upgraded = root.with_name("demo-skill-0.0.2")
    root.rename(upgraded)
    (upgraded / "SKILL.md").write_text("---\nname: 虚构图片生成\n---\n# 新版", encoding="utf-8")
    v2 = (await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")).version
    skills = await assets_of(client, "skill")
    assert len(skills) == 1
    current = next(iter(skills.values()))
    assert current["id"] == skill["id"] and current["latest_rev"] == 2
    items = (await client.get(f"{API}/versions/{v2['id']}/assets")).json()["items"]
    used = next(i for i in items if i["kind"] == "skill")
    assert used["display_name"] == "虚构图片生成" and used["rev"] == 2
    assert used["origin"] == "snapshot"
    old_pin = next(
        p["$asset"] for p in v1["spec"]["snapshot"]["files"].values() if p["$asset"]["kind"] == "skill"
    )
    assert old_pin["rev"] == 1


async def test_directory_skill_source_id_from_frontmatter_and_per_file_version_diff(
    project, evalp_client, client,
):
    (project / "agent.yaml").write_text(
        AGENT_YAML
        + '  map:\n    - {glob: "skills/*", kind: skill, name: "{stem_nover}", '
        'id: "$.skill_id", display: "$.name"}\n',
        encoding="utf-8",
    )
    (project / "exports/current/skills/画图 skill.json").unlink()
    root = project / "exports/current/skills/image-gen-0.0.1"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nskill_id: sk-img-1\nname: 画图\n---\n画一只猫\n", encoding="utf-8")
    (root / "scripts/run.py").write_text("print('cat')\n", encoding="utf-8")
    (root / "scripts/old.txt").write_text("旧文件", encoding="utf-8")
    (root / "logo.bin").write_bytes(PNG)
    (root / "keep.txt").write_text("不变", encoding="utf-8")
    ws = snap_ws(project)
    v1 = (await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")).version
    skill = next(iter((await assets_of(client, "skill")).values()))
    assert skill["source_id"] == "sk-img-1" and skill["display_name"] == "画图"

    upgraded = root.with_name("image-gen-0.0.2")
    root.rename(upgraded)
    (upgraded / "SKILL.md").write_text(
        "---\nskill_id: sk-img-1\nname: 画图\n---\n画一只狗\n", encoding="utf-8"
    )
    (upgraded / "scripts/old.txt").unlink()
    (upgraded / "scripts/new.py").write_text("print('dog')\n", encoding="utf-8")
    (upgraded / "logo.bin").write_bytes(PNG + b"\x00")
    v2 = (await actions.publish(evalp_client, ws, notes=None, tag=None, via="cli")).version
    assert len(await assets_of(client, "skill")) == 1  # 同一个来源 ID，仍是一个资产

    d = (await client.get(f"{API}/versions/diff", params={"left": v1["id"], "right": v2["id"]})).json()
    (c,) = [c for c in d["changes"] if c["asset"]["kind"] == "skill"]
    assert c["op"] == "asset_changed" and "content_diff" not in c
    pd = c["package_diff"]
    assert pd["added"] == ["scripts/new.py"] and pd["removed"] == ["scripts/old.txt"]
    assert pd["unchanged_count"] == 2  # keep.txt、scripts/run.py
    assert (pd["left_root"], pd["right_root"]) == ("image-gen-0.0.1", "image-gen-0.0.2")
    changed = {m["path"]: m for m in pd["modified"]}
    assert "+画一只狗" in changed["SKILL.md"]["content_diff"]["text"]
    assert changed["logo.bin"] == {"path": "logo.bin"}
