# Synthetic fixtures authored by hand; no recorded agent data.
import json

import pytest
from evalp.store.models import Asset, AssetRevision, VersionAssetRef
from evalp.utils.skill_package import package_entries

from .conftest import API


def source_package(version="0.0.1", text=b"# Original\n"):
    root = f"image-gen-{version}"
    return package_entries([
        (root + "/SKILL.md", b"---\nname: image-gen\n---\n" + text),
        (root + "/references/deep/guide.md", "原始说明".encode()),
        (root + "/references/deep/empty/", b""),
        (root + "/assets/binary.bin", bytes(range(256))),
    ])


async def import_package(client, data):
    response = await client.post(f"{API}/assets/skill/upload", files=[("files", ("source.zip", data))])
    assert response.status_code == 200, response.text
    upload = response.json()
    result = await client.post(f"{API}/assets", json={
        "kind": "skill", "name": upload["suggested_name"], "blob_upload_id": upload["blob_upload_id"],
    })
    assert result.status_code in (200, 201), result.text
    return result.json()


async def test_native_package_original_name_tree_preview_and_byte_exact_download(client):
    data = source_package()
    result = await import_package(client, data)
    assert result["asset"]["name"] == "image-gen"
    assert result["asset"]["display_name"] == "image-gen"
    rev = result["revision"]["id"]
    url = f"{API}/assets/revisions/{rev}"
    raw = await client.get(url + "/raw")
    assert raw.content == data
    assert ".zip" in raw.headers["content-disposition"]
    tree = (await client.get(url + "/skill/tree")).json()
    assert tree["file_count"] == 3
    assert tree["default_path"] == "image-gen-0.0.1/SKILL.md"
    assert "image-gen-0.0.1/references/deep/empty" in {e["path"] for e in tree["entries"]}
    file = (await client.get(url + "/skill/file", params={"path": tree["default_path"]})).json()
    assert "# Original" in file["content"]
    binary_path = "image-gen-0.0.1/assets/binary.bin"
    preview = (await client.get(url + "/skill/file", params={"path": binary_path})).json()
    assert not preview["content_available"]
    binary = await client.get(url + "/skill/file", params={"path": binary_path, "download": True})
    assert binary.content == bytes(range(256))
    assert binary.headers["content-disposition"].startswith("attachment")
    assert binary.headers["x-content-type-options"] == "nosniff"
    assert (await client.get(url + "/skill/file", params={"path": "../escape"})).status_code == 404
    found = (await client.get(f"{API}/assets", params={"q": "image-gen"})).json()["items"]
    assert found[0]["id"] == result["asset"]["id"]
    detail = (await client.get(url)).json()
    assert not detail["content_available"]


async def test_upgrade_and_dedup_keep_one_asset_and_old_revision_paths(client):
    first = await import_package(client, source_package())
    same = await import_package(client, source_package())
    assert same["existed"] and same["revision"]["id"] == first["revision"]["id"]
    second = await import_package(client, source_package("0.0.2", b"# Improved\n"))
    assert second["asset"]["id"] == first["asset"]["id"]
    assert second["revision"]["rev"] == 2
    old_tree = (await client.get(f"{API}/assets/revisions/{first['revision']['id']}/skill/tree")).json()
    assert old_tree["default_path"] == "image-gen-0.0.1/SKILL.md"


async def test_folder_upload_preserves_relative_paths_and_unicode_name(client):
    response = await client.post(f"{API}/assets/skill/upload", data={"folder": "true"}, files=[
        ("files", ("demo-skill/SKILL.md", "---\nname: 虚构中文示例\n---\n# 说明".encode())),
        ("files", ("demo-skill/scripts/deep/run.py", b"print('unchanged')")),
    ])
    assert response.status_code == 200, response.text
    upload = response.json()
    assert upload["display_name"] == "虚构中文示例" and upload["file_count"] == 2
    result = await client.post(f"{API}/assets", json={
        "kind": "skill", "name": upload["suggested_name"], "blob_upload_id": upload["blob_upload_id"],
    })
    assert result.status_code == 201
    assert result.json()["asset"]["display_name"] == "虚构中文示例"


async def test_legacy_name_is_available_without_rewriting_historical_blob(api, client, ctx):
    created = await api.asset("skill", "old-skill", '{"Name": "虚构旧名称"}')
    rid = created["revision"]["id"]
    async with ctx.db.write() as session:
        row = await session.get(AssetRevision, rid)
        row.meta = {"origin": "snapshot"}
    asset = (await client.get(f"{API}/assets/{created['asset']['id']}")).json()
    assert asset["display_name"] == "虚构旧名称"
    assert asset["latest"]["meta"]["format"] == "metadata"
    assert (await client.get(f"{API}/assets/revisions/{rid}/skill/tree")).status_code == 400
    raw = await client.get(f"{API}/assets/revisions/{rid}/raw")
    assert raw.content == '{"Name": "虚构旧名称"}'.encode()


