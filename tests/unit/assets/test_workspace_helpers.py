from evalp.services.workspace import norm_path, restore_file_refs, rewrite_file_refs

MAPPING = {
    "prompts/system.md": "prompt/cs-system",
    "tools/query.yaml": "tool/query",
    "knowledge/faq.md": "knowledge/faq",
}

SPEC = {
    "kind": "config",
    "prompts": {"system": "file:prompts/system.md"},
    "tools": [
        "file:./tools/query.yaml",
        "asset://tool/other@2",
        {"builtin": "knowledge_search", "with": {"sources": ["file:knowledge/faq.md"]}},
    ],
    "knowledge": {"inject": ["file:knowledge\\faq.md"]},
}


def test_norm_path():
    assert norm_path("./a/b.md") == "a/b.md"
    assert norm_path("a\\b.md") == "a/b.md"
    assert norm_path("a/../b.md") == "b.md"


def test_rewrite_file_refs():
    out, missing = rewrite_file_refs(SPEC, MAPPING)
    assert missing == []
    assert out["prompts"]["system"] == "asset://prompt/cs-system@latest"
    assert out["tools"][0] == "asset://tool/query@latest"
    assert out["tools"][1] == "asset://tool/other@2"
    assert out["tools"][2]["with"]["sources"] == ["asset://knowledge/faq@latest"]
    assert out["knowledge"]["inject"] == ["asset://knowledge/faq@latest"]
    assert SPEC["prompts"]["system"] == "file:prompts/system.md"  # 不修改入参


def test_rewrite_reports_unmapped_files_and_leaves_inline_text():
    spec = {"prompts": {"system": "file:nope.md"}, "tools": ["file:tools/none.yaml"]}
    out, missing = rewrite_file_refs(spec, MAPPING)
    assert missing == ["nope.md", "tools/none.yaml"]
    inline = {"prompts": {"system": "请阅读 file:x 里的内容\n谢谢"}}
    assert rewrite_file_refs(inline, MAPPING) == (inline, [])


def test_restore_file_refs_only_for_latest_and_mapped():
    spec = {
        "prompts": {"system": "asset://prompt/cs-system@latest"},
        "tools": ["asset://tool/query@3", "asset://tool/query@latest", "asset://tool/unmapped@latest"],
        "knowledge": {"inject": ["asset://knowledge/faq@latest"]},
    }
    reverse = {
        "prompt/cs-system": "prompts/system.md",
        "tool/query": "tools/query.yaml",
        "knowledge/faq": "knowledge/faq.md",
    }
    out = restore_file_refs(spec, reverse)
    assert out["prompts"]["system"] == "file:prompts/system.md"
    assert out["tools"] == ["asset://tool/query@3", "file:tools/query.yaml", "asset://tool/unmapped@latest"]
    assert out["knowledge"]["inject"] == ["file:knowledge/faq.md"]
