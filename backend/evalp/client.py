"""EvalP REST 薄客户端（CLI 与 MCP 共用）。

- 服务地址：显式参数 > ``.evalp.toml`` 的 ``[server].url`` > 数据目录配置里的 host:port（默认 7860）；
- token：读取数据目录下的 ``token`` 文件（数据目录取自 ``--data-dir`` / ``EVALP_DATA_DIR`` / ``~/.evalp``）；
- 错误：服务端的 ``{"error": {code, message, details}}`` 统一转成 :class:`EvalpClientError`，
  连接失败给出"请先运行 evalp serve"的提示。

客户端是异步的（httpx.AsyncClient），测试中可以通过 ``http=`` 注入挂在 ASGI 应用上的客户端。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from evalp.auth import is_loopback
from evalp.config import load_config

API_PREFIX = "/api/v1"
DEFAULT_URL = "http://127.0.0.1:7860"

# 常见错误码的补充提示（服务端 message 已是中文，这里只追加可操作的建议）
_HINTS = {
    "UNAUTHORIZED": "token 缺失或不正确。请确认 --data-dir / EVALP_DATA_DIR 指向运行 evalp serve 的目录。",
    "SESSION_BUSY": "该会话正在执行上一轮，请稍后再试。",
    "DRAFT_CONFLICT": "草稿已被修改。可以先 evalp pull，或用 evalp push --force 覆盖。",
}


class EvalpClientError(Exception):
    """调用 EvalP 服务失败。``code`` 取自服务端错误码；本地错误用 ``CLIENT_*``。"""

    def __init__(
        self, message: str, *, code: str = "ERROR", status: int | None = None, details: dict | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details: dict[str, Any] = details or {}

    def friendly(self) -> str:
        """面向用户的中文提示（含字段级错误明细）。"""
        lines = [self.message]
        errors = self.details.get("errors") if isinstance(self.details, dict) else None
        if isinstance(errors, list):
            for e in errors[:10]:
                if isinstance(e, dict):
                    where = e.get("path") or e.get("line")
                    prefix = f"{where}: " if where not in (None, "") else ""
                    lines.append(f"  - {prefix}{e.get('message', '')}")
            if len(errors) > 10:
                lines.append(f"  ……另有 {len(errors) - 10} 项")
        hint = _HINTS.get(self.code)
        if hint and hint not in self.message:
            lines.append(hint)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.friendly()


class ServerUnavailable(EvalpClientError):
    """连接不上 EvalP 服务。"""

    def __init__(self, url: str, reason: str = "") -> None:
        msg = f"无法连接到 EvalP 服务（{url}）。请先运行 evalp serve。"
        if reason:
            msg += f"\n原因: {reason}"
        super().__init__(msg, code="CLIENT_UNAVAILABLE")


@dataclass
class SSEEvent:
    """一个 SSE 帧。``data`` 已按 JSON 解析（解析失败时保留原字符串）。"""

    event: str
    data: Any
    id: str | None = None


def resolve_server_url(url: str | None, data_dir: str | os.PathLike[str] | None = None) -> str:
    """服务地址：显式参数优先，否则取数据目录配置里的 host:port。"""
    if url:
        return url.rstrip("/")
    try:
        cfg = load_config(data_dir)
        host = "127.0.0.1" if cfg.server.host in ("0.0.0.0", "::") else cfg.server.host
        return f"http://{host}:{cfg.server.port}"
    except Exception:  # 配置文件损坏时退回默认值，由后续请求报出真正的问题
        return DEFAULT_URL


def read_token(data_dir: str | os.PathLike[str] | None = None) -> str:
    """读取数据目录下的 token 文件。"""
    path = load_config(data_dir).token_path
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise EvalpClientError(
            f"找不到 API token（{path}）。请先运行 evalp serve 生成；"
            "如果服务使用了其他数据目录，请通过 --data-dir 或环境变量 EVALP_DATA_DIR 指定。",
            code="CLIENT_NO_TOKEN",
        )
    return token


class EvalpClient:
    """REST 客户端。``async with EvalpClient(...) as c: await c.get("/agents")``。"""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        token: str | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = str(http.base_url).rstrip("/") if http is not None else base_url.rstrip("/")
        self.token = token
        self._owns_http = http is None
        if http is None:
            host = urlparse(self.base_url).hostname
            http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=httpx.Timeout(timeout, connect=5.0),
                trust_env=not is_loopback(host),  # 本机地址不走代理
            )
        self._http = http

    @classmethod
    def create(
        cls,
        url: str | None = None,
        data_dir: str | os.PathLike[str] | None = None,
        **kw: Any,
    ) -> EvalpClient:
        """按约定解析地址与 token 后构造客户端。"""
        return cls(resolve_server_url(url, data_dir), read_token(data_dir), **kw)

    async def __aenter__(self) -> EvalpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ 请求

    def web_url(self, path: str) -> str:
        """Web UI 链接，如 ``/evals/evr_x``。"""
        return f"{self.base_url}{path}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
        raw: bool = False,
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> Any:
        """发请求并返回 JSON；失败抛 :class:`EvalpClientError`。``raw=True`` 时 path 不加 /api/v1 前缀。

        ``files`` 非空时以 multipart 上传（``{字段名: (文件名, 内容)}``）。"""
        url = path if raw else f"{API_PREFIX}{path}"
        kw: dict[str, Any] = {}
        if timeout is not None:
            kw["timeout"] = httpx.Timeout(timeout, connect=5.0)
        try:
            if files:
                kw["files"] = files
            else:
                kw["json"] = json_body
            resp = await self._http.request(method, url, params=_clean(params), **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ServerUnavailable(self.base_url, str(e)) from e
        except httpx.TimeoutException as e:
            raise EvalpClientError(f"请求超时: {method} {path}", code="CLIENT_TIMEOUT") from e
        except httpx.HTTPError as e:
            raise EvalpClientError(f"请求失败: {e}", code="CLIENT_HTTP_ERROR") from e
        return _parse_response(resp)

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return await self.request("POST", path, json_body=body if body is not None else {}, **kw)

    async def put(self, path: str, body: Any = None, **kw: Any) -> Any:
        return await self.request("PUT", path, json_body=body, **kw)

    async def patch(self, path: str, body: Any = None, **kw: Any) -> Any:
        return await self.request("PATCH", path, json_body=body, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, **kw)

    async def upload_blob(
        self, data: bytes, filename: str = "upload.bin", *, timeout: float = 300.0
    ) -> dict[str, Any]:
        """上传大文件（``POST /assets/upload``），返回 ``{blob_upload_id, size}``。"""
        return await self.request("POST", "/assets/upload", files={"file": (filename, data)}, timeout=timeout)

    async def stream(
        self, path: str, *, params: dict[str, Any] | None = None, raw: bool = False
    ) -> AsyncIterator[SSEEvent]:
        """订阅 SSE。逐帧产出 :class:`SSEEvent`，服务端关闭连接后结束。"""
        url = path if raw else f"{API_PREFIX}{path}"
        timeout = httpx.Timeout(30.0, connect=5.0, read=120.0)  # 服务端每 15 秒发一次心跳
        try:
            async with self._http.stream(
                "GET",
                url,
                params=_clean(params),
                timeout=timeout,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    _parse_response(resp)
                event, data_lines, last_id = "message", [], None
                async for line in resp.aiter_lines():
                    if line == "":
                        if data_lines:
                            yield SSEEvent(event, _json_or_text("\n".join(data_lines)), last_id)
                        event, data_lines, last_id = "message", [], None
                    elif line.startswith(":"):
                        continue
                    else:
                        name, _, value = line.partition(":")
                        value = value[1:] if value.startswith(" ") else value
                        if name == "event":
                            event = value
                        elif name == "data":
                            data_lines.append(value)
                        elif name == "id":
                            last_id = value
                if data_lines:
                    yield SSEEvent(event, _json_or_text("\n".join(data_lines)), last_id)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ServerUnavailable(self.base_url, str(e)) from e
        except httpx.TimeoutException as e:
            raise EvalpClientError("订阅事件流超时", code="CLIENT_TIMEOUT") from e


# ---------------------------------------------------------------------- 工具函数


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _parse_response(resp: httpx.Response) -> Any:
    if resp.status_code < 400:
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text
    code, message, details = "HTTP_ERROR", f"服务返回 HTTP {resp.status_code}", {}
    try:
        err = resp.json().get("error") or {}
        code = err.get("code", code)
        message = err.get("message") or message
        details = err.get("details") or {}
    except (ValueError, AttributeError):
        if resp.text:
            message += f": {resp.text[:200]}"
    if resp.status_code == 401:
        code = "UNAUTHORIZED"
        message = "鉴权失败（401）"
    raise EvalpClientError(message, code=code, status=resp.status_code, details=details)
