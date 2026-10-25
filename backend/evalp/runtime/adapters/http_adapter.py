"""HttpAdapter：通过 HTTP 调用代码型 agent（``evalp-agent/1`` 协议，详设 6.5）。

- ``POST {base_url}/evalp/run``：响应 ``application/json`` 为 L1（``{"output": Message, ...}``），
  ``application/x-ndjson`` 为 L2（每行一个事件，不含 seq / turn_id）；
- 429 / 502 / 503 / 504 与连接失败产出 ``retriable=true`` 的 error 事件，其他错误不可重试；
- 取消 / 超时：断开连接，并尽力调用 ``POST {base_url}/evalp/cancel``；
- 健康检查：``GET {base_url}/evalp/health``；
- 请求头中的 ``${env:NAME}`` 在每次执行时才替换（环境变量缺失时本轮以 VALIDATION_ERROR 失败）。

agent 一般在本机或内网，所以 httpx 客户端不读取环境里的代理设置（``trust_env=False``）。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from evalp.logging import get_logger
from evalp.runtime.adapters.base import AgentAdapter, HealthResult, RunContext
from evalp.runtime.adapters.protocol import (
    NDJSON_TYPES,
    PROTOCOL,
    ProtocolError,
    build_request,
    event_from_wire,
    l1_events,
    protocol_error_event,
)
from evalp.runtime.events import AgentEvent, ev, redact
from evalp.runtime.messages import Message
from evalp.spec.models import CodeSpec
from evalp.utils.envref import resolve_env, resolve_env_deep

log = get_logger(__name__)

RETRIABLE_STATUS = frozenset({429, 502, 503, 504})
CANCEL_TIMEOUT_S = 2.0
BODY_EXCERPT = 500


def _excerpt(text: str, limit: int = BODY_EXCERPT) -> str:
    text = str(redact(" ".join(text.split())))
    return text if len(text) <= limit else text[:limit] + "…"


class HttpAdapter(AgentAdapter):
    """HTTP 代码型 agent。``transport`` 仅供测试注入（如 ASGITransport）。"""

    def __init__(self, spec: CodeSpec, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        assert spec.http is not None
        self.spec = spec
        self.cfg = spec.http
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(trust_env=False, transport=self._transport)
        return self._client

    @property
    def base(self) -> str:
        """agent 的 base_url（``${env:NAME}`` 在使用时才替换）。"""
        return resolve_env(self.cfg.base_url).rstrip("/")

    def _headers(self) -> dict[str, str]:
        """请求头；``${env:NAME}`` 在这里（执行时）才替换。"""
        return {str(k): str(v) for k, v in resolve_env_deep(dict(self.cfg.headers)).items()}

    # ------------------------------------------------------------ 执行

    async def run_turn(
        self, ctx: RunContext, history: list[Message], user_input: Message
    ) -> AsyncIterator[AgentEvent]:
        body = build_request(ctx, self.spec, history, user_input)
        headers = {"Accept": "application/x-ndjson, application/json", **self._headers()}
        timeout = httpx.Timeout(self.cfg.timeout_s, connect=min(10.0, self.cfg.timeout_s))
        finished = False
        try:
            async with self._http().stream(
                "POST", f"{self.base}/evalp/run", json=body, headers=headers, timeout=timeout
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    yield self._http_error(resp.status_code, text)
                    finished = True
                    return
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype in NDJSON_TYPES:
                    async for event in self._read_ndjson(resp, user_input):
                        yield event
                else:
                    raw = await resp.aread()
                    for event in self._read_l1(raw, ctx):
                        yield event
                finished = True
        except httpx.ConnectError as e:
            yield self._transport_error("AGENT_UNREACHABLE", f"无法连接 agent（{self.base}）: {e}", True)
            finished = True
        except httpx.ConnectTimeout:
            yield self._transport_error("AGENT_UNREACHABLE", f"连接 agent 超时（{self.base}）", True)
            finished = True
        except httpx.ReadTimeout:
            yield self._transport_error(
                "AGENT_TIMEOUT", f"agent {self.cfg.timeout_s:g}s 内没有返回数据（http.timeout_s）", False
            )
        except (httpx.RemoteProtocolError, httpx.ReadError) as e:
            yield self._transport_error("AGENT_CONNECTION_LOST", f"与 agent 的连接中断: {e}", True)
            finished = True
        finally:
            if not finished:
                await self._send_cancel(ctx)

    def _http_error(self, status: int, text: str) -> AgentEvent:
        retriable = status in RETRIABLE_STATUS
        return ev(
            "error",
            code="AGENT_ERROR",
            message=f"agent 返回 HTTP {status}: {_excerpt(text) or '（空响应）'}",
            retriable=retriable,
            status=status,
        )

    @staticmethod
    def _transport_error(code: str, message: str, retriable: bool) -> AgentEvent:
        return ev("error", code=code, message=message, retriable=retriable)

    async def _read_ndjson(self, resp: httpx.Response, user_input: Message) -> AsyncIterator[AgentEvent]:
        n = 0
        async for raw in resp.aiter_lines():
            line = raw.strip()
            if not line:
                continue
            n += 1
            try:
                try:
                    obj = json.loads(line)
                except ValueError as e:
                    raise ProtocolError(
                        f"第 {n} 行不是合法的 JSON: {e}", line=n, excerpt=_excerpt(line, 200)
                    ) from None
                try:
                    event = event_from_wire(obj, user_input=user_input)
                except ProtocolError as e:
                    e.message = f"第 {n} 行: {e.message}"
                    e.details.setdefault("line", n)
                    e.details.setdefault("excerpt", _excerpt(line, 200))
                    raise
            except ProtocolError as e:
                yield protocol_error_event(e)
                return
            yield event
            if event.type == "turn.end":
                return

    def _read_l1(self, raw: bytes, ctx: RunContext) -> list[AgentEvent]:
        try:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise ProtocolError(
                    f"响应不是合法的 JSON（也不是 {PROTOCOL} 的 NDJSON）: {e}",
                    excerpt=_excerpt(raw.decode("utf-8", "replace"), 200),
                ) from None
            return l1_events(obj, ctx)
        except ProtocolError as e:
            return [protocol_error_event(e)]

    async def _send_cancel(self, ctx: RunContext) -> None:
        """尽力通知 agent 取消；任何失败都忽略。"""
        try:
            await self._http().post(
                f"{self.base}/evalp/cancel",
                json={"turn_id": ctx.turn_id, "session_id": ctx.session_id},
                headers=self._headers(),
                timeout=CANCEL_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001 - 取消通知是 best effort
            log.debug("http_adapter.cancel_failed", turn_id=ctx.turn_id, error=f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 健康检查

    async def healthcheck(self) -> HealthResult:
        start = time.perf_counter()
        details: dict[str, Any] = {"url": f"{self.base}/evalp/health"}
        try:
            r = await self._http().get(details["url"], headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as e:
            return HealthResult(
                ok=False, message=f"无法访问健康检查接口: {type(e).__name__}: {e}", details=details
            )
        latency = int((time.perf_counter() - start) * 1000)
        details["status"] = r.status_code
        if r.status_code != 200:
            return HealthResult(
                ok=False, message=f"健康检查返回 HTTP {r.status_code}", latency_ms=latency, details=details
            )
        try:
            data = r.json()
        except ValueError:
            return HealthResult(
                ok=False, message="健康检查响应不是 JSON", latency_ms=latency, details=details
            )
        if not isinstance(data, dict) or not data.get("ok"):
            return HealthResult(
                ok=False, message=f"agent 报告不健康: {data!r}"[:300], latency_ms=latency, details=details
            )
        protocol = data.get("protocol")
        details["protocol"] = protocol
        msg = "健康"
        if protocol != PROTOCOL:
            msg = f"健康，但协议版本为 {protocol!r}，期望 {PROTOCOL}"
        return HealthResult(ok=True, message=msg, latency_ms=latency, details=details)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
