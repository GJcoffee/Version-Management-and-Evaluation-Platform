"""浏览器导入通过真实的工作区和版本服务落库。"""

import io
import json
import zipfile

import pytest
from evalp.errors import ValidationFailed
from evalp.services import hiagent_import
from evalp.services.hiagent_snapshot import SnapshotSource, collect_snapshot, dependency_args, read_app
from evalp.store.models import AssetRevision, Version

from .test_workspace_api import count

API = "/api/v1"
IMPORTS = f"{API}/hiagent-imports"
SOURCE = {"workspace_id": "workspace-1", "app_id": "app-1"}


def make_archive(path, source, *, include_skill=True, unsafe_path=False):
    skill = io.BytesIO()
    with zipfile.ZipFile(skill, "w") as bundle:
        bundle.writestr("review/SKILL.md", "# Review\n")
        bundle.writestr("review/_hiagent_meta.yaml", "Name: API_KEY\nDefault: synthetic-value\n")
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("agent/review.yaml", json.dumps({
            "VersionCode": source.publish_id,
            "AppConfig": {
                "AppID": source.app_id,
                "WorkspaceID": source.workspace_id,
                "SingleAgentConfig": {
                    "PrePrompt": f"Review using {source.publish_id}.",
                    "SkillIDs": ["skill-1"],
                    "APIKey": "synthetic-value",
                },
            },
            "AppDepends": {"SkillMap": {"skill-1": {"Name": "review"}}},
        }))
        if include_skill:
            bundle.writestr("skill/review.zip", skill.getvalue())
        if unsafe_path:
            bundle.writestr("../escape.txt", "untrusted")


@pytest.fixture
def mock_exports(monkeypatch, tmp_path):
    calls = []

    async def export(source):
        calls.append(source)
        path = tmp_path / f"{source.publish_id}.zip"
        make_archive(path, source)
        return collect_snapshot(path, [], source)

    monkeypatch.setattr(hiagent_import, "_export", export)
    return calls


async def import_version(client, publish_id, *, expected=200, **target):
    body = {**SOURCE, "publish_id": publish_id, "notes": "导入已发布版本", **target}
    response = await client.post(IMPORTS, json=body)
    assert response.status_code == expected, response.text
    return response.json()


async def test_first_import_appends_and_deduplicates_versions(client, ctx, mock_exports):
    first = await import_version(client, "release-1", agent_name="审核专员", tag="baseline")
    agent_id = first["version"]["agent_id"]
    context = await client.get(f"{IMPORTS}/context", params={"agent_id": agent_id})
    assert context.json() == SOURCE
    second = await import_version(client, "release-2", agent_id=agent_id)
    assert first["version"]["seq"] == 1
    assert second["version"]["seq"] == 2
    assert second["version"]["parent_version_id"] == first["version"]["id"]
    assert second["asset_count"] == 4
    duplicate = await import_version(client, "release-1", agent_id=agent_id)
    assert duplicate["existed"] and duplicate["version"]["id"] == first["version"]["id"]
    rejected = await import_version(client, "release-3", agent_id=agent_id, app_id="app-2", expected=409)
    assert "来源不一致" in rejected["error"]["message"]
    assert len(mock_exports) == 2 and await count(ctx, Version) == 2
    for seq, release in enumerate(("release-1", "release-2"), 1):
        version = (await client.get(f"{API}/versions/{agent_id}:v{seq}")).json()
        assert version["spec"]["params"]["hiagent_publish_id"] == release
        assert version["spec"]["snapshot"]["files"]["system-prompt.md"]["$asset"]["rev"] == seq


async def test_rejected_publication_does_not_save_any_assets_or_drafts(client, ctx, mock_exports):
    first = await import_version(client, "release-1", agent_name="reviewer")
    aid = first["version"]["agent_id"]
    updated = await client.patch(f"{API}/agents/{aid}", json={"conventions": {"tag_from_param": "missing"}})
    assert updated.status_code == 200
    before = await count(ctx, AssetRevision)
    failed = await import_version(client, "release-2", agent_id=aid, expected=400)
    assert failed["error"]["code"] == "CONVENTION_VIOLATION"
    assert len(mock_exports) == 2
    assert await count(ctx, Version) == 1
    assert await count(ctx, AssetRevision) == before
    drafts = (await client.get(f"{API}/agents/{aid}/drafts")).json()
    draft = next(item for item in drafts if item["name"] == "hiagent-import")
    assert draft["spec"]["params"]["hiagent_publish_id"] == "release-1"


def test_snapshot_redacts_metadata_and_retains_skill_source(tmp_path):
    source = SnapshotSource(**SOURCE, publish_id="release-1")
    archive = tmp_path / "export.zip"
    make_archive(archive, source)
    with zipfile.ZipFile(archive) as bundle:
        _, app = read_app(bundle, source)
        assert dependency_args(app) == ["--skill-id", "skill-1"]
    files = collect_snapshot(archive, [], source)
    skill = next(file for file in files if file.kind == "skill")
    assert skill.source_id == "skill-1"
    with zipfile.ZipFile(io.BytesIO(skill.data)) as bundle:
        assert bundle.read("review/SKILL.md") == b"# Review\n"
        assert b"synthetic-value" not in bundle.read("review/_hiagent_meta.yaml")
    config = next(file for file in files if file.kind == "app_config")
    assert b"synthetic-value" not in config.data
    with pytest.raises(ValidationFailed, match="发布编号"):
        collect_snapshot(archive, [], SnapshotSource(**SOURCE, publish_id="wrong"))


@pytest.mark.parametrize("unsafe_path", [True, False])
def test_unsafe_or_incomplete_archives_are_rejected(tmp_path, unsafe_path):
    source = SnapshotSource(**SOURCE, publish_id="release-1")
    archive = tmp_path / "export.zip"
    make_archive(archive, source, include_skill=unsafe_path, unsafe_path=unsafe_path)
    with pytest.raises(ValidationFailed, match="文件路径" if unsafe_path else "缺少技能包"):
        collect_snapshot(archive, [], source)
