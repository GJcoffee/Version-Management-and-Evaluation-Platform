import pytest
from evalp.spec.models import (
    CodeSpec,
    ConfigSpec,
    dump_spec,
    format_validation_errors,
    parse_spec,
    spec_json_schema,
)
from pydantic import ValidationError

H = "b" * 64


def errors_of(raw):
    with pytest.raises(ValidationError) as ei:
        parse_spec(raw)
    return {e["path"]: e["message"] for e in format_validation_errors(ei.value)}


def test_config_defaults_follow_design_doc():
    s = parse_spec({"kind": "config", "model": {"provider": "p", "name": "m"}})
    assert isinstance(s, ConfigSpec)
    d = dump_spec(s)
    assert d["schema"] == "evalp/v1"
    assert d["model"]["params"] == {"temperature": 0.3, "top_p": 1.0, "max_tokens": 4096, "seed": None}
    assert d["runtime"] == {
        "max_steps": 12,
        "turn_timeout_s": 180,
        "parallel_tool_calls": True,
        "tool_timeout_s": 30,
    }
    assert d["prompts"] == {"system": "", "variables": {}}
    assert d["tools"] == [] and d["knowledge"] == {"inject": []}


def test_code_defaults_follow_design_doc():
    s = parse_spec({"kind": "code", "transport": "http", "http": {"base_url": "http://x"}})
    assert isinstance(s, CodeSpec)
    d = dump_spec(s)
    assert d["http"] == {"base_url": "http://x", "headers": {}, "timeout_s": 300}
    assert d["subprocess"] is None
    assert d["source"] == {"repo": ".", "capture_git": True, "allow_dirty": False, "git": None}
    assert d["params"] == {} and d["runtime"] == {"turn_timeout_s": 300}


def test_kind_is_the_discriminator():
    assert isinstance(parse_spec({"kind": "config", "model": {"provider": "p", "name": "m"}}), ConfigSpec)
    assert errors_of({"model": {"provider": "p", "name": "m"}}) == {"kind": "kind 必须是 config 或 code"}
    assert "kind" in errors_of({"kind": "other"})


def test_schema_field_fixed():
    raw = {"kind": "config", "model": {"provider": "p", "name": "m"}}
    parse_spec({**raw, "schema": "evalp/v1"})
    assert "schema" in errors_of({**raw, "schema": "evalp/v2"})


def test_unknown_fields_rejected_with_path():
    errs = errors_of({"kind": "config", "model": {"provider": "p", "name": "m", "oops": 1}, "typo": 1})
    assert "model.oops" in errs and "typo" in errs


def test_model_params_allow_passthrough_extras():
    s = parse_spec(
        {"kind": "config", "model": {"provider": "p", "name": "m", "params": {"frequency_penalty": 0.5}}}
    )
    assert dump_spec(s)["model"]["params"]["frequency_penalty"] == 0.5


def test_tools_accept_asset_refs_and_builtin():
    s = parse_spec(
        {
            "kind": "config",
            "model": {"provider": "p", "name": "m"},
            "tools": [
                "asset://tool/query-order@3",
                {
                    "builtin": "knowledge_search",
                    "with": {"sources": ["asset://knowledge/faq@latest"], "top_k": 7},
                },
                {"builtin": "knowledge_search"},
            ],
        }
    )
    d = dump_spec(s)
    assert d["tools"][0] == "asset://tool/query-order@3"
    assert d["tools"][1] == {
        "builtin": "knowledge_search",
        "with": {"sources": ["asset://knowledge/faq@latest"], "top_k": 7},
    }
    assert d["tools"][2]["with"] == {"sources": [], "top_k": 5}


def test_tool_errors_have_clean_paths():
    errs = errors_of(
        {
            "kind": "config",
            "model": {"provider": "p", "name": "m"},
            "tools": [
                "asset://prompt/x@1",
                {"builtin": "nope"},
                5,
                {"builtin": "knowledge_search", "with": {"top_k": 0}},
                "file:tools/a.yaml",
            ],
        }
    )
    assert "tools[0]" in errs and "tool" in errs["tools[0]"]
    assert "tools[1].builtin" in errs
    assert "tools[2]" in errs
    assert "tools[3].with.top_k" in errs
    assert "file:" in errs["tools[4]"]


def test_system_prompt_inline_or_ref():
    raw = {"kind": "config", "model": {"provider": "p", "name": "m"}}
    assert (
        dump_spec(parse_spec({**raw, "prompts": {"system": "多行\n文本"}}))["prompts"]["system"]
        == "多行\n文本"
    )
    assert (
        dump_spec(parse_spec({**raw, "prompts": {"system": "asset://prompt/a@latest"}}))["prompts"]["system"]
        == "asset://prompt/a@latest"
    )
    assert "prompts.system" in errors_of({**raw, "prompts": {"system": "asset://tool/a@1"}})
    assert "file:" in errors_of({**raw, "prompts": {"system": "file:prompts/system.md"}})["prompts.system"]


def test_resolved_form_is_parseable():
    """固化后的 Spec（含 $asset）能被同一套模型解析，运行时可直接使用。"""
    pin = lambda k, n, r: {"$asset": {"kind": k, "name": n, "rev": r, "hash": H}}  # noqa: E731
    raw = {
        "kind": "config",
        "model": {"provider": "p", "name": "m"},
        "prompts": {"system": pin("prompt", "sys", 2)},
        "tools": [
            pin("tool", "q", 1),
            {"builtin": "knowledge_search", "with": {"sources": [pin("knowledge", "faq", 4)]}},
        ],
        "knowledge": {"inject": [pin("knowledge", "style", 1)]},
    }
    s = parse_spec(raw)
    d = dump_spec(s)
    assert d["prompts"]["system"] == pin("prompt", "sys", 2)
    assert d["tools"][0] == pin("tool", "q", 1)
    assert d["tools"][1]["with"]["sources"] == [pin("knowledge", "faq", 4)]
    assert errors_of({**raw, "tools": [pin("prompt", "q", 1)]}).keys() == {"tools[0]"}


def test_code_transport_sections():
    ok = parse_spec(
        {"kind": "code", "transport": "subprocess", "subprocess": {"command": ["python", "-m", "x"]}}
    )
    assert dump_spec(ok)["subprocess"] == {"command": ["python", "-m", "x"], "cwd": ".", "env": {}}
    assert "(root)" in errors_of({"kind": "code", "transport": "http"})
    assert "subprocess.command" in errors_of(
        {"kind": "code", "transport": "subprocess", "subprocess": {"command": []}}
    )
    # 未使用的 transport 配置被丢弃，避免影响哈希
    both = parse_spec(
        {
            "kind": "code",
            "transport": "http",
            "http": {"base_url": "http://x"},
            "subprocess": {"command": ["a"]},
        }
    )
    assert dump_spec(both)["subprocess"] is None


def test_json_schema_export():
    schema = spec_json_schema()
    text = str(schema)
    assert "oneOf" in schema or "anyOf" in schema
    assert "evalp/v1" in text and "knowledge_search" in text
    props = {}
    for d in schema["$defs"].values():
        props.update(d.get("properties", {}))
    assert "schema" in props and "with" in props  # 使用别名
