"""HiAgent 已发布 API 的平台内托管适配器，不包含业务阶段判断。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from sqlalchemy import select, update

from evalp.context import AppContext
from evalp.errors import AgentError
from evalp.runtime.adapters.base import (
    HealthResult,
    PlatformAdapter,
    RunContext,
    TraceRefreshRequest,
)
from evalp.runtime.adapters.binding import NormalizedBinding, normalize_binding
from evalp.runtime.adapters.http_adapter import HttpAdapter
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.mapper import HiAgentMapper
from evalp.runtime.adapters.platforms.hiagent.sse import decoded, sse_frames
from evalp.runtime.credentials import CredentialsManager
from evalp.runtime.events import AgentEvent, ev
from evalp.runtime.hiagent_auth import AccountAuthError
from evalp.runtime.messages import Message
from evalp.spec.models import AdapterBinding, CodeSpec, ConfigSpec, HttpTransport, parse_spec
from evalp.store.models import Session, Version


def business_bridge(config: HiAgentConfig, spec: ConfigSpec | CodeSpec) -> HttpAdapter:
    """业务桥 HttpAdapter：保留版本 spec 的参数与请求头，只替换地址和超时；检查与实际调用共用。

    仅为尚未迁移的业务绑定保留；新绑定不应使用，业务胶水迁走后删除（阶段 4）。
    """
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


class HiAgentAdapter(PlatformAdapter):
    adapter = "hiagent"
    major = 1
    capabilities = frozenset({
        "streaming", "reasoning", "function_call", "mcp", "turn_usage", "trace", "refresh_trace",
        "no_external_history",
    })
    config_model = HiAgentConfig
    trace_config_keys = frozenset({"observe_base_url", "observe_account_ref", "observation_wait_s"})

    def __init__(self, binding: NormalizedBinding | AdapterBinding | dict[str, Any], app: AppContext) -> None:
        self.binding, self.app = normalize_binding(binding), app
        self.config = HiAgentConfig.model_validate(self.binding.config)

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
        secret, key = await self.credential()
        details: dict[str, Any] = {"llm_requested": False}
        if key is None:
            return HealthResult(ok=False, message="API Key 不可用，请新增凭据并更新配置", details=details)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                # 已发布接口的只读查询：不存在的 RunID 不执行工作流或创建会话。
                response = await client.post(
                    self.config.base_url + "/api/v1/query_run_app_process",
                    headers={"Apikey": key, "X-APP-User": self.config.user_id},
                    json={"RunID": "evalp-auth-check"},
                )
                if response.status_code in (401, 403):
                    return HealthResult(
                        ok=False, message="发布 API 认证失败（Key 可能过期），请新增凭据并更新配置",
                        details={**details, "api_key": "authentication_failed"},
                    )
                result = response.json()
                error = result.get("ResponseMetadata", {}).get("Error") if isinstance(result, dict) else None
                # 此明确错误在认证后判定应用类型；错误 Key 对照返回 403 Unauthorized.Warning。
                chat_app = (response.status_code == 400 and isinstance(error, dict)
                            and error.get("Code") == "BadRequest.Warning"
                            and error.get("Message") == "Bad request: only support workflow type app")
                if not chat_app:
                    response.raise_for_status()
                value = result.get("Result", result) if isinstance(result, dict) else None
                details["api_key"] = (
                    "verified" if chat_app or (isinstance(value, dict) and "status" in value and not error)
                    else "unverified"
                )
                details["published_probe"] = "chat_app_type_check" if chat_app else "workflow_process_query"
                headers = secret.get("observe_headers") if isinstance(secret, dict) else None
                if self.config.observe_base_url and (self.config.observe_account_ref or headers):
                    body = {
                        **({"WorkspaceID": self.config.workspace_id} if self.config.workspace_id else {}),
                        "MessageID": "evalp-auth-check",
                    }
                    params = {"Action": "GetTraceInfo", "Version": "2025-05-01"}
                    if self.config.observe_account_ref:
                        response, auth = await self.app.svc(CredentialsManager).request(
                            self.config.observe_account_ref, self.config.observe_base_url,
                            params=params, body=body,
                        )
                        details["trace_auth"] = auth
                    else:
                        response = await client.post(
                            self.config.observe_base_url + "/api/observe",
                            headers=headers, params=params, json=body,
                        )
                    if response.status_code in (401, 403):
                        return HealthResult(
                            ok=False,
                            message=("Trace 账号无权查询，请检查站点权限"
                                     if self.config.observe_account_ref else
                                     "Trace 认证失败（Cookie／CSRF 可能过期），请更新 observe_headers"),
                            details={**details, "observe": "authentication_failed"},
                        )
                    response.raise_for_status()
                    error = response.json().get("ResponseMetadata", {}).get("Error")
                    details["observe"] = "unverified" if error else "verified"
                else:
                    details["observe"] = "not_configured"
            verified = (details["api_key"] == "verified"
                        and details["observe"] in ("verified", "not_configured"))
            return HealthResult(
                ok=verified, message="上游认证检查通过" if verified else
                "接口未提供可确认的认证结果，请用已有轮次重查 Trace 验证",
                latency_ms=round((time.monotonic() - started) * 1000), details=details,
            )
        except AccountAuthError as exc:
            return HealthResult(ok=False, message=str(exc), details={**details, "observe": exc.status})
        except (httpx.HTTPError, ValueError, TypeError):
            return HealthResult(
                ok=False, message="认证检查未完成，请检查服务地址与网络；凭据状态尚未确认", details=details,
            )

    async def refresh_trace(
        self, request: TraceRefreshRequest,
    ) -> AsyncIterator[AgentEvent]:
        """按同一上游消息重查 Trace；整轮 token 仍以当时的 SSE 为准。"""
        from evalp.runtime.adapters.platforms.hiagent.trace import refresh_trace

        secret = request.credential
        if secret is None:
            secret = decoded(await self.app.secrets.get(self.binding.credential_ref))
        usage = {
            key: request.previous_usage[key]
            for key in ("prompt_tokens", "completion_tokens")
            if isinstance(request.previous_usage.get(key), int)
            and not isinstance(request.previous_usage.get(key), bool)
            and request.previous_usage[key] > 0
        }
        async for event in refresh_trace(
            self.config, secret, request.message_id, usage,
            credentials=self.app.svc(CredentialsManager) if self.config.observe_account_ref else None,
        ):
            yield event

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
        from evalp.runtime.adapters.platforms.hiagent.trace import collect_trace

        ctx.late_observe = lambda: collect_trace(
            self.config, secret, mapper,
            credentials=self.app.svc(CredentialsManager) if self.config.observe_account_ref else None,
        )
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
                # AppConversationID 与 SSE conversation_id 属于不同标识空间：只校验 SSE 帧之间一致，
                # 不用请求侧的会话 ID 约束运行侧 ID。
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