async def test_description_upload_search_edit_and_revision_history(client):
    def package(description):
        source = f"---\nname: demo\ndescription: {description}\n---\n".encode()
        return package_entries([("demo/SKILL.md", source)])

    first_data = package("生成虚构图片")
    response = await client.post(f"{API}/assets/skill/upload", files=[("files", ("demo.zip", first_data))])
    upload = response.json()
    assert upload["description"] == "生成虚构图片"
    first = await import_package(client, first_data)
    second = await import_package(client, package("处理虚构视频"))
    aid = first["asset"]["id"]
    assert second["asset"]["id"] == aid
    assert second["asset"]["description"] == "处理虚构视频"
    found = (await client.get(f"{API}/assets", params={"kind": "skill", "q": "虚构视频"})).json()["items"]
    assert found[0]["id"] == aid
    missing = (await client.get(f"{API}/assets", params={"kind": "skill", "q": "虚构图片"})).json()["items"]
    assert missing == []
    old = (await client.get(f"{API}/assets/revisions/{first['revision']['id']}")).json()
    assert old["meta"]["description"] == "生成虚构图片"
    edited = await client.patch(f"{API}/assets/{aid}", json={"description": "人工补充用途"})
    assert edited.status_code == 200 and edited.json()["description"] == "人工补充用途"
    assert edited.json()["latest_rev"] == 2
    assert (await client.get(f"{API}/assets", params={"q": "人工补充"})).json()["items"][0]["id"] == aid
    assert (await client.get(f"{API}/assets/revisions/{first['revision']['id']}/raw")).content == first_data


@pytest.mark.parametrize("packaged", [True, False])
async def test_existing_description_search_and_history(api, client, ctx, packaged):
    if packaged:
        source = b"# Updated notes\n\n---\nname: demo\ndescription: unique-purpose\n---\n"
        data = package_entries([("demo/SKILL.md", source)])
        created = await import_package(client, data)
    else:
        data = b'{"Name":"demo","Description":"unique-purpose"}'
        created = await api.asset("skill", "demo", data.decode())
    rid, aid = created["revision"]["id"], created["asset"]["id"]
    historical_meta = {"origin": "snapshot", **({"format": "skill_package"} if packaged else {})}
    async with ctx.db.write() as session:
        row = await session.get(AssetRevision, rid)
        row.meta = historical_meta
    await api.asset("skill", "newer-unrelated", '{"Name":"unrelated"}')
    # 旧修订在第一次读取时补出描述并写回，搜索只查数据库中已有的描述。
    assert (await client.get(f"{API}/assets/{aid}")).json()["description"] == "unique-purpose"
    for _ in range(2):
        response = await client.get(f"{API}/assets", params={"q": "unique-purpose", "limit": 1})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1 and items[0]["id"] == aid
        assert items[0]["description"] == "unique-purpose"
        assert response.json()["next_cursor"] is None
        detail = (await client.get(f"{API}/assets/revisions/{rid}")).json()
        assert detail["meta"]["description"] == "unique-purpose"
    async with ctx.db.read() as session:
        row = await session.get(AssetRevision, rid)
        assert row.meta["origin"] == "snapshot" and row.meta["description"] == "unique-purpose"
    assert (await client.get(f"{API}/assets/revisions/{rid}/raw")).content == data


@pytest.fixture
def blob_reads(ctx, monkeypatch):
    """记录读取 blob 的次数：补描述时才需要解析包。"""
    reads = []
    original = ctx.blobs.get_bytes

    async def get_bytes(blob_hash):
        reads.append(blob_hash)
        return await original(blob_hash)

    monkeypatch.setattr(ctx.blobs, "get_bytes", get_bytes)
    return reads


@pytest.mark.parametrize("description", ["unique-purpose", None])
async def test_legacy_description_is_persisted_once(api, client, ctx, blob_reads, description):
    body = {"Name": "demo", **({"Description": description} if description else {})}
    created = await api.asset("skill", "demo", json.dumps(body))
    rid, aid = created["revision"]["id"], created["asset"]["id"]
    async with ctx.db.write() as session:
        (await session.get(AssetRevision, rid)).meta = {"origin": "snapshot"}
    for _ in range(3):
        asset = (await client.get(f"{API}/assets/{aid}")).json()
        assert asset["description"] == description
        assert (await client.get(f"{API}/assets", params={"kind": "skill"})).json()["items"][0]["id"] == aid
    assert len(blob_reads) == 1
    detail = (await client.get(f"{API}/assets/revisions/{rid}")).json()
    assert detail["meta"].get("description") == description
    async with ctx.db.read() as session:
        meta = (await session.get(AssetRevision, rid)).meta
    assert meta["origin"] == "snapshot" and meta["format"] == "metadata"
    assert meta["description"] == description


