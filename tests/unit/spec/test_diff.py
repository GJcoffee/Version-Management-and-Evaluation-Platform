"""版本 diff（详设 4.5）。"""

import pytest
from evalp.spec.diff import diff_specs, identity_paths, unified_diff_text
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
    left = spec(knowledge={"inject": [pin("dataset", "blob", 1, "1")]})
    right = spec(knowledge={"inject": [pin("dataset", "blob", 2, "2")]})
    called = []

    async def load(p):
        called.append(p)
        return "x"

    c = by_path(await diff_specs(left, right, load))["knowledge.inject[blob]"]
    assert c["op"] == "asset_changed" and "content_diff" not in c and not called


async def test_file_asset_gets_content_diff_when_text():
    left = {"kind": "code", "snapshot": {"dir": "x", "files": {"a.json": pin("file", "f", 1, "1")}}}
    right = {"kind": "code", "snapshot": {"dir": "x", "files": {"a.json": pin("file", "f", 2, "2")}}}

    async def load(p):
        return "a\n" if p["rev"] == 1 else "b\n"

    c = by_path(await diff_specs(left, right, load))["snapshot.files[file/f]"]
    assert (
        c["op"] == "asset_changed" and "-a" in c["content_diff"]["text"] and "+b" in c["content_diff"]["text"]
    )


def snap(**files):
    return {"kind": "code", "snapshot": {"dir": "x", "files": files}}


async def test_snapshot_files_are_matched_by_asset_not_by_path():
    """skill 升级后文件名带新版本号：仍是同一个资产的新修订；只在一侧的资产是新增 / 删除。"""
    left = snap(
        **{
            "skills/crawl-engine-0.5.3.json": pin("skill", "crawl-engine", 3, "a"),
            "skills/old.json": pin("skill", "old", 1, "b"),
            "same.json": pin("app_config", "cfg", 1, "c"),
        }
    )
    right = snap(
        **{
            "skills/crawl-engine-0.5.4.json": pin("skill", "crawl-engine", 4, "d"),
            "skills/new.json": pin("skill", "new", 1, "e"),
            "same.json": pin("app_config", "cfg", 1, "c"),
        }
    )
    result = await diff_specs(left, right)
    changes = by_path(result)
    assert set(changes) == {
        "snapshot.files[skill/crawl-engine]",
        "snapshot.files[skill/old]",
        "snapshot.files[skill/new]",
    }
    up = changes["snapshot.files[skill/crawl-engine]"]
    assert up["op"] == "asset_changed" and up["asset"] == {"kind": "skill", "name": "crawl-engine"}
    assert (up["left"], up["right"]) == ({"rev": 3}, {"rev": 4})
    assert changes["snapshot.files[skill/old]"]["op"] == "remove"
    assert changes["snapshot.files[skill/new]"]["right"]["name"] == "new"


async def test_snapshot_files_empty_on_one_side_lists_every_asset_as_add():
    result = await diff_specs(snap(), snap(**{"a.json": pin("skill", "a", 1, "a")}))
    assert [(c["path"], c["op"]) for c in result["changes"]] == [("snapshot.files[skill/a]", "add")]


def test_identity_paths_for_snapshot_files():
    s = snap(**{"skills/a-1.0.json": pin("skill", "a", 1, "a"), "b.json": pin("file", "snap-b", 1, "b")})
    assert identity_paths(s) == {
        "snapshot.files.skills/a-1.0.json": "snapshot.files[skill/a]",
        "snapshot.files.b.json": "snapshot.files[file/snap-b]",
    }


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


def test_identity_paths_map_index_to_identity_key():
    s = spec(
        prompts={"system": pin("prompt", "sys", 1, "a")},
        tools=[
            pin("tool", "q", 1, "b"),
            {"builtin": "knowledge_search", "with": {"sources": [pin("knowledge", "faq", 1, "c")]}},
        ],
        knowledge={"inject": [pin("knowledge", "faq", 1, "c")]},
    )
    assert identity_paths(s) == {
        "prompts.system": "prompts.system",
        "tools[0]": "tools[q]",
        "tools[1].with.sources[0]": "tools[knowledge_search].with.sources[faq]",
        "knowledge.inject[0]": "knowledge.inject[faq]",
    }


async def test_identity_paths_equal_diff_paths():
    """identity_paths 的结果必须与 diff 产出的 asset_changed 路径同一套规则。"""
    left = spec(tools=[pin("tool", "q", 1, "b"), pin("tool", "r", 1, "d")])
    right = spec(tools=[pin("tool", "r", 2, "e"), pin("tool", "q", 2, "f")])  # 顺序也变了
    changed = {c["path"] for c in (await diff_specs(left, right))["changes"] if c["op"] == "asset_changed"}
    assert changed == {"tools[q]", "tools[r]"}
    assert changed <= set(identity_paths(left).values()) & set(identity_paths(right).values())


