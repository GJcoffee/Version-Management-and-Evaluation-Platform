"""ToolSet：把一组工具定义变成可被模型调用、可执行的工具集（详设 6.4 / 6.9）。

用法::

    tools = ToolSet.build(
        [tool_definition, BuiltinTool(sources=[("faq", text, sha)], top_k=3)],
        mocks={"query_order": [MockResponse(match={"order_id": "A001"}, output={"status": "已发货"})]},
    )
    schemas = tools.schemas()                       # 传给 LLM 的 tools 参数
    result = await tools.execute(tool_call, timeout=30)   # ToolResult，永不因工具错误而抛异常
    messages.append(Message(role="tool", tool_call_id=tool_call.id, content=result.as_text()))
    await tools.aclose()

模型调用不存在的工具、或参数不是合法 JSON 对象时，返回 ``is_error=True`` 的结果（不抛异常），
让模型有机会自行纠正。``mocks``（评测用例的 tool_mocks）按工具名覆盖同名工具的实现，
结果带 ``mocked=True``；mocks 里出现但工具集中没有的名字会被忽略。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evalp.errors import ValidationFailed
from evalp.logging import get_logger
from evalp.runtime.messages import ToolCall
from evalp.runtime.tools.base import ToolRunner
from evalp.runtime.tools.builtin import KnowledgeSearchTool
from evalp.runtime.tools.definition import (
    HttpImpl,
    MockImpl,
    MockResponse,
    PythonImpl,
    ToolDefinition,
)
from evalp.runtime.tools.http_tool import HttpTool, SharedHttpClient
from evalp.runtime.tools.mock_tool import MockTool
from evalp.runtime.tools.python_tool import PythonTool
from evalp.runtime.tools.result import ToolResult, error_result

log = get_logger(__name__)

TIMEOUT_GRACE_S = 5.0  # 外层兜底超时比工具自己的超时多留的时间

__all__ = ["BuiltinTool", "ToolResult", "ToolSet"]


class BuiltinTool(BaseModel):
    """内置工具声明。sources 为 (资产名, 文本内容, 内容哈希) 列表。"""

    model_config = ConfigDict(extra="forbid")

    name: Literal["knowledge_search"] = "knowledge_search"
    sources: list[tuple[str, str, str]] = Field(default_factory=list)
    top_k: int = Field(5, ge=1)


MockSpec = Mapping[str, list[MockResponse | dict[str, Any]]]


def _build_runner(tool: ToolDefinition | BuiltinTool, http: SharedHttpClient) -> ToolRunner:
    if isinstance(tool, BuiltinTool):
        return KnowledgeSearchTool(tool.sources, tool.top_k, name=tool.name)
    impl = tool.impl
    if isinstance(impl, HttpImpl):
        return HttpTool(tool, http)
    if isinstance(impl, PythonImpl):
        return PythonTool(tool)
    if isinstance(impl, MockImpl):
        return MockTool(tool.name, tool.description, tool.parameters, impl.responses)
    raise ValidationFailed(f"不支持的工具类型: {type(impl).__name__}", {"tool": tool.name})


def _parse_args(raw: dict[str, Any] | str) -> tuple[dict[str, Any] | None, str]:
    """返回 (参数 dict, 错误信息)。"""
    if isinstance(raw, dict):
        return raw, ""
    if not raw.strip():
        return {}, ""
    try:
        val = json.loads(raw)
    except ValueError as e:
        return None, f"参数不是合法的 JSON: {e}。收到的原文: {raw[:200]}"
    if not isinstance(val, dict):
        return None, f"参数必须是 JSON 对象，实际是 {type(val).__name__}"
    return val, ""


class ToolSet:
    """一组可执行工具。"""

    def __init__(self, runners: list[ToolRunner], http: SharedHttpClient | None = None) -> None:
        self._runners: dict[str, ToolRunner] = {}
        for r in runners:
            if r.name in self._runners:
                raise ValidationFailed(f"工具名重复: {r.name}", {"tool": r.name})
            self._runners[r.name] = r
        self._http = http or SharedHttpClient()

    @classmethod
    def build(
        cls,
        tools: list[ToolDefinition | BuiltinTool],
        mocks: MockSpec | None = None,
    ) -> ToolSet:
        """由工具定义构建工具集；mocks 覆盖同名工具。"""
        http = SharedHttpClient()
        runners = [_build_runner(t, http) for t in tools]
        if mocks:
            for i, r in enumerate(runners):
                if r.name in mocks:
                    responses = [
                        m if isinstance(m, MockResponse) else MockResponse.model_validate(m)
                        for m in mocks[r.name]
                    ]
                    runners[i] = MockTool(r.name, r.description, r.parameters, responses)
        return cls(runners, http)

    @property
    def names(self) -> list[str]:
        return list(self._runners)

    def __contains__(self, name: object) -> bool:
        return name in self._runners

    def __len__(self) -> int:
        return len(self._runners)

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI tools 参数列表（空工具集返回空列表）。"""
        return [r.schema() for r in self._runners.values()]

    def is_mocked(self, name: str) -> bool:
        r = self._runners.get(name)
        return bool(r and r.mocked)

    async def execute(self, call: ToolCall, timeout: float | None = None) -> ToolResult:
        """执行一次工具调用。timeout 为调用方给的上限，与工具自身的 timeout_s 取较小者。"""
        start = time.perf_counter()
        result = await self._execute(call, timeout)
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        return result

    async def _execute(self, call: ToolCall, timeout: float | None) -> ToolResult:
        runner = self._runners.get(call.name)
        if runner is None:
            avail = ", ".join(self._runners) or "（无）"
            return error_result(f"未知工具: {call.name}。可用工具: {avail}")
        args, err = _parse_args(call.arguments)
        if args is None:
            return error_result(f"工具 {call.name} 的{err}")
        limits = [t for t in (timeout, runner.timeout_s) if t]
        eff = min(limits) if limits else None
        try:
            async with asyncio.timeout(eff + TIMEOUT_GRACE_S if eff else None):
                result = await runner.run(args, eff)
        except TimeoutError:
            return error_result(f"工具 {call.name} 执行超时（{eff:g}s）")
        except Exception as e:  # 工具实现的任何异常都转为错误结果交给模型
            log.warning("tool.execute_failed", tool=call.name, error=f"{type(e).__name__}: {e}")
            return error_result(f"工具 {call.name} 执行异常: {type(e).__name__}: {e}")
        if runner.mocked:
            result.mocked = True
        return result

    async def aclose(self) -> None:
        """释放工具占用的资源（共享的 HTTP 客户端等）。"""
        for r in self._runners.values():
            await r.aclose()
        await self._http.aclose()

    async def __aenter__(self) -> ToolSet:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
