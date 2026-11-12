"""只查询当前消息的原生 Trace；指标缺失不改写执行结果。

Trace 由 HiAgent 异步生成：会话服务在本轮结束后于后台调用 :func:`collect_trace`。每次轮询先查
GetTraceInfo，只有拿到 TraceID 且 EndTime 存在（Trace 已完整）才拉 ListTraceSpans 明细，否则等待重试。

身份核验只要求 TraceID 与 message_id 一致；应用、版本写法不同只标注差异。单个 span 不合格只跳过
该 span 并记录原因，不丢弃整份 Trace。任何失败都只影响观测完整度，不影响本轮成败。
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.mapper import HiAgentMapper
from evalp.runtime.adapters.platforms.hiagent.sse import decoded
from evalp.runtime.credentials import CredentialsManager
from evalp.runtime.events import AgentEvent, ev, redact
from evalp.runtime.hiagent_auth import AccountAuthError


class TraceNotReady(Exception):
    """Trace 尚未生成或尚未完整；稍后再查可能成功。"""


def duration(start: Any, end: Any) -> int | None:
    """毫秒；缺失、无法解析或为负时返回 None。"""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        ms = round((datetime.fromisoformat(end.replace("Z", "+00:00")) -
                    datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000)
    except ValueError:
        return None
    return ms if ms >= 0 else None


def token_count(value: Any) -> int | None:
    """Observe 属性可返回数字字符串；缺失、布尔值和非法用量仍保持未知。"""
    if isinstance(value, str):
        text = value.strip()
        if not text.isascii() or not text.isdecimal():
            return None
        value = int(text)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def input_messages(attrs: dict) -> list[dict] | None:
    """span 的模型输入：消息数组原样保留，纯文本和其他结构作为一条 user 消息。"""
    value = next((v for v in (decoded(attrs.get(k)) for k in ("input", "prompt", "messages"))
                  if v not in (None, "", [], {})), None)
    if value is None:
        return None
    if isinstance(value, dict) and "messages" in value:
        value = decoded(value["messages"])
    if isinstance(value, list) and value and all(isinstance(m, dict) and "role" in m for m in value):
        return redact(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return [{"role": "user", "content": redact(text)}]


def output_text(attrs: dict) -> str | None:
    output = decoded(attrs.get("output"))
    content = output.get("content", output.get("text")) if isinstance(output, dict) else output
    generations = output.get("generations") if isinstance(output, dict) else None
    first = next((g for group in generations or [] for g in group or [] if g), None)
    if first:
        content = first.get("text", content)
    return content if isinstance(content, str) else None


TraceSpan = tuple[dict, dict, dict, str, int]


def model_spans(
    spans: list[TraceSpan], items: list[dict], tid: str,
) -> tuple[list[TraceSpan], dict[str, str]]:
    """沿真实父链去掉核心调用的 llm 包装；多个核心子调用仍分别保留。"""
    # HTTP 等中间 span 只提供父链；跨 Trace、重复 ID 或异轮消息不能作为去重依据。
    nodes: dict[str, dict] = {}
    duplicates: set[str] = set()
    for item in items:
        context = item.get("Context") or {}
        sid = context.get("SpanID")
        if not sid or context.get("TraceID") != tid:
            continue
        if sid in nodes:
            duplicates.add(sid)
        nodes[sid] = item
    models = {c["SpanID"]: kind for _, c, _, kind, _ in spans if kind in ("llm", "core_llm")}
    children: dict[str, set[str]] = {}
    ancestors: dict[str, list[str]] = {}
    for sid, kind in models.items():
        if kind != "core_llm" or sid in duplicates:
            continue
        path = {sid}
        node = nodes.get(sid)
        while node:
            parent = node.get("ParentSpanID") or (node.get("Context") or {}).get("ParentSpanID")
            if not parent or parent in path or parent in duplicates or parent not in nodes:
                break
            path.add(parent)
            node = nodes[parent]
            attrs = node.get("Attributes") or {}
            if attrs.get("gen_ai.span_type") in ("llm", "core_llm") and parent not in models:
                break  # 未通过本轮核验的模型 span 不能证明属于同一次调用
            if models.get(parent) == "llm":
                children.setdefault(parent, set()).add(sid)
                ancestors.setdefault(sid, []).append(parent)
    selected = [s for s in spans if s[1]["SpanID"] not in children]
    steps = {}
    for _, context, _, kind, _ in selected:
        if kind not in ("llm", "core_llm"):
            continue
        sid = context["SpanID"]
        # 一对一保留原包装的步骤 ID；一对多不能合并，重试各有自己的核心步骤 ID。
        wrapper = next((p for p in ancestors.get(sid, []) if len(children[p]) == 1), sid)
        steps[sid] = f"llm:{wrapper}"
    for wrapper, cores in children.items():
        if len(cores) == 1:
            steps[wrapper] = steps[next(iter(cores))]
    return selected, steps


def trace_events(
    config: HiAgentConfig, mapper: HiAgentMapper, trace: dict, page: dict,
) -> list[AgentEvent]:
    """逐 span 补齐；明细未收齐时抛 TraceNotReady，其余问题只记录在指标里。"""
    tid = trace["TraceID"]
    items = page.get("Items") or []
    if page.get("HasMore") is not False or page.get("Total") != len(items):
        raise TraceNotReady("Trace 明细尚未收齐")
    skipped: list[dict] = []
    spans: list[TraceSpan] = []
    seen: set[str] = set()
    for span in items:
        context, attrs = span.get("Context") or {}, span.get("Attributes") or {}
        kind = attrs.get("gen_ai.span_type")
        if kind not in ("llm", "core_llm", "tool"):
            continue
        sid = context.get("SpanID")
        ms = duration(span.get("StartTime"), span.get("EndTime"))
        reason = (
            "TraceID 与本轮不一致" if context.get("TraceID") != tid
            else "缺少或重复的 SpanID" if not sid or sid in seen
            else "message_id 与本轮不一致" if attrs.get("message_id") != mapper.message_id
            else "起止时间缺失或无效" if ms is None else None
        )
        if reason:
            skipped.append({"span_id": sid, "reason": reason})
            continue
        seen.add(sid)
        spans.append((span, context, attrs, kind, ms))
    spans, model_steps = model_spans(spans, items, tid)
    model_count = sum(kind in ("llm", "core_llm") for _, _, _, kind, _ in spans)
    events: list[AgentEvent] = []
    llm_ms, tool_ms = 0, 0
    token_complete = bool(model_count)
    for span, context, attrs, kind, ms in spans:
        sid = context["SpanID"]
        upstream: dict[str, Any] = {
            "source": "actual_completed_upstream_trace", "trace_id": tid, "span_id": sid,
            "message_id": mapper.message_id, "start_time": span["StartTime"], "end_time": span["EndTime"],
        }
        mismatch = {}
        if attrs.get("app_id") != config.app_id:
            mismatch["app_id"] = {"expected": config.app_id, "actual": attrs.get("app_id")}
        version = attrs.get("gen_ai.agent.version")
        if str(version) != config.published_version:
            mismatch["version"] = {"expected": config.published_version, "actual": version}
        if mismatch:
            upstream["identity_mismatch"] = mismatch
        if kind in ("llm", "core_llm"):
            llm_ms += ms
            stage_id = attrs.get("think_message_id")
            stage = mapper.stages.get(str(stage_id)) if stage_id else None
            step = model_steps[sid]
            usage = {"prompt_tokens": token_count(attrs.get("input_tokens")),
                     "completion_tokens": token_count(attrs.get("output_tokens")), "source": "hiagent_trace"}
            token_complete &= all(usage[k] is not None for k in ("prompt_tokens", "completion_tokens"))
            events.append(ev("llm.request", step_id=step, ts=span["StartTime"],
                             model=span.get("Name") or "HiAgent", messages=input_messages(attrs) or [],
                             source="hiagent_trace", upstream=upstream))
            if stage:
                events.append(ev("llm.reasoning", step_id=step, text="".join(stage["reasoning"]),
                                 source="hiagent_trace", replaces_stage=stage["step"]))
            events.append(ev("llm.response", step_id=step, ts=span["EndTime"], content=output_text(attrs),
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
                                 is_error=(span.get("Status") or {}).get("Code") not in ("OK", "UNSET", None),
                                 latency_ms=ms, source="hiagent_trace", upstream=upstream))
    notes: list[str] = []
    if not model_count:
        notes.append("Trace 中没有可用的模型调用")
    if skipped:
        notes.append(f"{len(skipped)} 个 span 未通过核验已跳过")
    # 整轮用量以 SSE 为准；明细只补空缺，不一致时保留明细并标注差异。
    mismatches: dict[str, dict] = {}
    if token_complete:
        responses = [e.payload["usage"] for e in events if e.type == "llm.response"]
        for key in ("prompt_tokens", "completion_tokens"):
            total = sum(u[key] for u in responses)
            if mapper.usage.get(key) is None:
                mapper.usage[key] = total
            elif int(mapper.usage[key]) != total:
                mismatches[key] = {"turn": int(mapper.usage[key]), "details": total}
        if mismatches:
            notes.append("明细 token 之和与整轮用量不一致，整轮以 HiAgent 返回为准")
    elif model_count:
        notes.append("部分调用缺少 token 明细")
    complete = token_complete and not mismatches and not skipped
    if complete:
        mapper.usage.update({"source": "hiagent_trace", "observation_status": "complete"})
    run_ms = duration(trace.get("StartTime"), trace["EndTime"])
    events.append(ev(
        "metrics", scope="turn", source="hiagent_trace", usage=mapper.usage,
        llm_calls=model_count, llm_api_ms=llm_ms, tool_execution_ms=tool_ms,
        observation_status="complete" if complete else "partial",
        trace_status="complete" if complete else "partial",
        **({"upstream_run_ms": run_ms} if run_ms is not None else {}),
        **({"trace_note": "；".join(notes)} if notes else {}),
        **({"trace_skipped": skipped} if skipped else {}),
        **({"token_detail_mismatch": mismatches} if mismatches else {}),
    ))
    return events


class TraceOnlyTarget:
    """没有本轮 SSE mapper 时的最小查询目标：只补模型调用与用量，不臆造工具明细。"""

    def __init__(self, message_id: str, usage: dict[str, Any] | None = None) -> None:
        self.message_id = message_id
        self.stages: dict[str, dict] = {}
        self.calls: dict[str, dict] = {}
        self.usage = dict(usage or {})


def refresh_trace(
    config: HiAgentConfig, secret: Any, message_id: str, usage: dict[str, Any] | None = None,
    *, credentials: CredentialsManager | None = None,
) -> AsyncIterator[AgentEvent]:
    """按同一 HiAgent 消息重新查询 Trace：Trace 未取得后由用户稍后重试时使用。

    ``usage`` 传当时 SSE 的整轮用量：核验口径与首次补齐一致（整轮以 SSE 为准，明细只补空缺）。
    """
    return collect_trace(config, secret, TraceOnlyTarget(message_id, usage), credentials=credentials)


def unavailable(note: str) -> list[AgentEvent]:
    """没有取得 Trace：明确记录原因，大模型调用次数显示为未取得。"""
    return [ev("log", level="warning", message=f"Trace 未取得：{note}"),
            ev("metrics", scope="turn", trace_status="unavailable", trace_note=note)]


def retryable(exc: Exception) -> bool:
    """只有暂时性情况值得再查：Trace 未就绪、网络错误、上游 5xx / 限流。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, TraceNotReady | httpx.TransportError)


