# Synthetic fixtures authored by hand; no recorded agent data.
import pytest
from evalp.store.models import AssetRevision
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
