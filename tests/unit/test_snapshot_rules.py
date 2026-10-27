"""快照入库规则：glob 匹配、版本号后缀、资产名模板。"""

import pytest
from evalp.spec.models import SnapshotRule
from evalp.workspace_client import WorkspaceError, glob_match, snapshot_rule_asset, strip_version


@pytest.mark.parametrize(
    ("pattern", "rel", "ok"),
    [
        ("system-prompt.md", "system-prompt.md", True),
        ("system-prompt.md", "a/system-prompt.md", False),
        ("skills/*.json", "skills/crawl-0.5.3.json", True),
        ("skills/*.json", "skills/sub/x.json", False),  # * 不跨目录
        ("skills/**", "skills/sub/x.json", True),
        ("**/x.json", "x.json", True),
        ("**/x.json", "a/b/x.json", True),
        ("resources/database*.json", "resources/database-tables.json", True),
        ("resources/database*.json", "resources/tables/a.json", False),
        ("platform/?.json", "platform/a.json", True),
        ("platform/?.json", "platform/ab.json", False),
        ("tables/[ab].json", "tables/a.json", True),
        ("tables/[!ab].json", "tables/a.json", False),
        ("a+b.json", "a+b.json", True),
    ],
)
def test_glob_match(pattern, rel, ok):
    assert glob_match(pattern, rel) is ok


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("crawl-engine-0.5.3", "crawl-engine"),
        ("crawl-engine-1.2", "crawl-engine"),
        ("crawl-engine-v1.2.3", "crawl-engine"),
        ("crawl-engine-V2", "crawl-engine"),
        ("crawl-engine", "crawl-engine"),
        ("web-search-2", "web-search-2"),  # 单个数字不算版本号
        ("agent-v0.5.3-draft", "agent-v0.5.3-draft"),  # 只去掉末尾的
    ],
)
def test_strip_version(stem, expected):
    assert strip_version(stem) == expected


def rule(name, kind="skill", glob="*"):
    return SnapshotRule(glob=glob, kind=kind, name=name)


def test_name_template_variables():
    assert snapshot_rule_asset("My_Agent", rule("{stem_nover}"), "skills/Crawl-Engine-0.5.3.json") == (
        "skill/crawl-engine"
    )
    assert snapshot_rule_asset(
        "My_Agent", rule("{agent}-table-{stem}", "database"), "resources/tables/t1.json"
    ) == ("database/my-agent-table-t1")
    assert snapshot_rule_asset("a", rule("{agent}-{path}", "app_config"), "platform/app-config.json") == (
        "app_config/a-platform-app-config"
    )
    assert snapshot_rule_asset("a", rule("{agent}-system", "prompt"), "system-prompt.md") == "prompt/a-system"


def test_same_skill_in_new_version_maps_to_same_asset():
    r = rule("{stem_nover}")
    assert snapshot_rule_asset("a", r, "skills/x-0.5.3.json") == snapshot_rule_asset(
        "a", r, "skills/x-0.6.0.json"
    )


def test_invalid_template_or_result_is_reported():
    with pytest.raises(WorkspaceError, match=r"未知变量 \{nope\}"):
        snapshot_rule_asset("a", rule("{nope}"), "x.json")
    with pytest.raises(WorkspaceError, match="资产名不合法"):
        snapshot_rule_asset("a", rule("{stem}"), "画图.json")  # 展开后为空
    with pytest.raises(WorkspaceError, match="资产名不合法"):
        snapshot_rule_asset("a", rule("{stem}-" + "x" * 70), "a.json")  # 超长


SKILL_JSON = (
    b'{"id": "sk_9", "name": "\xe7\x88\xac\xe8\x99\xab Engine v2", "meta": {"skill_id": 42, "tags": ["a"]}}'
)


def rule_with(**kw):
    return SnapshotRule(glob="*", kind=kw.pop("kind", "skill"), **kw)


def test_json_path_name_and_id_come_from_file_content():
    from evalp.workspace_client import SnapshotEntry, snapshot_rule_entry

    r = rule_with(name="$.name", id="$.meta.skill_id")
    # 资产名由 name 的原文规范化得到，原文（含中文）作为显示名；id 取原值（数字转字符串）
    assert snapshot_rule_entry("a", r, "skills/x.json", SKILL_JSON) == SnapshotEntry(
        "skill/engine-v2", "42", "爬虫 Engine v2"
    )
    assert snapshot_rule_entry(
        "a", rule_with(name="{stem_nover}", id="$.id"), "s/crawl-0.5.3.json", SKILL_JSON
    ) == (SnapshotEntry("skill/crawl", "sk_9", "crawl"))
    # 没有配置 id 的规则：只有名字
    assert snapshot_rule_entry("a", rule_with(name="{stem}"), "s/crawl.json", SKILL_JSON).source_id is None


