"""ConfigAdapter：配置型 agent 的执行循环（详设 6.4）。

流程：渲染 system prompt（Jinja2 沙箱 + StrictUndefined）→ 循环流式调用 LLM → 有 tool_calls 时
并行 / 串行执行工具并把结果追加到消息里 → 没有 tool_calls 时输出 ``message.assistant`` 结束；
达到 ``max_steps`` 仍未结束则输出 ``MAX_STEPS_EXCEEDED`` 错误。

模板变量：``variables``（Spec 的 ``prompts.variables`` 被会话 / 单轮变量依次覆盖）、``knowledge[资产名]``
（``knowledge.inject`` 中资产的全文）、``now``（本地时区的 datetime）。为了与 Spec 校验保持一致（校验把
``prompts.variables`` 的键视为已定义），变量也可以直接写 ``{{ company }}``。
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from evalp.context import AppContext
from evalp.errors import EvalpError
from evalp.runtime.adapters.base import AgentAdapter, HealthResult, RunContext
from evalp.runtime.events import AgentEvent, ev
from evalp.runtime.llm.registry import LLMRegistry
from evalp.runtime.messages import Message, ToolCall
from evalp.runtime.tools.definition import ToolDefinition
from evalp.runtime.tools.executor import BuiltinTool, ToolSet
from evalp.runtime.tools.result import ToolResult
from evalp.services.asset import AssetService
from evalp.services.asset_content import decode_text, parse_tool_content
from evalp.spec.models import BuiltinTool as BuiltinToolSpec
from evalp.spec.models import ConfigSpec, ResolvedRef
from evalp.spec.refs import is_asset_ref

RESERVED_NAMES = frozenset({"variables", "knowledge", "now"})


@dataclass
class _Materials:
    """从资产加载出来的运行素材。"""

    system_template: str
    tools: list[ToolDefinition | BuiltinTool] = field(default_factory=list)
    knowledge: dict[str, str] = field(default_factory=dict)


def render_system_prompt(template: str, variables: dict[str, Any], knowledge: dict[str, str]) -> str:
    """用 Jinja2 沙箱渲染 system prompt；引用未定义的变量会抛 ``jinja2.UndefinedError``。"""
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    top = {k: v for k, v in variables.items() if isinstance(k, str) and k.isidentifier()}
    scope = {**top, "variables": variables, "knowledge": knowledge, "now": datetime.now().astimezone()}
    return env.from_string(template).render(scope)


class ConfigAdapter(AgentAdapter):
    """配置型 agent：Prompt + 工具 + 知识 + 模型。"""

    def __init__(self, spec: ConfigSpec, app: AppContext) -> None:
        self.spec = spec
        self.app = app
        self._materials: _Materials | None = None

    # ------------------------------------------------------------ 素材加载

    async def _read_ref(self, ref: ResolvedRef | str) -> tuple[str, str, str]:
        """读取资产引用，返回 (资产名, 内容哈希, 文本)。字符串引用（草稿写法）按当前修订解析。"""
        assets = self.app.svc(AssetService)
        if isinstance(ref, str):
            resolved = await assets.resolve_ref(ref)
            name, h = resolved.name, resolved.hash
        else:
            name, h = ref.asset.name, ref.asset.hash
        data = await self.app.blobs.get_bytes(h)
        return name, h, decode_text(data, f"资产 {name} 的内容")

    async def load(self) -> _Materials:
        """加载 system prompt、工具定义与注入的知识（结果缓存在 Adapter 上）。"""
        if self._materials is not None:
            return self._materials
        spec = self.spec
        system = spec.prompts.system
        if isinstance(system, ResolvedRef) or (isinstance(system, str) and is_asset_ref(system)):
            _, _, system_text = await self._read_ref(system)
        else:
            system_text = system
        tools: list[ToolDefinition | BuiltinTool] = []
        for item in spec.tools:
            if isinstance(item, BuiltinToolSpec):
                sources = []
                for src in item.with_.sources:
                    name, h, text = await self._read_ref(src)
                    sources.append((name, text, h))
                tools.append(BuiltinTool(name=item.builtin, sources=sources, top_k=item.with_.top_k))
            else:
                _, _, text = await self._read_ref(item)
                tools.append(parse_tool_content(text))
        knowledge: dict[str, str] = {}
        for ref in spec.knowledge.inject:
            name, _, text = await self._read_ref(ref)
            knowledge[name] = text
        self._materials = _Materials(system_text, tools, knowledge)
        return self._materials

    # ------------------------------------------------------------ 执行

    async def run_turn(
        self, ctx: RunContext, history: list[Message], user_input: Message
    ) -> AsyncIterator[AgentEvent]:
        spec = self.spec
        yield ev("turn.start", input=user_input, history_len=len(history))
        mats = await self.load()
        variables = {**spec.prompts.variables, **ctx.variables}
        try:
            system = render_system_prompt(mats.system_template, variables, mats.knowledge)
        except TemplateError as e:
            yield ev(
                "error",
                code="PROMPT_RENDER_ERROR",
                message=f"system prompt 渲染失败: {type(e).__name__}: {e}",
                retriable=False,
            )
            return

        messages: list[Message] = []
        if system.strip():
            messages.append(Message(role="system", content=system))
        messages.extend(history)
        messages.append(user_input)

        llm = self.app.svc(LLMRegistry).llm
        params = spec.model.params.model_dump(mode="json")
        counter = itertools.count(1)
        runtime = spec.runtime

        tools = ToolSet.build(mats.tools, mocks=ctx.tool_mocks)
        try:
            schemas = tools.schemas()
            for _ in range(runtime.max_steps):
                step = f"s{next(counter)}"
                yield ev(
                    "llm.request",
                    step_id=step,
                    provider=spec.model.provider,
                    model=spec.model.name,
                    params=params,
                    messages=messages,
                    tools=schemas or None,
                )
                stream = llm.stream_chat(
                    spec.model.provider,
                    spec.model.name,
                    messages,
                    schemas or None,
                    params,
                    priority=ctx.priority,
                )
                try:
                    async for chunk in stream:
                        yield ev(
                            "llm.delta", step_id=step, text=chunk.text, tool_call_delta=chunk.tool_call_delta
                        )
                finally:
                    await stream.aclose()  # 提前结束（取消 / 超时）时归还限流许可
                resp = stream.response
                yield ev("llm.response", step_id=step, **resp.event_fields())

                assistant = resp.to_message()
                messages.append(assistant)
                ctx.new_messages.append(assistant)
                if not resp.tool_calls:
                    yield ev("message.assistant", message=assistant)
                    return

                async for event in self._run_tools(
                    tools, resp.tool_calls, step, counter, messages, ctx, runtime.parallel_tool_calls
                ):
                    yield event
            yield ev(
                "error",
                code="MAX_STEPS_EXCEEDED",
                message=f"达到 max_steps={runtime.max_steps} 仍未得到最终回复",
                retriable=False,
            )
        finally:
            await tools.aclose()

    async def _run_tools(
        self,
        tools: ToolSet,
        calls: list[ToolCall],
        parent_step: str,
        counter: itertools.count[int],
        messages: list[Message],
        ctx: RunContext,
        parallel: bool,
    ) -> AsyncIterator[AgentEvent]:
        """执行一批工具调用：先产出全部 tool.call，随后每个工具完成就产出 tool.result；消息按调用顺序追加。"""
        timeout = self.spec.runtime.tool_timeout_s
        steps = [f"s{next(counter)}" for _ in calls]
        results: dict[int, ToolResult] = {}

        def result_event(i: int) -> AgentEvent:
            c, r = calls[i], results[i]
            return ev(
                "tool.result",
                step_id=steps[i],
                parent_step_id=parent_step,
                call_id=c.id,
                name=c.name,
                output=r.output,
                is_error=r.is_error,
                latency_ms=r.latency_ms,
                mocked=r.mocked or tools.is_mocked(c.name),
            )

        def call_event(i: int) -> AgentEvent:
            c = calls[i]
            return ev(
                "tool.call",
                step_id=steps[i],
                parent_step_id=parent_step,
                call_id=c.id,
                name=c.name,
                arguments=c.arguments,
            )

        if parallel:
            for i in range(len(calls)):
                yield call_event(i)
            tasks = {asyncio.create_task(tools.execute(c, timeout=timeout)): i for i, c in enumerate(calls)}
            try:
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for t in sorted(done, key=lambda t: tasks[t]):
                        results[tasks[t]] = t.result()
                        yield result_event(tasks[t])
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            for i, c in enumerate(calls):
                yield call_event(i)
                results[i] = await tools.execute(c, timeout=timeout)
                yield result_event(i)

        for i, c in enumerate(calls):
            msg = Message(role="tool", tool_call_id=c.id, content=results[i].as_text())
            messages.append(msg)
            ctx.new_messages.append(msg)

    # ------------------------------------------------------------ 健康检查

    async def healthcheck(self) -> HealthResult:
        """检查 Provider 是否已登记（不发起模型请求，避免产生费用）。"""
        try:
            handle = await self.app.svc(LLMRegistry).get(self.spec.model.provider)
        except EvalpError as e:
            return HealthResult(ok=False, message=e.message)
        return HealthResult(
            ok=True,
            message=f"Provider {handle.name} 可用",
            details={"provider": handle.name, "model": self.spec.model.name},
        )
