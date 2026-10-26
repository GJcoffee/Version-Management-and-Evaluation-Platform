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
    prompt = by_path["snapshot.files.platform/harness-agent.json"]
    assert prompt["op"] == "asset_changed"
    assert '-  "SystemPrompt": "你是助手。\\n只回答问题。"' in prompt["content_diff"]["text"]
    assert "多回答一点" in prompt["content_diff"]["text"]
    assert "content_diff" not in by_path["snapshot.files.logo.png"]  # 二进制文件只标记变化
    assert by_path["snapshot.files.logo.png"]["op"] == "asset_changed"
    assert by_path["snapshot.files.skills/new.json"]["op"] == "add"
    assert by_path["snapshot.files.skills/画图 skill.json"]["op"] == "remove"


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
