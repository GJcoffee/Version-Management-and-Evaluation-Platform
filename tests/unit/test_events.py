"""事件协议、步骤签名、脱敏。"""

import pytest
from evalp.errors import ValidationFailed
from evalp.runtime.events import (
    ALL_TYPES,
    PERSISTED_TYPES,
    AgentEvent,
    ev,
    is_sensitive_key,
    redact,
    step_signature,
)
from evalp.runtime.messages import Message, ToolCall


def test_persisted_types_exclude_only_delta():
    assert "llm.delta" not in PERSISTED_TYPES
    assert PERSISTED_TYPES == ALL_TYPES - {"llm.delta"}
    assert len(ALL_TYPES) == 12


def test_ev_turn_start_serializes_message():
    e = ev("turn.start", input=Message.user("你好"), history_len=2)
    assert isinstance(e, AgentEvent)
    assert e.seq is None and e.turn_id is None
    assert e.payload == {"input": {"role": "user", "content": "你好"}, "history_len": 2}
    assert e.ts.endswith("Z") and e.persisted


def test_ev_outer_fields_and_copies():
    e = ev("tool.call", step_id="s2", parent_step_id="s1", call_id="c1", name="q", arguments={"a": 1})
    assert (e.step_id, e.parent_step_id) == ("s2", "s1")
    e2 = e.with_turn("trn_1").with_seq(7)
    assert (e2.turn_id, e2.seq) == ("trn_1", 7) and e.seq is None
    wire = e2.to_wire()
    assert set(wire) == {"seq", "turn_id", "ts", "type", "step_id", "parent_step_id", "payload"}


def test_llm_delta_has_no_seq_and_carries_step_id():
    e = ev("llm.delta", step_id="s1", text="你")
    assert e.seq is None and not e.persisted
    assert e.payload == {"step_id": "s1", "text": "你"}
    d = ev("llm.delta", step_id="s1", tool_call_delta={"index": 0, "arguments": '{"a'})
    assert d.payload == {"step_id": "s1", "tool_call_delta": {"index": 0, "arguments": '{"a'}}


def test_ev_all_types_roundtrip():
    msg = Message(role="assistant", content="hi")
    events = [
        ev("llm.request", step_id="s1", provider="p", model="m", params={"t": 0}, messages=[msg], tools=None),
        ev(
            "llm.response",
            step_id="s1",
            content="hi",
            tool_calls=[ToolCall(id="c", name="n", arguments={"a": 1})],
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 2},
            latency_ms=5,
            ttft_ms=2,
            cost=0.1,
        ),
        ev("tool.result", step_id="s3", call_id="c", name="n", output={"x": 1}, is_error=False, latency_ms=1),
        ev("span.start", step_id="s4", name="retrieve", attrs={"q": "x"}),
        ev("span.end", step_id="s4", name="retrieve", attrs={"n": 2}, latency_ms=3),
        ev("log", level="warn", message="m"),
        ev("message.assistant", message=msg),
        ev("error", code="TIMEOUT", message="超时", retriable=True),
        ev("turn.end", status="completed", output={"final": {}}, usage={}, cost=0.0, latency_ms=1, ttft_ms=1),
    ]
    assert {e.type for e in events} | {"turn.start", "tool.call", "llm.delta"} == set(ALL_TYPES)
    resp = events[1].payload
    assert resp["tool_calls"] == [{"id": "c", "name": "n", "arguments": {"a": 1}}]
    assert resp["usage"]["estimated"] is False
    assert events[2].payload["mocked"] is False
    assert events[0].payload["messages"] == [{"role": "assistant", "content": "hi"}]
    for e in events:  # 能被 AgentEvent 重新解析
        assert AgentEvent.model_validate(e.to_wire()) == e


def test_ev_keeps_extra_payload_fields():
    e = ev("log", message="x", custom={"k": 1})
    assert e.payload["custom"] == {"k": 1}


def test_ev_validation_errors():
    with pytest.raises(ValidationFailed):
        ev("tool.call", name="x")  # 缺 call_id
    with pytest.raises(ValidationFailed):
        ev("turn.end", status="bogus")
    with pytest.raises(ValidationFailed):
        ev("nope")  # type: ignore[arg-type]


def test_step_signature():
    assert step_signature(ev("llm.response", content="x")) == "llm"
    assert step_signature(ev("tool.call", call_id="c", name="query", arguments={})) == "tool:query"
    assert step_signature(ev("span.start", name="retrieve")) == "span:retrieve"
    assert step_signature(ev("message.assistant", message=Message(role="assistant", content="x"))) == "final"
    for e in (
        ev("turn.start", input=Message.user("x")),
        ev("llm.request", model="m"),
        ev("llm.delta", step_id="s", text="a"),
        ev("tool.result", call_id="c", name="q"),
        ev("span.end", name="r"),
        ev("log", message="x"),
        ev("error", code="X", message="m"),
        ev("turn.end", status="completed"),
    ):
        assert step_signature(e) is None
    # 数据库行（dict）同样适用
    assert step_signature({"type": "tool.call", "payload": {"name": "q"}}) == "tool:q"


@pytest.mark.parametrize(
    "key",
    ["Authorization", "authorization", "api_key", "API-KEY", "apiKey".lower(), "X-Api-Token", "x-auth-token",
     "access_token", "Proxy-Authorization", "password", "client_secret", "Cookie", "token"],
)  # fmt: skip
def test_sensitive_keys(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["max_tokens", "prompt_tokens", "completion_tokens", "tokens", "name", "model", "tokenizer"]
)
def test_non_sensitive_keys(key):
    assert not is_sensitive_key(key)


def test_redact_headers_and_nested():
    obj = {
        "headers": {"Authorization": "Bearer sk-abcdefghijklmnop", "X-Org-Token": "t", "Accept": "json"},
        "params": {"api_key": "k", "max_tokens": 100, "temperature": 0},
        "usage": {"prompt_tokens": 5},
        "list": [{"password": "p", "ok": 1}, "plain"],
        "empty": {"token": None},
    }
    out = redact(obj)
    assert out["headers"] == {"Authorization": "***", "X-Org-Token": "***", "Accept": "json"}
    assert out["params"] == {"api_key": "***", "max_tokens": 100, "temperature": 0}
    assert out["usage"] == {"prompt_tokens": 5}
    assert out["list"] == [{"password": "***", "ok": 1}, "plain"]
    assert out["empty"] == {"token": None}
    assert obj["headers"]["Authorization"].startswith("Bearer")  # 不修改入参


def test_redact_strings_and_models():
    assert redact("Authorization failed for Bearer abcdef123456789") == "Authorization failed for Bearer ***"
    assert redact("key=sk-ABCDEFGHIJKLMNOPQRST!") == "key=***!"
    assert redact("普通文本 token 不受影响") == "普通文本 token 不受影响"
    assert redact(Message.user("hi")) == {"role": "user", "content": "hi", "tool_calls": None,
                                           "tool_call_id": None, "name": None}  # fmt: skip
    assert redact(("a", {"api_key": "x"})) == ["a", {"api_key": "***"}]