async def test_search_uses_stored_description_without_reading_packages(api, client, ctx, blob_reads):
    legacy = await api.asset("skill", "demo", '{"Name":"demo","Description":"unique-purpose"}')
    async with ctx.db.write() as session:
        (await session.get(AssetRevision, legacy["revision"]["id"])).meta = {"origin": "snapshot"}
    # 描述补入一次后，搜索只查数据库。
    await client.get(f"{API}/assets/{legacy['asset']['id']}")
    blob_reads.clear()
    other = await api.asset("skill", "other", '{"Name":"other"}')
    found = (await client.get(f"{API}/assets", params={"q": "unique-purpose", "limit": 1})).json()
    assert [i["id"] for i in found["items"]] == [legacy["asset"]["id"]]
    assert found["next_cursor"] is None
    assert (await client.get(f"{API}/assets", params={"q": "nothing-matches"})).json()["items"] == []
    assert blob_reads == []
    # 即使有尚未补描述的旧修订，搜索也不解压任何包。
    async with ctx.db.write() as session:
        (await session.get(AssetRevision, other["revision"]["id"])).meta = {"origin": "snapshot"}
    await client.get(f"{API}/assets", params={"q": "unique-purpose"})
    assert blob_reads == []


@pytest.mark.parametrize("packaged", [True, False])
async def test_agent_skill_list_description_uses_pinned_revision(api, client, ctx, packaged):
    async def create(description):
        if not packaged:
            return await api.asset("skill", "demo", f'{{"Name":"demo","Description":"{description}"}}')
        source = f"---\nname: demo\ndescription: {description}\n---\n".encode()
        data = package_entries([("demo/SKILL.md", source)])
        return await import_package(client, data)

    old = await create("旧版用途")
    latest = await create("新版用途")
    agent = await api.agent("demo-agent", kind="code")
    v1 = (await api.publish(agent["draft"]))["version"]
    spec = {**agent["draft"]["spec"], "params": {"phase": 2}}
    saved = (await api.save(agent["draft"], spec))["draft"]
    v2 = (await api.publish(saved))["version"]
    meta = {"origin": "snapshot", **({"format": "skill_package"} if packaged else {})}
    async with ctx.db.write() as session:
        asset = await session.get(Asset, old["asset"]["id"])
        asset.description = "当前资产说明，不应泄漏到历史版本"
        for version, created in [(v1, old), (v2, latest)]:
            revision = await session.get(AssetRevision, created["revision"]["id"])
            revision.meta = meta
            session.add(VersionAssetRef(
                version_id=version["id"], asset_revision_id=revision.id, spec_path="snapshot.skills.demo",
            ))
    for version, description, rev in [(v1, "旧版用途", 1), (v2, "新版用途", 2)]:
        response = await client.get(f"{API}/versions/{version['id']}/assets")
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["description"] == description and item["rev"] == rev
    async with ctx.db.read() as session:
        for created in (old, latest):
            revision = await session.get(AssetRevision, created["revision"]["id"])
            assert revision.meta["origin"] == "snapshot"
            assert revision.meta["description"] in ("旧版用途", "新版用途")


@pytest.mark.parametrize("data", [b"PKbroken", b'{"name":"only metadata"}'])
async def test_skill_import_rejects_non_packages(client, data):
    response = await client.post(f"{API}/assets/skill/upload", files=[("files", ("test.zip", data))])
    assert response.status_code == 400
    assert (await client.get(f"{API}/assets")).json()["items"] == []


async def test_revision_diff_returns_per_file_package_diff(client):
    first = await import_package(client, source_package())
    second = await import_package(client, source_package("0.0.2", b"# Improved\n"))
    diff = (await client.get(f"{API}/assets/diff", params={
        "left": first["revision"]["id"], "right": second["revision"]["id"],
    })).json()
    assert diff["type"] == "skill_package" and not diff["identical"]
    pd = diff["package_diff"]
    assert (pd["added"], pd["removed"]) == ([], [])
    assert pd["unchanged_count"] == 2
    assert [m["path"] for m in pd["modified"]] == ["SKILL.md"]
    assert "-# Original" in pd["modified"][0]["content_diff"]["text"]
    assert "+# Improved" in pd["modified"][0]["content_diff"]["text"]
    assert (pd["left_root"], pd["right_root"]) == ("image-gen-0.0.1", "image-gen-0.0.2")


async def test_revision_diff_package_against_legacy_metadata_has_no_package_diff(api, client):
    legacy = await api.asset("skill", "image-gen", '{"Name": "image-gen"}')
    package = await import_package(client, source_package())
    assert package["asset"]["id"] == legacy["asset"]["id"]
    diff = (await client.get(f"{API}/assets/diff", params={
        "left": legacy["revision"]["id"], "right": package["revision"]["id"],
    })).json()
    assert diff["type"] == "binary" and "package_diff" not in diff
