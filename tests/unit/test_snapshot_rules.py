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
