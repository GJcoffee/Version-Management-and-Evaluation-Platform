"""数据集解析（详设 5.3、8.4）。"""

import json

import pytest
from evalp.errors import DatasetParseError
from evalp.services.dataset_parse import (
    auto_case_key,
    compute_case_hash,
    parse_csv,
    parse_dataset,
    parse_jsonl,
)
from evalp.utils.canonical import canonical_json, sha256_hex


def jl(*rows) -> str:
    return "\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows)


def test_full_jsonl_record():
    row = {
        "case_key": "refund-001",
        "input": "我要退货，订单 A001",
        "expected": "应说明退货流程并查询订单状态",
        "rubric": "提到 7 天无理由；调用了 query_order",
        "tags": ["refund", "p0"],
        "metadata": {
            "variables": {"company": "测试公司"},
            "tool_mocks": {"query_order": [{"match": {"order_id": "A001"}, "output": {"status": "已签收"}}]},
        },
    }
    res = parse_jsonl(jl(row))
    c = res.cases[0]
    assert (c.case_key, c.line) == ("refund-001", 1)
    assert c.input == {"turns": ["我要退货，订单 A001"]}
    assert c.expected == row["expected"] and c.rubric == row["rubric"]
    assert c.tags == ["refund", "p0"]
    assert c.metadata == row["metadata"]
    assert res.warnings == []


def test_input_forms_normalized_to_turns():
    res = parse_jsonl(
        jl(
            {"case_key": "a", "input": "单轮"},
            {"case_key": "b", "input": ["第一句", "第二句"]},
            {"case_key": "c", "input": {"turns": ["x", "y"]}},
            {"case_key": "d", "input": [{"role": "user", "content": "消息对象"}]},
        )
    )
    assert [c.input for c in res.cases] == [
        {"turns": ["单轮"]},
        {"turns": ["第一句", "第二句"]},
        {"turns": ["x", "y"]},
        {"turns": ["消息对象"]},
    ]


def test_missing_case_key_generated_with_warning():
    res = parse_jsonl(jl({"input": "你好"}, {"case_key": "k", "input": "x"}, {"input": ["多", "轮"]}))
    assert res.cases[0].case_key == sha256_hex("你好")[:12]
    assert res.cases[2].case_key == sha256_hex(canonical_json(["多", "轮"]))[:12]
    assert auto_case_key(["你好"]) == res.cases[0].case_key
    (w,) = res.warnings
    assert w["code"] == "MISSING_CASE_KEY" and w["count"] == 2 and w["lines"] == [1, 3]
    assert "跨修订" in w["message"]


def test_case_hash_definition():
    """case_hash = sha256(canonical({"input","variables","tool_mocks"}))（详设 8.4）。"""
    mocks = {"t": [{"match": {"a": 1}, "output": "x"}]}
    row = {"case_key": "k", "input": "q", "metadata": {"variables": {"v": 1}, "tool_mocks": mocks}}
    c = parse_jsonl(jl(row)).cases[0]
    expected = sha256_hex(
        canonical_json({"input": {"turns": ["q"]}, "variables": {"v": 1}, "tool_mocks": mocks})
    )
    assert c.case_hash == expected == compute_case_hash({"turns": ["q"]}, {"v": 1}, mocks)


def test_case_hash_ignores_expected_rubric_tags_and_key():
    a = parse_jsonl(
        jl({"case_key": "a", "input": "q", "expected": "e1", "rubric": "r1", "tags": ["x"]})
    ).cases[0]
    b = parse_jsonl(
        jl({"case_key": "b", "input": "q", "expected": "e2", "rubric": "r2", "tags": ["y"]})
    ).cases[0]
    assert a.case_hash == b.case_hash
    c = parse_jsonl(jl({"case_key": "a", "input": "q2"})).cases[0]
    d = parse_jsonl(jl({"case_key": "a", "input": "q", "metadata": {"variables": {"v": 1}}})).cases[0]
    assert len({a.case_hash, c.case_hash, d.case_hash}) == 3


def test_case_hash_missing_equals_empty_variables():
    a = parse_jsonl(jl({"case_key": "a", "input": "q"})).cases[0]
    b = parse_jsonl(
        jl({"case_key": "a", "input": "q", "metadata": {"variables": {}, "tool_mocks": {}}})
    ).cases[0]
    assert a.case_hash == b.case_hash


def test_blank_lines_and_bom_ignored():
    res = parse_jsonl("﻿" + jl({"case_key": "a", "input": "x"}, "", "   ", {"case_key": "b", "input": "y"}))
    assert [c.line for c in res.cases] == [1, 4]


def test_unknown_fields_go_to_metadata_with_warning():
    res = parse_jsonl(jl({"case_key": "a", "input": "x", "category": "退货", "variables": {"v": 1}}))
    c = res.cases[0]
    assert c.metadata == {"category": "退货", "variables": {"v": 1}}
    assert [w["code"] for w in res.warnings] == ["UNKNOWN_FIELD"]  # variables 不算未知字段


def test_error_lines_reported():
    text = jl(
        {"case_key": "ok", "input": "x"},
        "{bad json",
        {"case_key": "no-input"},
        {"case_key": "bad-tags", "input": "x", "tags": [1]},
        [1, 2],
        {"case_key": "empty-turn", "input": ["a", " "]},
        {"case_key": "ok2", "input": "x"},
    )
    with pytest.raises(DatasetParseError) as ei:
        parse_jsonl(text)
    e = ei.value
    assert e.code == "DATASET_PARSE_ERROR" and e.status == 422
    assert e.details["lines"] == [2, 3, 4, 5, 6]
    assert e.details["total_errors"] == 5
    assert {x["line"] for x in e.details["errors"]} == {2, 3, 4, 5, 6}
    assert all(x["message"] for x in e.details["errors"])


