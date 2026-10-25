"""版本 diff（详设 4.5）。"""

import pytest
from evalp.spec.diff import diff_specs, unified_diff_text
from evalp.spec.models import dump_spec, parse_spec


def pin(kind, name, rev, h):
    return {"$asset": {"kind": kind, "name": name, "rev": rev, "hash": h * 64}}


def spec(**over):
    base = dump_spec(parse_spec({"kind": "config", "model": {"provider": "p", "name": "m"}}))
    base.update(over)
    return base


def by_path(result):
    return {c["path"]: c for c in result["changes"]}


async def test_identical_specs_have_no_changes():
    s = spec()
    r = await diff_specs(s, s)
    assert r["changes"] == []
    assert r["summary"] == {"add": 0, "remove": 0, "replace": 0, "asset_changed": 0}


async def test_dict_keys_replace_add_remove():
    left = spec()
    right = spec()
    right["model"] = {
        **left["model"],
        "params": {**left["model"]["params"], "temperature": 0.7, "stop": ["x"]},
    }
    del right["runtime"]["max_steps"]
    r = by_path(await diff_specs(left, right))
    assert r["model.params.temperature"] == {
        "path": "model.params.temperature",
        "op": "replace",
        "left": 0.3,
        "right": 0.7,
    }
    assert r["model.params.stop"]["op"] == "add" and r["model.params.stop"]["right"] == ["x"]
    assert r["runtime.max_steps"]["op"] == "remove" and r["runtime.max_steps"]["left"] == 12


async def test_bool_vs_int_is_a_change():
    left, right = spec(), spec()
    left["runtime"]["parallel_tool_calls"] = True
    right["runtime"]["parallel_tool_calls"] = 1
    assert (await diff_specs(left, right))["summary"]["replace"] == 1


async def test_asset_changed_with_content_diff():
    texts = {"1" * 64: "a\nb\nc\n", "2" * 64: "a\nB\nc\nd\n", "9" * 64: "same"}

    async def load(p):
        return texts[p["hash"]]

    left = spec(
        tools=[pin("tool", "query-order", 3, "1")],
        prompts={"system": pin("prompt", "sys", 5, "1"), "variables": {}},
    )
    right = spec(
        tools=[pin("tool", "query-order", 4, "2")],
        prompts={"system": pin("prompt", "sys", 7, "2"), "variables": {}},
    )
    r = await diff_specs(left, right, load)
    ch = by_path(r)
    assert set(ch) == {"tools[query-order]", "prompts.system"}
    t = ch["tools[query-order]"]
    assert t["op"] == "asset_changed" and t["left"] == {"rev": 3} and t["right"] == {"rev": 4}
    assert t["content_diff"]["format"] == "unified"
    assert "-b" in t["content_diff"]["text"] and "+B" in t["content_diff"]["text"]
    assert t["content_diff"]["text"].startswith("@@")
    assert ch["prompts.system"]["left"] == {"rev": 5} and ch["prompts.system"]["right"] == {"rev": 7}
    assert r["summary"] == {"add": 0, "remove": 0, "replace": 0, "asset_changed": 2}


async def test_same_hash_different_rev_is_unchanged():
    left = spec(tools=[pin("tool", "t", 1, "1")])
    right = spec(tools=[pin("tool", "t", 2, "1")])
    assert (await diff_specs(left, right))["changes"] == []


async def test_tools_matched_by_name_not_position():
    left = spec(tools=[pin("tool", "a", 1, "1"), pin("tool", "b", 1, "2")])
    right = spec(tools=[pin("tool", "b", 1, "2"), pin("tool", "c", 1, "3"), pin("tool", "a", 1, "1")])
    r = by_path(await diff_specs(left, right))
    assert set(r) == {"tools[c]"}
    assert r["tools[c]"]["op"] == "add" and r["tools[c]"]["right"] == {"kind": "tool", "name": "c", "rev": 1}
    r2 = by_path(await diff_specs(right, left))
    assert r2["tools[c]"]["op"] == "remove"