def test_json_path_supports_list_index_and_reports_missing_fields():
    from evalp.workspace_client import snapshot_rule_entry

    data = b'{"items": [{"id": "first"}], "n": null, "b": true, "e": " "}'
    assert (
        snapshot_rule_entry("a", rule_with(name="{stem}", id="$.items.0.id"), "x.json", data).source_id
        == "first"
    )
    with pytest.raises(WorkspaceError, match=r"取值失败: 找不到 'nope'.*按实际导出的 JSON 调整"):
        snapshot_rule_entry("a", rule_with(name="{stem}", id="$.nope"), "x.json", data)
    with pytest.raises(WorkspaceError, match="取到的不是非空"):
        snapshot_rule_entry("a", rule_with(name="{stem}", id="$.n"), "x.json", data)
    with pytest.raises(WorkspaceError, match="取到的不是非空"):
        snapshot_rule_entry("a", rule_with(name="{stem}", id="$.b"), "x.json", data)
    with pytest.raises(WorkspaceError, match="取到的不是非空"):
        snapshot_rule_entry("a", rule_with(name="{stem}", id="$.e"), "x.json", data)
    with pytest.raises(WorkspaceError, match="文件不是合法的 JSON"):
        snapshot_rule_entry("a", rule_with(name="$.name"), "x.md", b"# not json")


def test_json_path_name_that_cannot_make_a_valid_asset_name_is_reported():
    from evalp.workspace_client import snapshot_rule_entry

    with pytest.raises(WorkspaceError, match=r"资产名不合法.*展开结果 '画图'"):
        snapshot_rule_entry("a", rule_with(name="$.name"), "x.json", '{"name": "画图"}'.encode())


def test_display_from_json_keeps_chinese_name_while_asset_name_uses_file_name():
    from evalp.workspace_client import SnapshotEntry, snapshot_rule_entry

    rule = rule_with(name="{stem_nover}", id="$.id", display="$.name")
    data = '{"id": "sk-1", "name": "画图"}'.encode()
    assert snapshot_rule_entry("a", rule, "skills/image-gen-0.5.3.json", data) == SnapshotEntry(
        "skill/image-gen", "sk-1", "画图"
    )


def _skill_package(frontmatter: str = "id: sk-img-1\nname: 画图\n") -> bytes:
    from evalp.utils.skill_package import package_entries

    return package_entries([
        ("image-gen-0.5.3/SKILL.md", f"---\n{frontmatter}---\n# 说明".encode()),
        ("image-gen-0.5.3/scripts/run.py", b"print('x')"),
    ])


def test_directory_skill_id_and_display_come_from_skill_md_frontmatter():
    from evalp.workspace_client import SnapshotEntry, snapshot_rule_entry

    r = rule_with(name="{stem_nover}", id="$.id", display="$.name")
    entry = snapshot_rule_entry("a", r, "skills/image-gen-0.5.3", _skill_package(), directory=True)
    assert entry == SnapshotEntry("skill/image-gen", "sk-img-1", "画图")
    # 嵌套字段与数字值；ZIP 文件（非目录）同样从包内 SKILL.md 取值
    nested = _skill_package("meta:\n  skill_id: 42\nname: image-gen\n")
    assert snapshot_rule_entry(
        "a", rule_with(name="$.name", id="$.meta.skill_id"), "skills/image-gen-0.5.3.zip", nested
    ) == SnapshotEntry("skill/image-gen", "42", "image-gen")


def test_directory_skill_missing_frontmatter_field_names_skill_md():
    from evalp.workspace_client import snapshot_rule_entry

    r = rule_with(name="{stem_nover}", id="$.skill_id")
    with pytest.raises(WorkspaceError, match=r"找不到 'skill_id'.*SKILL\.md 的 YAML frontmatter"):
        snapshot_rule_entry("a", r, "skills/image-gen-0.5.3", _skill_package(), directory=True)
    from evalp.utils.skill_package import package_entries

    no_frontmatter = package_entries([("image-gen/SKILL.md", b"# no frontmatter")])
    with pytest.raises(WorkspaceError, match=r"找不到 'id'.*SKILL\.md 的 YAML frontmatter"):
        snapshot_rule_entry(
            "a", rule_with(name="{stem_nover}", id="$.id"), "skills/x", no_frontmatter, directory=True
        )
    with pytest.raises(WorkspaceError, match="取到的不是非空"):
        snapshot_rule_entry(
            "a", rule_with(name="{stem_nover}", id="$.id"), "skills/x",
            _skill_package("id: ' '\n"), directory=True,
        )
