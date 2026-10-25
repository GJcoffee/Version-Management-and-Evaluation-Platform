"""http 工具（详设 5.2 / 6.9）。

- URL 与 headers 中的 ``${env:NAME}`` 在**执行时**替换（缺失时返回错误结果）；
- ``body_template`` 用 Jinja2 沙箱渲染，变量为 ``args``（``StrictUndefined``）；
  未配置时：POST / PUT / PATCH 把参数作为 JSON body，GET / DELETE 作为查询参数；
- 2xx 返回 JSON（解析成功时）或文本；其他状态码返回 ``is_error=True``。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from evalp.errors import EvalpError
from evalp.runtime.tools.base import ToolRunner
from evalp.runtime.tools.definition import HttpImpl, ToolDefinition
from evalp.runtime.tools.result import ToolResult, error_result
from evalp.utils.envref import resolve_env

ERROR_BODY_CHARS = 2000
_jinja = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


class SharedHttpClient:
    """按需创建、可复用的 httpx 客户端（属于一个 ToolSet，随 ToolSet 关闭）。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=True)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def render_body(template: str, args: dict[str, Any]) -> str:
    """渲染 body_template；模板错误抛 jinja2.TemplateError。"""
    return _jinja.from_string(template).render(args=args)


def _query_params(args: dict[str, Any]) -> dict[str, str]:
    return {
        k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        for k, v in args.items()
        if v is not None
    }


class HttpTool(ToolRunner):
    def __init__(self, definition: ToolDefinition, client: SharedHttpClient) -> None:
        assert isinstance(definition.impl, HttpImpl)
        self.impl: HttpImpl = definition.impl
        self.name = definition.name
        self.description = definition.description
        self.parameters = definition.parameters
        self.timeout_s = self.impl.timeout_s
        self._http = client

    async def run(self, args: dict[str, Any], timeout: float | None) -> ToolResult:
        impl = self.impl
        try:
            url = resolve_env(impl.url)
            headers = {k: resolve_env(v) for k, v in impl.headers.items()}
            request_kwargs: dict[str, Any] = {}
            if impl.body_template is not None:
                body = render_body(impl.body_template, args)
                if "content-type" not in {k.lower() for k in headers}:
                    headers["Content-Type"] = "application/json"
                request_kwargs["content"] = body.encode("utf-8")
            elif impl.method in ("POST", "PUT", "PATCH"):
                request_kwargs["json"] = args
            elif args:
                request_kwargs["params"] = _query_params(args)
        except EvalpError as e:  # 环境变量缺失
            return error_result(e.message)
        except TemplateError as e:
            return error_result(f"body_template 渲染失败: {type(e).__name__}: {e}")

        try:
            resp = await self._http.get().request(
                impl.method, url, headers=headers, timeout=timeout or impl.timeout_s, **request_kwargs
            )
        except httpx.TimeoutException:
            return error_result(f"HTTP 请求超时（{timeout or impl.timeout_s:g}s）")
        except httpx.HTTPError as e:
            return error_result(f"HTTP 请求失败: {type(e).__name__}: {e}")

        if not 200 <= resp.status_code < 300:
            return error_result(
                f"HTTP {resp.status_code}: {resp.text[:ERROR_BODY_CHARS]}", status=resp.status_code
            )
        text = resp.text
        if not text.strip():
            return ToolResult(output="")
        try:
            return ToolResult(output=resp.json())
        except ValueError:
            return ToolResult(output=text)