async def test_builtin_tool_matched_by_builtin_name():
    def bt(top_k, sources):
        return {"builtin": "knowledge_search", "with": {"top_k": top_k, "sources": sources}}

    left = spec(tools=[pin("tool", "a", 1, "1"), bt(5, [pin("knowledge", "faq", 1, "4")])])
    right = spec(
        tools=[
            bt(8, [pin("knowledge", "faq", 2, "5"), pin("knowledge", "extra", 1, "6")]),
            pin("tool", "a", 1, "1"),
        ]
    )

    async def load(p):
        return {"4" * 64: "x", "5" * 64: "y"}.get(p["hash"])

    r = by_path(await diff_specs(left, right, load))
    assert r["tools[knowledge_search].with.top_k"]["left"] == 5
    assert r["tools[knowledge_search].with.sources[faq]"]["op"] == "asset_changed"
    assert r["tools[knowledge_search].with.sources[extra]"]["op"] == "add"
    assert not any(k.startswith("tools[a]") for k in r)


async def test_knowledge_matched_by_asset_name():
    left = spec(knowledge={"inject": [pin("knowledge", "style", 1, "1"), pin("knowledge", "faq", 1, "2")]})
    right = spec(knowledge={"inject": [pin("knowledge", "faq", 1, "2")]})
    r = by_path(await diff_specs(left, right))
    assert list(r) == ["knowledge.inject[style]"] and r["knowledge.inject[style]"]["op"] == "remove"


async def test_plain_lists_compared_by_index():
    left = {"a": ["x", "y"]}
    right = {"a": ["x", "z", "w"]}
    r = by_path(await diff_specs(left, right))
    assert r["a[1]"]["op"] == "replace" and r["a[2]"]["op"] == "add"


async def test_long_or_multiline_text_gets_unified_diff():
    left = spec(prompts={"system": "你是客服。\n请礼貌。\n", "variables": {"short": "a", "long": "x" * 81}})
    right = spec(
        prompts={"system": "你是客服。\n请非常礼貌。\n", "variables": {"short": "b", "long": "x" * 80 + "y"}}
    )
    r = by_path(await diff_specs(left, right))
    assert "content_diff" in r["prompts.system"]
    assert "+请非常礼貌。" in r["prompts.system"]["content_diff"]["text"]
    assert "content_diff" not in r["prompts.variables.short"]
    assert "content_diff" in r["prompts.variables.long"]


async def test_inline_vs_asset_replace():
    async def load(p):
        return "来自资产的提示词\n第二行"

    left = spec(prompts={"system": "内联提示词\n第二行", "variables": {}})
    right = spec(prompts={"system": pin("prompt", "sys", 1, "1"), "variables": {}})
    c = by_path(await diff_specs(left, right, load))["prompts.system"]
    assert c["op"] == "replace" and c["left"] == "内联提示词\n第二行"
    assert c["right"] == {"kind": "prompt", "name": "sys", "rev": 1}
    assert "content_diff" in c


async def test_different_asset_at_same_path_is_replace():
    left = spec(prompts={"system": pin("prompt", "old", 1, "1"), "variables": {}})
    right = spec(prompts={"system": pin("prompt", "new", 1, "2"), "variables": {}})
    c = by_path(await diff_specs(left, right))["prompts.system"]
    assert c["op"] == "replace" and c["left"]["name"] == "old" and c["right"]["name"] == "new"


async def test_non_text_asset_has_no_content_diff():
    left = spec(knowledge={"inject": [pin("file", "blob", 1, "1")]})
    right = spec(knowledge={"inject": [pin("file", "blob", 2, "2")]})
    called = []

    async def load(p):
        called.append(p)
        return "x"

    c = by_path(await diff_specs(left, right, load))["knowledge.inject[blob]"]
    assert c["op"] == "asset_changed" and "content_diff" not in c and not called


def test_unified_diff_text_has_no_file_header():
    text = unified_diff_text("a\nb\nc\n", "a\nB\nc\n", "l", "r")
    assert text.splitlines()[0].startswith("@@")
    assert "---" not in text.splitlines()[0]


@pytest.mark.parametrize("loader", [None])
async def test_no_loader_still_reports_asset_changed(loader):
    left = spec(tools=[pin("tool", "t", 1, "1")])
    right = spec(tools=[pin("tool", "t", 2, "2")])
    c = by_path(await diff_specs(left, right, loader))["tools[t]"]
    assert c["op"] == "asset_changed" and "content_diff" not in c
