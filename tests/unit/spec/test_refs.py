import pytest
from evalp.spec.refs import (
    AssetRef,
    RefError,
    classify,
    find_env_refs,
    format_asset_ref,
    is_asset_ref,
    is_file_ref,
    make_pin,
    node_ref,
    parse_asset_ref,
    parse_file_ref,
    pin_to_ref,
    validate_asset_name,
)


def test_parse_latest_and_rev():
    assert parse_asset_ref("asset://prompt/cs-system@latest") == AssetRef("prompt", "cs-system", None)
    assert parse_asset_ref("asset://tool/query-order@3") == AssetRef("tool", "query-order", 3)
    assert parse_asset_ref("asset://knowledge/faq").is_latest  # 省略 @ 按 latest 处理
    assert parse_asset_ref("  asset://dataset/reg@12  ").rev == 12


@pytest.mark.parametrize(
    "bad",
    [
        "asset://prompt",
        "asset://prompt/",
        "asset://nope/x@1",
        "asset://prompt/Bad_Name@1",
        "asset://prompt/x@0",
        "asset://prompt/x@abc",
        "asset://prompt/x@-1",
        "asset:/prompt/x@1",
        "prompt/x@1",
        "file:a.md",
    ],
)
def test_parse_rejects_bad_refs(bad):
    with pytest.raises(RefError):
        parse_asset_ref(bad)


def test_format_roundtrip():
    assert format_asset_ref("prompt", "a", None) == "asset://prompt/a@latest"
    assert format_asset_ref("prompt", "a", "latest") == "asset://prompt/a@latest"
    assert format_asset_ref("tool", "b-c", 7) == "asset://tool/b-c@7"
    for s in ("asset://prompt/a@latest", "asset://tool/b-c@7"):
        assert str(parse_asset_ref(s)) == s


def test_is_asset_ref_distinguishes_inline_text():
    assert is_asset_ref("asset://prompt/a@1")
    assert not is_asset_ref("你是客服。\nasset://prompt/a@1")
    assert not is_asset_ref("你是客服")
    assert not is_asset_ref(3)


def test_file_ref():
    assert is_file_ref("file:prompts/system.md")
    assert parse_file_ref("file:prompts/system.md") == "prompts/system.md"
    assert not is_file_ref("file://host/x")
    assert not is_file_ref("see file:x for details")
    with pytest.raises(RefError):
        parse_file_ref("prompts/system.md")


def test_env_refs_recognized_not_resolved():
    text = "Bearer ${env:MY_TOKEN} and ${env:MY_TOKEN} ${env:OTHER_1} ${env:} ${bad}"
    assert find_env_refs(text) == ["MY_TOKEN", "OTHER_1"]
    assert find_env_refs("plain") == []
    assert classify("${env:X}") == "env"
    assert classify("asset://prompt/a@1") == "asset"
    assert classify("file:a.md") == "file"
    assert classify("hello") == "inline"


@pytest.mark.parametrize("name", ["a", "cs-system", "a1-b2", "x--y", "0abc"])
def test_valid_asset_names(name):
    assert validate_asset_name(name) == name


@pytest.mark.parametrize("name", ["", "Abc", "a_b", "a b", "-a", "a-", "中文", "a.b", "a" * 65])
def test_invalid_asset_names(name):
    with pytest.raises(RefError):
        validate_asset_name(name)


def test_pins():
    pin = make_pin("tool", "q", 3, "a" * 64)
    assert pin == {"$asset": {"kind": "tool", "name": "q", "rev": 3, "hash": "a" * 64}}
    assert pin_to_ref(pin) == "asset://tool/q@3"
    assert node_ref(pin) == AssetRef("tool", "q", 3)
    assert node_ref("asset://tool/q@latest") == AssetRef("tool", "q", None)
    assert node_ref("inline text") is None