def test_identity_paths_fall_back_to_index_when_keys_duplicate():
    s = spec(knowledge={"inject": [pin("knowledge", "faq", 1, "c"), pin("knowledge", "faq", 2, "d")]})
    assert identity_paths(s)["knowledge.inject[1]"] == "knowledge.inject[1]"


async def test_renamed_asset_is_matched_by_identity_and_reported_even_if_content_is_unchanged():
    def snap(name, rev, h):
        return spec(snapshot={"dir": "d", "files": {"skills/a.json": pin("skill", name, rev, h)}})

    renames = {("skill", "old-name"): "new-name"}
    # 内容变了：asset_changed，asset 里带旧名，并附文本 diff
    texts = {"a" * 64: '{"v": 1}', "b" * 64: '{"v": 2}'}

    async def load_text(p):
        return texts[p["hash"]]

    r = await diff_specs(snap("old-name", 3, "a"), snap("new-name", 4, "b"), load_text, renames)
    (c,) = r["changes"]
    assert c["op"] == "asset_changed" and c["path"] == "snapshot.files[skill/new-name]"
    assert c["asset"] == {"kind": "skill", "name": "new-name", "renamed_from": "old-name"}
    assert c["left"] == {"rev": 3} and c["right"] == {"rev": 4}
    assert '-{"v": 1}' in c["content_diff"]["text"] and '+{"v": 2}' in c["content_diff"]["text"]
    # 只改名、内容没变：仍然报告（没有文本 diff）
    r = await diff_specs(snap("old-name", 1, "a"), snap("new-name", 1, "a"), load_text, renames)
    (c,) = r["changes"]
    assert c["op"] == "asset_changed" and c["asset"]["renamed_from"] == "old-name"
    assert c["left"] == c["right"] == {"rev": 1} and "content_diff" not in c
    # 不传 renames：按名字匹配，旧名删除 + 新名新增
    r = await diff_specs(snap("old-name", 1, "a"), snap("new-name", 1, "a"))
    assert {c["op"] for c in r["changes"]} == {"add", "remove"}


# ---- Skill 文件包的逐文件 diff（手写虚构数据） ----


def _package(version, files):
    from evalp.utils.skill_package import SkillPackage, package_entries

    root = f"image-gen-{version}"
    return SkillPackage(package_entries([(f"{root}/{p}", d) for p, d in files.items()]))


def _package_loader(packages):
    async def load(p):
        return packages.get(p["hash"])

    return load


async def test_package_diff_lists_files_and_text_diffs_ignoring_versioned_root():
    left = _package("0.0.1", {
        "SKILL.md": "---\nname: image-gen\n---\n画一只猫\n".encode(),
        "scripts/run.py": b"print('a')\n",
        "assets/logo.bin": bytes(range(8)),
        "assets/keep.txt": b"same",
        "old.txt": b"gone",
    })
    right = _package("0.0.2", {
        "SKILL.md": "---\nname: image-gen\n---\n画一只狗\n".encode(),
        "scripts/run.py": b"print('a')\n",
        "assets/logo.bin": bytes(range(9)),
        "assets/keep.txt": b"same",
        "new.txt": b"added",
    })
    load = _package_loader({"1" * 64: left, "2" * 64: right})
    r = await diff_specs(
        spec(snapshot={"files": {"skills/image-gen": pin("skill", "image-gen", 1, "1")}}),
        spec(snapshot={"files": {"skills/image-gen": pin("skill", "image-gen", 2, "2")}}),
        load_package=load,
    )
    change = by_path(r)["snapshot.files[skill/image-gen]"]
    assert change["op"] == "asset_changed" and "content_diff" not in change
    pd = change["package_diff"]
    assert pd["added"] == ["new.txt"] and pd["removed"] == ["old.txt"]
    assert pd["unchanged_count"] == 2
    assert (pd["left_root"], pd["right_root"]) == ("image-gen-0.0.1", "image-gen-0.0.2")
    skill_md, binary = pd["modified"]
    assert skill_md["path"] == "SKILL.md" and "-画一只猫" in skill_md["content_diff"]["text"]
    assert "+画一只狗" in skill_md["content_diff"]["text"]
    assert binary == {"path": "assets/logo.bin"}  # 二进制只列出


async def test_package_against_legacy_metadata_keeps_plain_asset_changed():
    package = _package("0.0.1", {"SKILL.md": b"# x"})

    async def load_text(p):  # 整包不是 UTF-8 文本，旧 JSON 一侧是文本
        return None if p["hash"] == "1" * 64 else '{"Name": "image-gen"}'

    r = await diff_specs(
        spec(snapshot={"files": {"s": pin("skill", "image-gen", 1, "1")}}),
        spec(snapshot={"files": {"s": pin("skill", "image-gen", 2, "2")}}),
        load_text,
        load_package=_package_loader({"1" * 64: package, "2" * 64: None}),
    )
    change = by_path(r)["snapshot.files[skill/image-gen]"]
    assert change["op"] == "asset_changed"
    assert "package_diff" not in change and "content_diff" not in change
