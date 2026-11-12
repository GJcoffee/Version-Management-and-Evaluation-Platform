"""HiAgent 契约样本：真实形状的 SSE + Trace 必须归一化成同一套规范事件。

改适配器实现必须同时通过这里；fixture 只含脱敏后的协议片段，不访问真实服务。
"""

import json
from pathlib import Path

from evalp.runtime.adapters.normalize import assert_canonical, canonical_summary
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.mapper import HiAgentMapper
from evalp.runtime.adapters.platforms.hiagent.trace import trace_events

FIXTURES = Path(__file__).parent / "hiagent"


def load():
    frames = [
        json.loads(line)
        for line in (FIXTURES / "stream.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sample = json.loads((FIXTURES / "trace.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    return frames, sample, expected


def test_hiagent_stream_and_trace_contract():
    frames, sample, expected = load()
    config = HiAgentConfig(
        base_url="https://hiagent.example", app_id="app-test", user_id="u", published_version="47",
    )
    mapper = HiAgentMapper(config)
    events = [event for frame in frames for event in mapper.feed(frame)]
    page = {"Items": sample["spans"], "Total": sample["total"], "HasMore": sample["has_more"]}
    events += trace_events(config, mapper, sample["trace"], page)

    assert_canonical(events)
    identity = next(event for event in events if event.type == "metrics")
    assert identity.payload["upstream_message_id"] == mapper.message_id
    assert canonical_summary(events) == expected
