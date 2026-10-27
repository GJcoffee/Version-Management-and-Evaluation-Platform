"""Spec 规范化与哈希：golden 文件锁定 6 条规则。"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from evalp.spec.canonical import HASH_PREFIX, canonicalize, normalize_text, spec_hash

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN = sorted(GOLDEN_DIR.glob("*.json"))
H = "a" * 64

BASE = {"kind": "config", "model": {"provider": "deepseek", "name": "deepseek-chat"}}


def test_golden_files_exist():
    assert len(GOLDEN) >= 5


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_golden(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    cs = canonicalize(case["spec"])
    assert cs.text == case["canonical"], "规范化文本变了：这会让所有已有版本的哈希失效"
    assert cs.hash == case["hash"]
    # 独立复算哈希：sha256("evalp/v1\n" + canonical)
    assert hashlib.sha256(b"evalp/v1\n" + case["canonical"].encode("utf-8")).hexdigest() == case["hash"]
    assert HASH_PREFIX == "evalp/v1\n"


def test_explicit_defaults_equal_omitted_defaults():
    explicit = {
        "schema": "evalp/v1",
        "kind": "config",
        "model": {
            "provider": "deepseek",
            "name": "deepseek-chat",
            "params": {"temperature": 0.3, "top_p": 1.0, "max_tokens": 4096, "seed": None},
        },
        "prompts": {"system": "", "variables": {}},
        "tools": [],
        "knowledge": {"inject": []},
        "runtime": {
            "max_steps": 12,
            "turn_timeout_s": 180,
            "parallel_tool_calls": True,
            "tool_timeout_s": 30,
        },
    }
    assert spec_hash(explicit) == spec_hash(BASE)


def test_explicit_code_defaults_equal_omitted():
    omitted = {"kind": "code", "transport": "http", "http": {"base_url": "http://x"}}
    explicit = {
        "kind": "code",
        "transport": "http",
        "http": {"base_url": "http://x", "headers": {}, "timeout_s": 300},
        "source": {"repo": ".", "capture_git": True, "allow_dirty": False},
        "params": {},
        "runtime": {"turn_timeout_s": 300},
    }
    assert spec_hash(omitted) == spec_hash(explicit)


def test_non_default_value_changes_hash():
    changed = copy.deepcopy(BASE)
    changed["model"]["params"] = {"temperature": 0.7}
    assert spec_hash(changed) != spec_hash(BASE)


def test_field_order_does_not_matter():
    a = {
        "kind": "config",
        "model": {"provider": "p", "name": "m", "params": {"temperature": 0.5, "seed": 1}},
        "prompts": {"system": "hi", "variables": {"a": 1, "b": {"x": 1, "y": 2}}},
    }
    b = {
        "prompts": {"variables": {"b": {"y": 2, "x": 1}, "a": 1}, "system": "hi"},
        "model": {"params": {"seed": 1, "temperature": 0.5}, "name": "m", "provider": "p"},
        "kind": "config",
    }
    assert spec_hash(a) == spec_hash(b)


def test_display_fields_do_not_affect_hash():
    a = {**BASE, "name": "客服", "description": "d1", "notes": "n1"}
    b = {**BASE, "name": "别的名字", "description": "d2", "notes": "n2"}
    assert spec_hash(a) == spec_hash(b) == spec_hash(BASE)
    cs = canonicalize(a)
    assert not {"name", "description", "notes"} & set(cs.data)  # 存入版本的内容里也没有


def test_asset_content_hash_participates():
    def spec(rev, h):
        return {
            **BASE,
            "prompts": {"system": {"$asset": {"kind": "prompt", "name": "s", "rev": rev, "hash": h}}},
        }

    assert spec_hash(spec(1, H)) == spec_hash(spec(1, H))
    assert spec_hash(spec(1, H)) != spec_hash(spec(2, "b" * 64))  # 新修订
    assert spec_hash(spec(1, H)) != spec_hash(spec(1, "b" * 64))  # 只有内容哈希不同


def test_float_normalization():
    a = {**BASE, "model": {**BASE["model"], "params": {"temperature": 1.0, "top_p": 1}}}
    b = {**BASE, "model": {**BASE["model"], "params": {"temperature": 1, "top_p": 1.0}}}
    assert spec_hash(a) == spec_hash(b)
    assert canonicalize(a).data["model"]["params"]["temperature"] == 1


def test_inline_text_newlines_and_trailing_whitespace():
    a = {**BASE, "prompts": {"system": "第一行  \r\n第二行\t\r\n\r\n第三行"}}
    b = {**BASE, "prompts": {"system": "第一行\n第二行\n\n第三行"}}
    assert spec_hash(a) == spec_hash(b)
    # 行首空白（缩进）和末尾换行是有意义的，不会被去掉
    c = {**BASE, "prompts": {"system": "第一行\n  第二行"}}
    assert spec_hash(c) != spec_hash({**BASE, "prompts": {"system": "第一行\n第二行"}})
    assert spec_hash({**BASE, "prompts": {"system": "x\n"}}) != spec_hash(
        {**BASE, "prompts": {"system": "x"}}
    )
    assert normalize_text("a \r\nb\r c") == "a\nb\n c"


def test_variables_text_is_normalized_too():
    a = {**BASE, "prompts": {"system": "s", "variables": {"k": "v \r\nw"}}}
    b = {**BASE, "prompts": {"system": "s", "variables": {"k": "v\nw"}}}
    assert spec_hash(a) == spec_hash(b)


def test_env_refs_are_hashed_verbatim():
    def spec(token):
        return {
            "kind": "code",
            "transport": "http",
            "http": {"base_url": "http://x", "headers": {"A": token}},
        }

    assert spec_hash(spec("Bearer ${env:T1}")) != spec_hash(spec("Bearer ${env:T2}"))
    assert "${env:T1}" in canonicalize(spec("Bearer ${env:T1}")).text


def test_code_git_pin_participates():
    def spec(commit, patch=None):
        return {
            "kind": "code",
            "transport": "http",
            "http": {"base_url": "http://x"},
            "source": {"git": {"commit": commit, "patch_hash": patch}},
        }

    assert spec_hash(spec("a" * 40)) != spec_hash(spec("b" * 40))
    assert spec_hash(spec("a" * 40)) != spec_hash(spec("a" * 40, H))


def test_canonical_text_is_compact_sorted_unicode():
    cs = canonicalize({**BASE, "prompts": {"system": "中文"}})
    assert " " not in cs.text.replace("中文", "")
    assert "中文" in cs.text and "\\u" not in cs.text
    assert json.loads(cs.text) == cs.data
    assert list(cs.data) == sorted(cs.data)


def test_empty_snapshot_is_omitted_but_declared_snapshot_is_hashed():
    base = {"kind": "code", "transport": "http", "http": {"base_url": "http://x"}}
    assert "snapshot" not in canonicalize(base).data
    assert canonicalize({**base, "snapshot": None}).hash == canonicalize(base).hash
    with_snap = {**base, "snapshot": {"dir": "exports/current"}}
    assert canonicalize(with_snap).data["snapshot"] == {"dir": "exports/current", "files": {}}
    assert canonicalize(with_snap).hash != canonicalize(base).hash


def test_snapshot_map_is_omitted_when_empty_and_hashed_when_declared():
    base = {"kind": "code", "transport": "http", "http": {"base_url": "http://x"}}
    plain = canonicalize({**base, "snapshot": {"dir": "d"}})
    assert "map" not in plain.data["snapshot"]
    assert canonicalize({**base, "snapshot": {"dir": "d", "map": []}}).hash == plain.hash
    rule = {"glob": "skills/*.json", "kind": "skill", "name": "{stem_nover}"}
    mapped = canonicalize({**base, "snapshot": {"dir": "d", "map": [rule]}})
    assert mapped.data["snapshot"]["map"] == [rule] and mapped.hash != plain.hash
