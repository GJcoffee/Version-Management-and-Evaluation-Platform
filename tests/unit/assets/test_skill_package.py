# Synthetic fixtures authored by hand; no recorded agent data.
import io
import stat
import zipfile

import pytest
from evalp.errors import ValidationFailed
from evalp.services.asset_content import validate_content
from evalp.utils.skill_package import SkillPackage, package_directory, package_entries


def test_directory_preserves_every_layer_empty_directory_and_binary(tmp_path):
    root = tmp_path / "image-gen-0.0.1"
    (root / "references" / "nested" / "empty").mkdir(parents=True)
    (root / "scripts").mkdir()
    doc = "---\nname: 虚构图片生成\n---\n# 使用说明\n".encode()
    (root / "SKILL.md").write_bytes(doc)
    binary = bytes(range(256))
    (root / "references" / "nested" / "data.bin").write_bytes(binary)
    (root / "scripts" / "run.py").write_bytes(b"print('original')\r\n")
    first = package_directory(root)
    assert first == package_directory(root)
    package = SkillPackage(first)
    assert package.display_name == "虚构图片生成"
    prefix = root.name + "/"
    assert package.read(prefix + "SKILL.md") == doc
    assert package.read(prefix + "references/nested/data.bin") == binary
    assert package.read(prefix + "scripts/run.py") == b"print('original')\r\n"
    paths = {e["path"]: e for e in package.tree()["entries"]}
    assert paths[prefix + "references/nested/empty"]["type"] == "directory"
    assert package.metadata()["file_count"] == 3
    assert package.default_path == prefix + "SKILL.md"
    assert not package.preview(prefix + "references/nested/data.bin")["content_available"]
    assert package.preview(prefix + "SKILL.md")["content"] == doc.decode()
    assert validate_content("skill", first).meta["display_name"] == "虚构图片生成"


@pytest.mark.parametrize(
    "path",
    ["../escape", "/absolute", "root/../escape", "root\\escape", "C:/escape", "root//a", "root/\x00x"],
)
def test_unsafe_paths_are_rejected(path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("SKILL.md", "# test")
        archive.writestr(path.replace("\x00", "_"), b"x")
    data = out.getvalue().replace(path.replace("\x00", "_").encode(), path.encode())
    with pytest.raises(ValueError):
        SkillPackage(data)


def test_duplicate_paths_symlinks_and_file_directory_conflicts_are_rejected():
    for variant in ("duplicate", "symlink", "conflict"):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("SKILL.md", "# test")
            if variant == "duplicate":
                with pytest.warns(UserWarning):
                    archive.writestr("SKILL.md", "# changed")
            elif variant == "symlink":
                info = zipfile.ZipInfo("link")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "target")
            else:
                archive.writestr("a", "file")
                archive.writestr("a/child", "child")
        with pytest.raises(ValueError):
            SkillPackage(out.getvalue())


def test_invalid_archives_and_missing_skill_document_are_rejected():
    with pytest.raises(ValidationFailed):
        validate_content("skill", b"PKbroken")
    with pytest.raises(ValueError, match="缺少 SKILL.md"):
        package_entries([("scripts/run.py", b"x")])


def test_legacy_metadata_is_not_fabricated_into_files():
    result = validate_content("skill", '{"Name": "虚构旧名称", "Steps": []}'.encode())
    assert result.content_type == "application/json"
    assert result.meta == {"format": "metadata", "display_name": "虚构旧名称"}


def test_expanded_size_and_entry_limits(monkeypatch):
    import evalp.utils.skill_package as mod

    data = package_entries([("SKILL.md", b"# test"), ("data.txt", b"x" * 2048)])
    monkeypatch.setattr(mod, "EXPANDED_LIMIT", 1024)
    with pytest.raises(ValueError, match="100MB"):
        SkillPackage(data)
    monkeypatch.setattr(mod, "EXPANDED_LIMIT", 4096)
    monkeypatch.setattr(mod, "ENTRY_LIMIT", 1)
    with pytest.raises(ValueError, match="10000"):
        SkillPackage(data)
