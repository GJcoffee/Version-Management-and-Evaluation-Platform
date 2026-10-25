"""评估器：配置校验、哈希、规则类 / trace 评估器的判定（不依赖数据库）。"""

import json

import pytest
from evalp.errors import ValidationFailed
from evalp.evaluators import (
    EVALUATOR_TYPES,
    build_evaluators,
    config_to_dict,
    evaluator_hash,
    parse_evaluators,
)
from evalp.evaluators.base import CaseView, Transcript, TurnView
from evalp.evaluators.llm_judge import JudgeParseError, parse_judge_output
from evalp.evaluators.python_eval import coerce_result
from evalp.runtime.events import AgentEvent


def case(expected=None, rubric=None, **kw):
    return CaseView(case_key="k", input={"turns": ["你好"]}, expected=expected, rubric=rubric, **kw)


def tr(final="好的", **kw):
    return Transcript([TurnView(idx=0, input="你好", output=final, **kw)])


def ev(a, b=None):
    """构造单个评估器。"""
    cfgs = [config_to_dict(c) for c in parse_evaluators([a])]
    return build_evaluators(None, cfgs)[0]  # type: ignore[arg-type]


def event(type_, **payload):
    return AgentEvent(seq=1, turn_id="t", type=type_, payload=payload)


async def run(cfg, c=None, t=None, events=None):
    return await ev(cfg).evaluate(c or case(), t or tr(), events or [])


def test_types_and_defaults():
    assert set(EVALUATOR_TYPES) == {
        "exact_match",
        "contains",
        "regex",
        "json_schema",
        "trace",
        "llm_judge",
        "python",
        "human",
    }
    cfgs = parse_evaluators(
        [
            {"type": "exact_match"},
            {"type": "exact_match", "field": "final"},
            {"type": "human"},
            {"type": "contains", "values": ["x"], "gate": False},
        ]
    )
    assert [c.key for c in cfgs] == ["exact_match_1", "exact_match_2", "human", "contains"]
    assert [c.gated for c in cfgs] == [True, True, False, False]  # human 默认不做门禁


@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ([], "evaluators"),
        ([{"type": "nope"}], "evaluators[0].type"),
        ([{"type": "regex", "pattern": "("}], "evaluators[0].pattern"),
        ([{"type": "exact_match", "field": "x"}], "evaluators[0].field"),
        ([{"type": "json_schema", "schema": {"type": "wrong"}}], "evaluators[0].schema"),
        ([{"type": "python", "entry": "nomodule"}], "evaluators[0].entry"),
        ([{"type": "human", "scale": [5, 1]}], "evaluators[0].scale"),
        ([{"type": "llm_judge", "provider": "p"}], "evaluators[0].model"),
        ([{"type": "contains", "bogus": 1}], "evaluators[0].bogus"),
        ([{"type": "trace", "assert": {"max_steps": -1}}], "evaluators[0].assert.max_steps"),
        ([{"type": "exact_match", "key": "a b"}], "evaluators[0].key"),
        ([{"type": "exact_match", "key": "a"}, {"type": "contains", "key": "a"}], "evaluators[1].key"),
    ],
)
def test_bad_config_is_validation_error_with_path(raw, path):
    with pytest.raises(ValidationFailed) as ei:
        parse_evaluators(raw)
    assert ei.value.code == "VALIDATION_ERROR"
    assert path in [e["path"] for e in ei.value.details["errors"]]


def test_evaluator_hash_ignores_key_and_gate_only():
    a, b, c = parse_evaluators(
        [
            {"type": "contains", "values": ["x"], "key": "a"},
            {"type": "contains", "values": ["x"], "key": "b", "gate": False},
            {"type": "contains", "values": ["y"], "key": "c"},
        ]
    )
    assert evaluator_hash(a) == evaluator_hash(b) != evaluator_hash(c)
    assert evaluator_hash(a) == evaluator_hash(config_to_dict(a))


async def test_exact_match():
    assert (await run({"type": "exact_match"}, case("好的"), tr("  好的\n"))).passed is True
    r = await run({"type": "exact_match"}, case("好的"), tr("不好"))
    assert r.passed is False and r.score == 0.0 and r.status == "done"
    r = await run({"type": "exact_match"}, case(None))
    assert r.status == "error"  # 缺少 expected
    r = await run(
        {"type": "exact_match", "normalize": ["lower", "punct"]}, case("Hello World"), tr("hello, world!")
    )
    assert r.passed is True
    assert (await run({"type": "exact_match"}, case(["甲", "好的"]), tr("好的"))).passed is True  # 多个候选
    r = await run(
        {"type": "exact_match", "field": r"regex:订单号：(\d+)"}, case("123"), tr("您的订单号：123 已发货")
    )
    assert r.passed is True
    r = await run({"type": "exact_match", "field": r"regex:订单号：(\d+)"}, case("123"), tr("没有"))
    assert r.passed is False


