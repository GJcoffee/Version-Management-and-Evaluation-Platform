"""工作区客户端：push / pull（进程内应用，ASGITransport）。"""

from __future__ import annotations

import json

import pytest
from evalp.workspace_client import (
    PushConflict,
    State,
    WorkspaceError,
    find_project,
    normalize_stem,
    sha256_bytes,
)

from tests.integration.p2a.conftest import API


async def test_first_push_creates_agent_and_assets(ws, evalp_client, provider, client):
    r = await ws.push(evalp_client)
    assert r.agent_created and r.revision == 1 and not r.unchanged
    assert {u["asset"] for u in r.uploaded} == {
        "prompt/support-system",
        "tool/query-order",
        "dataset/support-regression",
    }
    assert r.validation and r.validation["ok"], r.validation
    # 服务端草稿里 file: 已改写成资产引用
    draft = (await client.get(f"{API}/drafts/{r.draft_id}")).json()
    assert draft["spec"]["prompts"]["system"] == "asset://prompt/support-system@latest"
    assert draft["spec"]["tools"] == ["asset://tool/query-order@latest"]
    # 本地状态
    state = State.load(ws.project.state_file)
    assert (state.agent_id, state.draft_id, state.draft_revision) == (r.agent_id, r.draft_id, 1)
    assert state.files["prompts/system.md"] == sha256_bytes("你是客服助手。\n".encode())


async def test_second_push_without_changes_is_skipped(ws, evalp_client, provider):
    first = await ws.push(evalp_client)
    again = await ws.push(evalp_client)
    assert again.unchanged and again.revision == first.revision
    assert not again.uploaded and len(again.skipped) == 3


async def test_push_uploads_only_changed_files(ws, evalp_client, provider, project_dir):
    await ws.push(evalp_client)
    (project_dir / "prompts" / "system.md").write_text("你是资深客服。\n", encoding="utf-8")
    r = await ws.push(evalp_client)
    assert not r.unchanged and r.revision == 2
    assert [u["path"] for u in r.uploaded] == ["prompts/system.md"] and r.uploaded[0]["rev"] == 2
    assert len(r.skipped) == 2


async def test_conflict_reports_diff_and_force_overrides(ws, evalp_client, provider, client, project_dir):
    first = await ws.push(evalp_client)
    # 有人在 UI 里改了草稿
    draft = (await client.get(f"{API}/drafts/{first.draft_id}")).json()
    spec = {**draft["spec"], "model": {"provider": "deepseek", "name": "deepseek-reasoner"}}
    r = await client.put(f"{API}/drafts/{first.draft_id}", json={"spec": spec, "expected_revision": 1})
    assert r.status_code == 200
    (project_dir / "prompts" / "system.md").write_text("改过的 prompt\n", encoding="utf-8")

    with pytest.raises(PushConflict) as ei:
        await ws.push(evalp_client)
    assert ei.value.current_revision == 2
    assert any(c["path"] == "model.name" for c in ei.value.diff["changes"])

    forced = await ws.push(evalp_client, force=True)
    assert forced.revision == 3
    draft = (await client.get(f"{API}/drafts/{first.draft_id}")).json()
    assert draft["spec"]["model"]["name"] == "deepseek-chat"


async def test_content_missing_is_retried_with_content(ws, evalp_client, provider):
    """state 里的哈希与服务端不一致（如数据目录被重置）时，自动补传内容。"""
    files = {f.path: f.hash for f in ws.collect_files(ws.load_spec())}
    State(files=files).save(ws.project.state_file)
    r = await ws.push(evalp_client)
    assert r.agent_created and len(r.uploaded) == 3


async def test_pull_restores_files_and_file_refs(ws, evalp_client, provider, project_dir):
    await ws.push(evalp_client)
    prompt = project_dir / "prompts" / "system.md"
    original = prompt.read_text(encoding="utf-8")
    prompt.unlink()
    (project_dir / "agent.yaml").write_text("kind: config\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="尚未推送"):
        await ws.pull(evalp_client)  # agent.yaml 有本地修改
    r = await ws.pull(evalp_client, force=True)
    assert prompt.read_text(encoding="utf-8") == original
    assert "prompts/system.md" in r.written and "agent.yaml" in r.written
    text = (project_dir / "agent.yaml").read_text(encoding="utf-8")
    assert "file:prompts/system.md" in text and "file:tools/query-order.yaml" in text
    assert "asset://" not in text
    # pull 后状态同步：不需要再推送
    assert (await ws.push(evalp_client)).unchanged


async def test_pull_picks_up_server_side_asset_change(ws, evalp_client, provider, client, project_dir):
    await ws.push(evalp_client)
    assets = (await client.get(f"{API}/assets", params={"kind": "prompt"})).json()["items"]
    r = await client.post(f"{API}/assets/{assets[0]['id']}/revisions", json={"content": "UI 里改的\n"})
    assert r.status_code == 201
    result = await ws.pull(evalp_client)
    assert result.written == ["prompts/system.md"]
    assert (project_dir / "prompts" / "system.md").read_text(encoding="utf-8") == "UI 里改的\n"


async def test_unmapped_file_ref_is_reported(ws, evalp_client, project_dir):
    (project_dir / "agent.yaml").write_text(
        "kind: config\nprompts:\n  system: file:other/p.md\n", encoding="utf-8"
    )
    with pytest.raises(WorkspaceError, match=r"\[assets\]"):
        await ws.push(evalp_client)


def test_project_discovery_and_stem(project_dir):
    sub = project_dir / "prompts"
    assert find_project(sub).root == project_dir.resolve()
    assert normalize_stem("Query_Order") == "query-order"
    assert json.loads(json.dumps(find_project(sub).assets))["tools/*.yaml"] == "tool/{stem}"
