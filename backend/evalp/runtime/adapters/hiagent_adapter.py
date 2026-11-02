"""HiAgent 已发布 API 的平台内托管适配器，不包含业务阶段判断。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from sqlalchemy import select, update

from evalp.context import AppContext
from evalp.errors import AgentError
from evalp.runtime.adapters.base import AgentAdapter, HealthResult, RunContext
from evalp.runtime.adapters.http_adapter import HttpAdapter
from evalp.runtime.adapters.registry import HiAgentConfig
from evalp.runtime.events import AgentEvent, ev, redact
from evalp.runtime.messages import Message
from evalp.spec.models import AdapterBinding, CodeSpec, ConfigSpec, HttpTransport, parse_spec
from evalp.store.models import Session, Version


def decoded(value: Any) -> Any:
    """允许 JSON 包装字符串，无法解析时保留原值。"""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return json.loads(value)
    return value


async def sse_frames(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """按空行分帧，保留上游事件 ID，支持多行 data 和尾帧。"""
    lines: list[str] = []
    event, identity = "", ""
    async for line in response.aiter_lines():
        if line == "":
            if lines:
                value = decoded(decoded("\n".join(lines)))
                if isinstance(value, dict):
                    yield {**value, "event": value.get("event") or event, "_event_id": identity}
            lines, event, identity = [], "", ""
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("id:"):
            identity = line[3:].strip()
    if lines:
        value = decoded(decoded("\n".join(lines)))
        if isinstance(value, dict):
            yield {**value, "event": value.get("event") or event, "_event_id": identity}


class HiAgentMapper:
    """调用身份来自上游 ID；思考阶段暂不声称是确定的 LLM 调用。"""

    def __init__(self, config: HiAgentConfig) -> None:
        self.config = config
        self.message_id: str | None = None
        self.conversation_id: str | None = None
        self.done = False
        self.calls: dict[str, dict] = {}
        self.stages: dict[str, dict] = {}
        self.current: str | None = None
        self.final_parts: list[str] = []
        self.usage: dict[str, Any] = {}
        self.seen: set[str] = set()

    def feed(self, frame: dict[str, Any]) -> list[AgentEvent]:
        """输出正文、思考和工具；整轮用量保留其范围，不分摊。"""
        eid = frame.get("_event_id")
        if eid and eid in self.seen:
            return []
        if eid:
            self.seen.add(eid)
        kind = frame.get("event")
        mid = frame.get("message_id")
        if not mid and kind in ("message_start", "message_end"):
            mid = frame.get("id")
        if mid:
            if self.message_id and str(mid) != self.message_id:
                raise AgentError("SSE 消息身份改变")
            self.message_id = str(mid)
        conversation = frame.get("conversation_id") or frame.get("app_conversation_id")
        if conversation:
            if self.conversation_id and conversation != self.conversation_id:
                raise AgentError("SSE 会话身份改变")
            self.conversation_id = str(conversation)
        if frame.get("app_version") and str(frame["app_version"]) != self.config.published_version:
            raise AgentError("SSE 发布版本与预期不一致")
        events = [ev("source.raw", source="hiagent_sse", frame=redact(frame))]
        if kind == "think_message_output_start":
            identity = frame.get("id")
            if not identity:
                raise AgentError("思考阶段缺少上游标识")
            self.current = str(identity)
            if self.current not in self.stages:
                self.stages[self.current] = {"text": [], "reasoning": [], "step": f"stage:{identity}"}
                events.append(ev(
                    "span.start", step_id=f"stage:{identity}", name="上游输出阶段",
                    attrs={"source": "hiagent_sse", "association": "unknown", "upstream_id": identity},
                ))
        elif kind == "message_output_start":
            identity = frame.get("think_message_id") or frame.get("id")
            self.current = str(identity) if identity else None
        elif kind in ("think_message", "think_message_output", "message"):
            identity = frame.get("think_message_id") or self.current
            stage = self.stages.get(str(identity)) if identity else None
            text = frame.get("answer") or frame.get("content") or frame.get("text")
            if isinstance(text, str):
                reasoning = kind != "message"
                if stage:
                    stage["reasoning" if reasoning else "text"].append(text)
                if not reasoning:
                    self.final_parts.append(text)
                events.append(ev(
                    "llm.reasoning" if reasoning else "llm.delta",
                    step_id=stage["step"] if stage else "output:unknown",
                    text=text, source="hiagent_sse", association="unknown",
                ))
        elif kind == "agent_thought" and frame.get("tool"):
            cid = str(frame.get("id") or "")
            if not cid:
                raise AgentError("工具请求缺少调用 ID")
            args = decoded(frame.get("tool_input") or {})
            call = {"name": frame["tool"], "arguments": args, "step": f"tool:{cid}"}
            if cid in self.calls:
                if self.calls[cid] != call:
                    raise AgentError("同一工具调用 ID 的内容发生变化")
                return events
            self.calls[cid] = call
            events.append(ev(
                "tool.call", step_id=call["step"], call_id=cid, name=call["name"],
                arguments=redact(args), source="hiagent_sse",
                protocol=frame.get("protocol") or "unknown", association="unknown",
            ))
        elif kind == "agent_thought_end":
            cid = str(frame.get("id") or "")
            call = self.calls.get(cid)
            if call is None:
                raise AgentError("工具结果没有对应调用")
            latency = frame.get("tool_latency")
            events.append(ev(
                "tool.result", step_id=call["step"], call_id=cid, name=call["name"],
                output=redact(decoded(frame.get("observation", frame.get("tool_output")))),
                is_error=bool(frame.get("error") or frame.get("error_msg") or frame.get("error_code")),
                latency_ms=(
                    round(latency * 1000) if isinstance(latency, int | float) and latency >= 0 else None
                ),
                source="hiagent_sse",
            ))
        elif kind == "message_end":
            self.done = True
            usage = frame.get("usage") or frame
            self.usage = {
                "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
                "scope": "turn", "source": "hiagent_sse", "observation_status": "partial",
            }
            events.append(ev("metrics", scope="turn", source="hiagent_sse", usage=self.usage,
                             execution_status="completed"))
        elif kind in ("error", "message_error"):
            raise AgentError("HiAgent 返回执行失败")
        return events


def business_bridge(config: HiAgentConfig, spec: ConfigSpec | CodeSpec) -> HttpAdapter:
    """业务桥 HttpAdapter：保留版本 spec 的参数与请求头，只替换地址和超时；检查与实际调用共用。"""
    if not isinstance(spec, CodeSpec) or spec.transport != "http":
        raise AgentError("业务桥接要求保留原 HTTP 业务版本")
    assert config.business_bridge is not None
    return HttpAdapter(spec.model_copy(update={
        "http": HttpTransport(
            base_url=config.business_bridge.base_url,
            timeout_s=config.business_bridge.timeout_s,
            headers=spec.http.headers,
        ),
    }))


class HiAgentAdapter(AgentAdapter):
    def __init__(self, binding: AdapterBinding, app: AppContext) -> None:
        self.binding, self.app = binding, app
        self.config = HiAgentConfig.model_validate(binding.config)

    async def credential(self) -> tuple[Any, str | None]:
        """解码后的凭据与其中的 api_key；api_key 不可用时为 None。"""
        secret = decoded(await self.app.secrets.get(self.binding.credential_ref))
        key = secret.get("api_key") if isinstance(secret, dict) else secret
        return secret, key if isinstance(key, str) and key.strip() else None

    async def healthcheck(self) -> HealthResult:
        """检查固定实现与凭据可用性，不发起上游模型请求。"""
        if self.config.business_bridge:
            # 桥接绑定的是不可变版本：用其 spec_hash 找到与实际调用相同的 spec。
            async with self.app.db.read() as s:
                spec = (await s.execute(
                    select(Version.spec).where(Version.spec_hash == self.config.business_bridge.spec_hash)
                )).scalars().first()
            if spec is None:
                return HealthResult(ok=False, message="业务桥接绑定的版本不存在")
            bridge = business_bridge(self.config, parse_spec(spec))
            try:
                return await bridge.healthcheck()
            finally:
                await bridge.aclose()
        _, key = await self.credential()
        return HealthResult(
            ok=key is not None, message="配置与凭据可读取，尚未验证上游认证" if key else "凭据不可用",
            details={"adapter": self.binding.id, "version": self.binding.version, "llm_requested": False},
        )

    async def run_turn(
        self, ctx: RunContext, history: list[Message], user_input: Message,
    ) -> AsyncIterator[AgentEvent]:
        if self.config.business_bridge:
            if ctx.version.spec_hash != self.config.business_bridge.spec_hash:
                raise AgentError("业务桥接与固定版本 spec hash 不一致")
            bridge = business_bridge(self.config, ctx.version.spec_model())
            try:
                async for event in bridge.run_turn(ctx, history, user_input):
                    yield event
            finally:
                await bridge.aclose()
            return
        secret, key = await self.credential()
        if key is None:
            raise AgentError("HiAgent 凭据不可用")
        headers = {"Apikey": key, "X-APP-User": self.config.user_id}
        mapper = HiAgentMapper(self.config)
        # 规范指标以 HiAgent Trace 为准：回复结束后由会话服务在后台补齐，不阻塞本轮。
        from evalp.runtime.adapters.hiagent_trace import collect_trace

        ctx.late_observe = lambda: collect_trace(self.config, secret, mapper)
        submitted = False
        async with httpx.AsyncClient(
            base_url=self.config.base_url, headers=headers,
            timeout=self.config.timeout_s, follow_redirects=False,
        ) as client:
            try:
                async with self.app.db.read() as s:
                    snapshot = (await s.execute(
                        select(Session.adapter_snapshot).where(Session.id == ctx.session_id)
                    )).scalar_one_or_none() or ctx.version.adapter_snapshot or {}
                upstream = snapshot.get("upstream") or {}
                conversation = upstream.get("conversation_id")
                if not conversation:
                    response = await client.post(
                        "/".join([self.config.base_url, "api/v1/create_conversation"]),
                        json={"ConversationName": f"EvalP {ctx.session_id}"},
                    )
                    if response.is_error:
                        raise AgentError(f"HiAgent 创建会话 HTTP {response.status_code}")
                    conversation = response.json().get("Conversation", {}).get("AppConversationID")
                    if not isinstance(conversation, str) or not conversation:
                        raise AgentError("HiAgent 创建会话没有返回有效 ID")
                    snapshot = {**snapshot, "upstream": {
                        "conversation_id": conversation, "app_id": self.config.app_id,
                        "expected_version": self.config.published_version,
                    }}
                    async with self.app.db.write() as s:
                        await s.execute(update(Session).where(Session.id == ctx.session_id).values(
                            adapter_snapshot=snapshot,
                        ))
                mapper.conversation_id = conversation
                t0 = time.monotonic()
                submitted = True
                async with client.stream(
                    "POST", self.config.base_url + "/api/v1/chat_query_v2",
                    headers={"Accept": "text/event-stream"},
                    json={"AppConversationID": conversation, "Query": user_input.text(),
                          "ResponseMode": "streaming", "PubAgentJump": True},
                ) as response:
                    if response.is_error:
                        raise AgentError(f"HiAgent 发布 API HTTP {response.status_code}")
                    async for frame in sse_frames(response):
                        for event in mapper.feed(frame):
                            yield event
                        if mapper.done:
                            break
                transport_ms = round((time.monotonic() - t0) * 1000)
                if not mapper.done:
                    raise AgentError("上游结果未知：连接结束但未收到 message_end",
                                     code="UPSTREAM_RESULT_UNKNOWN")
                text = "".join(mapper.final_parts)
                final = Message(role="assistant", content=text)
                ctx.new_messages.append(final)
                yield ev("message.assistant", message=final)
                for identity, stage in mapper.stages.items():
                    yield ev("span.end", step_id=stage["step"], name="上游输出阶段",
                             attrs={"upstream_id": identity, "association": "unknown"})
                yield ev("metrics", scope="turn", usage=dict(mapper.usage), transport_ms=transport_ms,
                         upstream={"message_id": mapper.message_id, "conversation_id": conversation,
                                   "app_id": self.config.app_id,
                                   "expected_version": self.config.published_version})
                yield ev("turn.end", status="completed", usage=dict(mapper.usage))
            except asyncio.CancelledError:
                if submitted and mapper.message_id and not mapper.done:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(client.post(
                            self.config.base_url + "/api/v1/stop_message",
                            json={"MessageID": mapper.message_id}, timeout=5,
                        ))
                raise
            except httpx.HTTPError:
                raise AgentError(
                    "上游结果未知，未自动重试" if submitted else "HiAgent 连接失败",
                    code="UPSTREAM_RESULT_UNKNOWN" if submitted else "AGENT_ERROR",
                ) from None
