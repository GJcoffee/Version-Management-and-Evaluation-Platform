"""只查询当前消息的原生 Trace；指标缺失不改写执行结果。"""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from evalp.runtime.adapters.hiagent_adapter import HiAgentMapper, decoded
from evalp.runtime.adapters.registry import HiAgentConfig
from evalp.runtime.events import AgentEvent, ev, redact


def duration(start: str, end: str) -> int:
    return round((datetime.fromisoformat(end.replace("Z", "+00:00")) -
                  datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000)


def trace_events(
    config: HiAgentConfig, mapper: HiAgentMapper, trace: dict, page: dict,
) -> list[AgentEvent]:
    """先核验完整页及所有身份，再补齐，拒绝跨消息和按文字猜测关联。"""
    tid = trace.get("TraceID")
    if (not tid or not trace.get("EndTime") or trace.get("Status", {}).get("Code") != "OK"
            or page.get("HasMore") is not False or page.get("Total") != len(page.get("Items", []))):
        raise ValueError("Trace 尚未完整")
    spans = page["Items"]
    seen: set[str] = set()
    for span in spans:
        context, attrs = span.get("Context", {}), span.get("Attributes", {})
        if context.get("TraceID") != tid:
            raise ValueError("Trace 过滤越界")
        if attrs.get("gen_ai.span_type") not in ("llm", "tool"):
            continue
        sid = context.get("SpanID")
        if (not sid or sid in seen or attrs.get("message_id") != mapper.message_id
                or attrs.get("app_id") != config.app_id
                or str(attrs.get("gen_ai.agent.version")) != config.published_version
                or not span.get("StartTime") or not span.get("EndTime")):
            raise ValueError("Trace 调用身份不符")
        seen.add(sid)
    if not any(s.get("Attributes", {}).get("gen_ai.span_type") == "llm" for s in spans):
        raise ValueError("缺少模型调用")
    events, llm_ms, tool_ms = [], 0, 0
    model_steps = {
        s["Context"]["SpanID"]: f"llm:{s['Context']['SpanID']}"
        for s in spans if s.get("Attributes", {}).get("gen_ai.span_type") == "llm"
    }
    token_complete = True
    for span in spans:
        attrs, context = span.get("Attributes", {}), span.get("Context", {})
        kind = attrs.get("gen_ai.span_type")
        if kind not in ("llm", "tool"):
            continue
        sid = context["SpanID"]
        ms = duration(span["StartTime"], span["EndTime"])
        if ms < 0:
            raise ValueError("Trace 时间无效")
        upstream = {"source": "actual_completed_upstream_trace", "trace_id": tid, "span_id": sid,
                    "message_id": mapper.message_id, "start_time": span["StartTime"],
                    "end_time": span["EndTime"]}
        if kind == "llm":
            llm_ms += ms
            stage_id = attrs.get("think_message_id")
            stage = mapper.stages.get(str(stage_id)) if stage_id else None
            step = model_steps[sid]
            output = decoded(attrs.get("output"))
            content = output.get("content", output.get("text")) if isinstance(output, dict) else output
            generations = output.get("generations") if isinstance(output, dict) else None
            if generations and generations[0]:
                content = generations[0][0].get("text", content)
            usage = {"prompt_tokens": attrs.get("input_tokens"),
                     "completion_tokens": attrs.get("output_tokens"), "source": "hiagent_trace"}
            token_complete &= all(isinstance(usage[k], int) for k in ("prompt_tokens", "completion_tokens"))
            events.append(ev("llm.request", step_id=step, ts=span["StartTime"],
                             model=span.get("Name", "HiAgent"), source="hiagent_trace"))
            if stage:
                events.append(ev("llm.reasoning", step_id=step, text="".join(stage["reasoning"]),
                                 source="hiagent_trace", replaces_stage=stage["step"]))
            events.append(ev("llm.response", step_id=step, ts=span["EndTime"],
                             content=content if isinstance(content, str) else None,
                             usage=usage, latency_ms=ms, upstream=upstream, source="hiagent_trace"))
        else:
            tool_ms += ms
            # 只使用确定的上游调用 ID；同名同参数不能证明是同一次调用。
            cid = attrs.get("call_id") or attrs.get("tool_call_id")
            call = mapper.calls.get(str(cid)) if cid else None
            if call:
                parent = model_steps.get(span.get("ParentSpanID") or context.get("ParentSpanID"))
                events.append(ev("tool.result", step_id=call["step"], parent_step_id=parent,
                                 call_id=str(cid), name=call["name"], ts=span["EndTime"],
                                 output=redact(decoded(attrs.get("output"))),
                                 is_error=span.get("Status", {}).get("Code") not in ("OK", "UNSET", None),
                                 latency_ms=ms, source="hiagent_trace", upstream=upstream))
    # 明细收齐才用明细汇总；否则继续保留 SSE 整轮用量。
    if token_complete:
        responses = [e.payload["usage"] for e in events if e.type == "llm.response"]
        totals = {k: sum(u[k] for u in responses) for k in ("prompt_tokens", "completion_tokens")}
        for key, value in totals.items():
            if mapper.usage.get(key) is not None and int(mapper.usage[key]) != value:
                raise ValueError("Trace 明细用量与整轮用量不一致")
        mapper.usage.update({**totals, "source": "hiagent_trace", "observation_status": "complete"})
    events.append(ev("metrics", scope="turn", source="hiagent_trace", usage=mapper.usage,
                     upstream_run_ms=duration(trace["StartTime"], trace["EndTime"]),
                     llm_api_ms=llm_ms, tool_execution_ms=tool_ms,
                     observation_status="complete" if token_complete else "partial"))
    return events


async def collect_trace(
    config: HiAgentConfig, secret: dict[str, Any], mapper: HiAgentMapper,
) -> AsyncIterator[AgentEvent]:
    """在有限观测窗口查询；网络与身份失败只影响观测完整性。"""
    if not mapper.message_id:
        return
    headers = secret.get("observe_headers")
    if not isinstance(headers, dict):
        yield ev("log", level="warning", message="Trace 凭据未提供，指标部分缺失")
        return
    deadline = time.monotonic() + config.observation_wait_s
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        async def query(action: str, body: dict) -> dict:
            response = await client.post(
                config.observe_base_url + "/api/observe",
                params={"Action": action, "Version": "2025-05-01"}, json=body,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("ResponseMetadata", {}).get("Error"):
                raise ValueError("Trace 查询失败")
            return result.get("Result", result)

        while True:
            try:
                async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                    trace = await query("GetTraceInfo", {"MessageID": mapper.message_id})
                    page = await query("ListTraceSpans", {
                        "IsCustomApp": False, "PageSize": 1000,
                        "Sort": [{"SortBy": "StartTime", "SortOrder": "Asc"}],
                        "SimpleFilters": [{"TraceID": {"Operator": "Equal", "Value": trace["TraceID"]}}],
                    })
                    events = trace_events(config, mapper, trace, page)
                yield ev("source.raw", source="hiagent_trace", trace=redact(trace), page=redact(page))
                for event in events:
                    yield event
                return
            except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
                if time.monotonic() >= deadline:
                    yield ev("log", level="warning", message="Trace 未收齐或身份不符，保留执行结果和已有指标")
                    return
                await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))