async def test_contains():
    c = {"type": "contains", "values": ["退货", "7 天"]}
    assert (await run(c, t=tr("支持7天退货"))).passed is False  # "7 天" 与 "7天" 不同
    assert (await run(c, t=tr("支持 7 天退货"))).passed is True
    r = await run(c, t=tr("只提到退货"))
    assert r.passed is False and r.score == pytest.approx(0.5)  # all 模式给部分分
    assert (await run({**c, "mode": "any"}, t=tr("只提到退货"))).passed is True
    assert (await run({"type": "contains", "values": ["抱歉"], "negate": True}, t=tr("好的"))).passed is True
    assert (
        await run({"type": "contains", "values": ["ABC"], "case_sensitive": False}, t=tr("abc"))
    ).passed is True
    assert (await run({"type": "contains"}, case("好"), tr("很好"))).passed is True  # values 缺省取 expected
    assert (await run({"type": "contains"}, case(None), tr("很好"))).status == "error"


async def test_regex_and_json_schema():
    assert (await run({"type": "regex", "pattern": r"\d{4}"}, t=tr("订单 1234"))).passed is True
    assert (await run({"type": "regex", "pattern": r"abc", "flags": ["i"]}, t=tr("ABC"))).passed is True
    assert (await run({"type": "regex", "pattern": r"^\d+$"}, t=tr("x1"))).passed is False
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
    js = {"type": "json_schema", "schema": schema}
    assert (await run(js, t=tr('{"a": 1}'))).passed is True
    assert (await run(js, t=tr('结果如下：```json\n{"a": 2}\n```'))).passed is True
    r = await run(js, t=tr('{"a": "x"}'))
    assert r.passed is False and "a" in (r.reason or "")
    assert (await run(js, t=tr("不是 json"))).passed is False
    assert (await run({**js, "extract": "codeblock"}, t=tr('{"a": 1}'))).passed is False


async def test_trace():
    t = Transcript(
        [
            TurnView(
                idx=0,
                input="q",
                output="a",
                latency_ms=500,
                usage={"prompt_tokens": 80, "completion_tokens": 20},
            )
        ]
    )
    events = [
        event("llm.response"),
        event("tool.call", name="search", call_id="1", arguments={}),
        event("tool.result", call_id="1", output="x"),
        event("llm.response"),
    ]
    ok = {
        "type": "trace",
        "assert": {
            "tool_called": "search",
            "tool_not_called": ["pay"],
            "max_steps": 2,
            "max_latency_ms": 1000,
            "max_tokens": 100,
        },
    }
    r = await run(ok, t=t, events=events)
    assert r.passed is True and r.score == 1.0
    bad = {
        "type": "trace",
        "assert": {
            "tool_called": ["search", "lookup"],
            "tool_not_called": ["search"],
            "max_steps": 1,
            "max_latency_ms": 100,
            "max_tokens": 50,
        },
    }
    r = await run(bad, t=t, events=events)
    assert r.passed is False and r.score == pytest.approx(1 / 6)
    assert len(r.detail["checks"]) == 6
    half = {"type": "trace", "assert": {"tool_called": ["search"], "tool_not_called": ["search"]}}
    r = await run(half, t=t, events=events)
    assert r.passed is False and r.score == 0.5


async def test_human_is_pending():
    r = await run({"type": "human", "scale": [1, 5]})
    assert r.status == "pending_human" and r.score is None and r.passed is None


def test_parse_judge_output():
    assert parse_judge_output('{"score": 4, "reason": "不错"}', 1, 5) == (4.0, "不错")
    assert parse_judge_output('说明：\n```json\n{"score": "3.5"}\n```', 1, 5) == (3.5, "")
    for bad in ["没有 json", '{"reason": "x"}', '{"score": "好"}', '{"score": 9}', '{"score": true}', ""]:
        with pytest.raises(JudgeParseError):
            parse_judge_output(bad, 1, 5)


def test_python_result_coercion():
    assert coerce_result(True).passed is True and coerce_result(False).score == 0.0
    r = coerce_result({"score": 0.8, "reason": "ok"})
    assert r.passed is True and r.score == 0.8 and r.reason == "ok"
    assert coerce_result({"score": 0.2}).passed is False
    assert coerce_result({"passed": True}).score == 1.0
    for bad in [{"score": 2}, {"score": "x"}, {"passed": "yes"}, {}, [1], None]:
        assert coerce_result(bad).status == "error"


def test_transcript_helpers():
    t = Transcript(
        [
            TurnView(0, "a", "b", usage={"prompt_tokens": 3, "completion_tokens": 2}, latency_ms=10),
            TurnView(
                1,
                "c",
                "d",
                messages=[
                    {"role": "assistant", "tool_calls": [{"id": "1", "name": "t", "arguments": {"x": 1}}]},
                    {"role": "tool", "tool_call_id": "1", "content": "res"},
                ],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                latency_ms=5,
            ),
        ]
    )
    assert t.final == "d" and t.input_text == "a\nc" and t.tokens == 7 and t.latency_ms == 15
    assert t.tool_calls() == [{"name": "t", "arguments": {"x": 1}, "output": "res"}]
    json.dumps(t.to_dict())
