"""各类资产内容的校验（详设 5.1）。"""

import json

import pytest
from evalp.errors import DatasetParseError, ValidationFailed
from evalp.services.asset_content import (
    MAX_SIZE,
    default_content_type,
    normalize_content_type,
    parse_tool_content,
    validate_content,
)

TOOL = """\
name: query_order
description: 查询订单
parameters:
  type: object
  properties:
    order_id: {type: string}
  required: [order_id]
impl:
  type: http
  method: POST
  url: http://127.0.0.1:9000/orders/query
  headers: {X-Token: "${env:ORDER_TOKEN}"}
  body_template: '{"id": "{{ args.order_id }}"}'
  timeout_s: 15
"""


def b(s: str) -> bytes:
    return s.encode("utf-8")


def test_prompt_lists_used_variables():
    v = validate_content(
        "prompt",
        b("你好 {{ variables.company }}，现在 {{ now }}。{% for k in knowledge %}{{ k }}{% endfor %}"),
    )
    assert v.content_type == "text/markdown"
    assert v.meta["variables"] == ["knowledge", "now", "variables"]


def test_prompt_plain_text_and_syntax_error():
    assert validate_content("prompt", b("纯文本，没有变量")).meta == {"variables": []}
    with pytest.raises(ValidationFailed) as ei:
        validate_content("prompt", b("第一行\n{% if x %}没有结束"))
    err = ei.value.details["errors"][0]
    assert err["path"] == "content" and "模板语法错误" in err["message"]
    with pytest.raises(ValidationFailed):
        validate_content("prompt", b"\xff\xfe")


def test_tool_yaml_and_json_accepted():
    v = validate_content("tool", b(TOOL))
    assert v.content_type == "application/x-yaml"
    assert v.meta == {"tool_name": "query_order", "impl_type": "http"}
    as_json = json.dumps(
        {
            "name": "t",
            "parameters": {"type": "object", "properties": {}},
            "impl": {"type": "mock", "responses": []},
        }
    )
    v2 = validate_content("tool", b(as_json))
    assert v2.content_type == "application/json" and v2.meta["impl_type"] == "mock"


def test_tool_python_and_mock_impl():
    py = "name: f\nimpl: {type: python, entry: 'my_tools.orders:query', workdir: /tmp}\n"
    assert parse_tool_content(py).impl.type == "python"
    mock = (
        "name: m\nimpl:\n  type: mock\n  responses:\n"
        "    - match: {a: 1}\n      output: {s: ok}\n    - default: {s: none}\n"
    )
    assert len(parse_tool_content(mock).impl.responses) == 2


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("name: x\nimpl: {type: ftp}\n", "impl"),  # 非法 impl 类型
        ("name: x\nimpl: {type: python, entry: nocolon}\n", "impl.entry"),
        ("description: 缺名字\nimpl: {type: mock}\n", "name"),
        ("name: 'bad name!'\nimpl: {type: mock}\n", "name"),
        ("name: x\nparameters: {type: array}\nimpl: {type: mock}\n", "parameters"),
        (
            "name: x\nparameters: {type: object, properties: {a: {type: 12}}}\nimpl: {type: mock}\n",
            "parameters",
        ),
        ("name: x\nimpl: {type: http, url: http://x, bogus: 1}\n", "impl.bogus"),
        ("- just\n- a list\n", "content"),
        ("name: [unclosed\n", "content"),
    ],
)
def test_tool_invalid(text, path):
    with pytest.raises(ValidationFailed) as ei:
        validate_content("tool", b(text))
    assert path in {e["path"] for e in ei.value.details["errors"]}, ei.value.details


def test_knowledge_markdown_and_jsonl():
    assert validate_content("knowledge", b("# FAQ\n内容")).meta == {"format": "text"}
    jl = '{"id": "1", "text": "退货政策", "meta": {}}\n{"text": "发货时间"}\n'
    v = validate_content("knowledge", b(jl), "application/jsonl")
    assert v.meta == {"format": "jsonl", "doc_count": 2}
    with pytest.raises(ValidationFailed) as ei:
        validate_content("knowledge", b('{"text": "ok"}\n{"id": 1}\nnot json\n'), "application/jsonl")
    assert ei.value.details["lines"] == [2, 3]


def test_size_limits():
    assert MAX_SIZE["knowledge"] == 20 * 1024 * 1024 and MAX_SIZE["file"] == 100 * 1024 * 1024
    with pytest.raises(ValidationFailed) as ei:
        validate_content("knowledge", b"x" * (20 * 1024 * 1024 + 1))
    assert ei.value.details["limit"] == 20 * 1024 * 1024
    assert validate_content("knowledge", b"x" * (20 * 1024 * 1024)).meta == {"format": "text"}


def test_file_accepts_any_bytes():
    v = validate_content("file", bytes(range(256)))
    assert v.content_type == "application/octet-stream" and v.meta == {}
    assert validate_content("file", b"a,b", "text/csv").content_type == "text/csv"


def test_dataset_jsonl_meta():
    v = validate_content("dataset", b('{"input": "x"}\n{"case_key": "k", "input": "y"}\n'))
    assert v.content_type == "application/jsonl"
    assert v.meta["case_count"] == 2 and v.meta["format"] == "jsonl"
    assert [w["code"] for w in v.meta["schema_warnings"]] == ["MISSING_CASE_KEY"]
    assert len(v.cases) == 2


def test_dataset_csv_by_content_type():
    v = validate_content(
        "dataset", b("问题\n你好\n"), "text/csv; charset=utf-8", column_map={"input": "问题"}
    )
    assert v.content_type == "text/csv" and v.meta["format"] == "csv"
    assert v.meta["column_map"] == {"input": "问题"}
    with pytest.raises(DatasetParseError):
        validate_content("dataset", b("问题\n你好\n"), "text/csv")


def test_content_type_helpers():
    assert normalize_content_type("Text/CSV; charset=utf-8") == "text/csv"
    assert normalize_content_type("") is None and normalize_content_type(None) is None
    assert default_content_type("tool", b'{"a":1}') == "application/json"
    assert default_content_type("tool", b"a: 1") == "application/x-yaml"


@pytest.mark.parametrize("kind", ["kb_config", "skill", "mcp", "app_config", "database"])
def test_text_config_kinds_store_utf8_text_with_sniffed_content_type(kind):
    assert validate_content(kind, b('{"名称": "x"}')).content_type == "application/json"
    assert validate_content(kind, b("# 说明\n正文")).content_type == "text/markdown"
    assert validate_content(kind, b("a: 1"), "application/x-yaml").content_type == "application/x-yaml"
    with pytest.raises(ValidationFailed):
        validate_content(kind, b"\xff\xfe")
    with pytest.raises(ValidationFailed):
        validate_content(kind, b"x" * (MAX_SIZE[kind] + 1))