def failure_reason(exc: Exception, wait_s: float) -> str:
    if isinstance(exc, AccountAuthError):
        return str(exc)
    if isinstance(exc, TraceNotReady):
        return f"{exc}（等待 {wait_s:g}s 后仍未收齐）"
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return "Trace 认证失败（Cookie／CSRF 可能过期），请更新凭据中的 observe_headers 后重新查询"
        return f"Trace 查询 HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError | TimeoutError):
        return f"Trace 查询网络错误或超时（{type(exc).__name__}）"
    return f"Trace 解析失败（{type(exc).__name__}）"


async def collect_trace(
    config: HiAgentConfig, secret: Any, mapper: HiAgentMapper,
    *, credentials: CredentialsManager | None = None,
) -> AsyncIterator[AgentEvent]:
    """在有限观测窗口查询；失败只影响观测完整性，只有暂时性情况才重试。"""
    headers = secret.get("observe_headers") if isinstance(secret, dict) else None
    note = (
        "未配置 Trace 观测地址" if not config.observe_base_url
        else "SSE 没有返回消息 ID" if not mapper.message_id
        else "凭据未提供 observe_headers" if not config.observe_account_ref and not isinstance(headers, dict)
        else None
    )
    if note:
        for event in unavailable(note):
            yield event
        return
    trace_auth: dict = {}
    if config.observe_account_ref:
        if credentials is None:
            for event in unavailable("Trace 账号管理器不可用"):
                yield event
            return
        try:
            await credentials.ensure(config.observe_account_ref, config.observe_base_url)
        except Exception as exc:  # noqa: BLE001
            note = str(exc) if isinstance(exc, AccountAuthError) else "Trace 账号不可用，请检查账号配置"
            for event in unavailable(note):
                if event.type == "metrics":
                    event.payload["trace_auth"] = {"account_ref": config.observe_account_ref}
                yield event
            return
    deadline = time.monotonic() + config.observation_wait_s
    async with httpx.AsyncClient(
        timeout=10, headers={} if config.observe_account_ref else headers, follow_redirects=False,
    ) as client:
        async def query(action: str, body: dict) -> dict:
            nonlocal trace_auth
            if config.workspace_id:
                body = {"WorkspaceID": config.workspace_id, **body}
            if config.observe_account_ref and credentials:
                response, trace_auth = await credentials.request(
                    config.observe_account_ref, config.observe_base_url,
                    params={"Action": action, "Version": "2025-05-01"}, body=body,
                )
                if credentials.auth.authentication_failed(response):
                    raise AccountAuthError(
                        "access_denied", "Trace 账号未通过查询授权，请检查站点权限或补充登录态",
                    )
            else:
                response = await client.post(
                    config.observe_base_url + "/api/observe",
                    params={"Action": action, "Version": "2025-05-01"}, json=body,
                )
            response.raise_for_status()
            result = response.json()
            if result.get("ResponseMetadata", {}).get("Error"):
                raise TraceNotReady("Trace 查询返回错误")
            return result.get("Result", result)

        while True:
            try:
                async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                    trace = await query("GetTraceInfo", {"MessageID": mapper.message_id})
                    if not trace.get("TraceID"):
                        raise TraceNotReady("Trace 尚未生成")
                    if not trace.get("EndTime"):
                        raise TraceNotReady("Trace 尚未完整")  # 不完整时不拉明细，等待重试
                    page = await query("ListTraceSpans", {
                        "IsCustomApp": False, "PageSize": 1000,
                        "Sort": [{"SortBy": "StartTime", "SortOrder": "Asc"}],
                        "SimpleFilters": [{"TraceID": {"Operator": "Equal", "Value": trace["TraceID"]}}],
                    })
                    events = trace_events(config, mapper, trace, page)
            except Exception as exc:  # noqa: BLE001 - Trace 的任何失败都不能改变本轮成败
                if retryable(exc) and time.monotonic() < deadline:
                    await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))
                    continue
                for event in unavailable(failure_reason(exc, config.observation_wait_s)):
                    if trace_auth and event.type == "metrics":
                        event.payload["trace_auth"] = trace_auth
                    yield event
                return
            yield ev("source.raw", source="hiagent_trace", trace=redact(trace), page=redact(page))
            for event in events:
                if trace_auth and event.type == "metrics":
                    event.payload["trace_auth"] = trace_auth
                yield event
            return