def test_duplicate_case_key_reports_later_line():
    with pytest.raises(DatasetParseError) as ei:
        parse_jsonl(
            jl(
                {"case_key": "a", "input": "x"},
                {"case_key": "b", "input": "y"},
                {"case_key": "a", "input": "z"},
            )
        )
    assert ei.value.details["lines"] == [3]
    assert "第 1 行" in ei.value.details["errors"][0]["message"]


def test_duplicate_generated_keys_are_errors():
    with pytest.raises(DatasetParseError) as ei:
        parse_jsonl(jl({"input": "same"}, {"input": "same"}))
    assert ei.value.details["lines"] == [2]


def test_empty_dataset_and_bad_encoding():
    with pytest.raises(DatasetParseError):
        parse_jsonl("\n\n")
    with pytest.raises(DatasetParseError):
        parse_jsonl(b"\xff\xfe\x00bad")


def test_error_report_is_capped():
    text = "\n".join("{bad" for _ in range(300))
    with pytest.raises(DatasetParseError) as ei:
        parse_jsonl(text)
    assert ei.value.details["total_errors"] == 300
    assert len(ei.value.details["lines"]) == 100 and len(ei.value.details["errors"]) == 100


# ---------------------------------------------------------------- CSV

CSV = """问题,参考答案,标签(;分隔),key,备注
我要退货,说明退货流程,refund;p0,r-1,x
"多行
问题",答案2,,r-2,y

查询订单,,order,r-3,z
"""


def test_csv_with_column_map():
    cmap = {"input": "问题", "expected": "参考答案", "tags": "标签(;分隔)", "case_key": "key"}
    res = parse_csv(CSV, cmap)
    assert [c.case_key for c in res.cases] == ["r-1", "r-2", "r-3"]
    assert res.cases[0].input == {"turns": ["我要退货"]}
    assert res.cases[0].expected == "说明退货流程" and res.cases[0].tags == ["refund", "p0"]
    assert res.cases[1].input == {"turns": ["多行\n问题"]} and res.cases[1].tags == []
    assert res.cases[2].expected is None  # 空单元格 -> None
    assert [c.line for c in res.cases] == [2, 3, 6]  # 多行记录从起始行计，空行跳过
    assert res.warnings == []


def test_csv_missing_case_key_warns():
    res = parse_csv(CSV, {"input": "问题"})
    assert len(res.cases) == 3
    assert [w["code"] for w in res.warnings] == ["MISSING_CASE_KEY"]
    assert res.warnings[0]["count"] == 3


def test_csv_default_map_uses_same_named_columns():
    res = parse_csv("case_key,input,expected,tags\na,hello,world,x;y\n")
    c = res.cases[0]
    assert (c.case_key, c.expected, c.tags) == ("a", "world", ["x", "y"])


def test_csv_multi_turn_columns_and_json_cell():
    res = parse_csv(
        't1,t2,t3,k\nhi,again,,a\n"[""x"",""y""]",,,b\n', {"input": ["t1", "t2", "t3"], "case_key": "k"}
    )
    assert res.cases[0].input == {"turns": ["hi", "again"]}
    res2 = parse_csv('q,k\n"[""x"",""y""]",a\nplain,b\n', {"input": "q", "case_key": "k"})
    assert res2.cases[0].input == {"turns": ["x", "y"]} and res2.cases[1].input == {"turns": ["plain"]}


def test_csv_json_columns_for_metadata():
    csv_text = 'q,vars,mocks\nhello,"{""company"": ""A""}","{""t"": []}"\n'
    c = parse_csv(csv_text, {"input": "q", "variables": "vars", "tool_mocks": "mocks"}).cases[0]
    assert c.metadata == {"variables": {"company": "A"}, "tool_mocks": {"t": []}}
    with pytest.raises(DatasetParseError) as ei:
        parse_csv("q,vars\nhello,{bad\n", {"input": "q", "variables": "vars"})
    assert ei.value.details["lines"] == [2]


def test_csv_errors():
    with pytest.raises(DatasetParseError) as ei:
        parse_csv("a,b\n1,2\n", {"input": "不存在的列"})
    assert ei.value.details["lines"] == [1]
    with pytest.raises(DatasetParseError):
        parse_csv("a,b\n1,2\n")  # 没有 input 列，也没给 column_map
    with pytest.raises(DatasetParseError):
        parse_csv("a,b\n1,2\n", {"input": "a", "bogus": "b"})
    with pytest.raises(DatasetParseError):
        parse_csv("")
    with pytest.raises(DatasetParseError) as ei:
        parse_csv("q,k\nok,a\n ,b\n", {"input": "q", "case_key": "k"})  # 有 key 但输入为空白
    assert ei.value.details["lines"] == [3]


def test_parse_dataset_dispatch():
    assert parse_dataset(jl({"case_key": "a", "input": "x"}), "jsonl").cases[0].case_key == "a"
    assert parse_dataset("q\nhello\n", "csv", {"input": "q"}).cases[0].input == {"turns": ["hello"]}
