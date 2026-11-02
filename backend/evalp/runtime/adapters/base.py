"""Adapter 基类、RunContext 与工厂函数（详设 6.3）。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from evalp.runtime.events import AgentEvent
from evalp.runtime.llm.ratelimit import PRIORITY_INTERACTIVE
from evalp.runtime.messages import Message
from evalp.spec.models import CodeSpec, ConfigSpec

if TYPE_CHECKING:
    from evalp.context import AppContext
    from evalp.services.version import ResolvedVersion


@dataclass(eq=False)
class RunContext:
    """一轮执行的上下文（Runner 与 Adapter 共用）。

    - ``app``：AppContext（ConfigAdapter 通过它取 LLM、资产）；
    - ``version``：已固化的版本（``ResolvedVersion``）；
    - ``variables``：会话级变量与单轮变量合并后的结果，会覆盖 Spec 中的 ``prompts.variables``；
    - ``tool_mocks``：评测用例里的工具 mock，``{工具名: [{match?, output|default}, ...]}``；
    - ``deadline``：``time.monotonic()`` 时间点，Runner 到期后终止本轮；
    - ``cancel``：被置位时 Runner 终止本轮；
    - ``priority``：LLM 限流优先级（对话 > 重放 > 评测）；
    - ``new_messages``：Adapter 在执行过程中追加本轮新增的 assistant / tool 消息（供下一轮拼接历史）；
      Adapter 没有填写时，Runner 退化为 ``[final]``；
    - ``late_observe``：Adapter 声明本轮的规范指标来自远端、需要在轮次结束后异步补齐时设置。它返回事件流
      （llm.request / llm.response / tool.result / metrics 等），由会话服务在后台消费并以该轮的 turn_id 追加到
      事件流；补齐前本轮的用时等规范指标为空，``usage.trace_status`` 为 ``pending``。
    """

    session_id: str
    turn_id: str
    version: ResolvedVersion
    app: AppContext
    seed: int | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    tool_mocks: dict[str, Any] | None = None
    deadline: float = float("inf")
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    priority: int = PRIORITY_INTERACTIVE
    new_messages: list[Message] = field(default_factory=list)
    late_observe: Callable[[], AsyncIterator[AgentEvent]] | None = None


class HealthResult(BaseModel):
    """健康检查结果。"""

    ok: bool
    message: str = ""
    latency_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AgentAdapter(ABC):
    """所有 Adapter 的基类。

    ``run_turn`` 是异步生成器：产出事件（不含 seq / turn_id，由 Runner 与 EventSink 补齐）。
    Adapter 可以不产出 ``turn.start`` / ``turn.end``（Runner 会补齐）；出错时可以产出 ``error`` 事件，
    也可以直接抛异常（Runner 会转成 ``error`` 事件）。取消 / 超时时生成器会收到 CancelledError 或被
    ``aclose()``，Adapter 必须在 ``finally`` 中释放资源（连接、子进程、LLM 流）。
    """

    @abstractmethod
    def run_turn(
        self, ctx: RunContext, history: list[Message], user_input: Message
    ) -> AsyncIterator[AgentEvent]:
        """执行一轮，产出事件流。"""

    async def healthcheck(self) -> HealthResult:
        """健康检查；默认认为健康。"""
        return HealthResult(ok=True)

    async def aclose(self) -> None:  # noqa: B027 - 默认无资源可释放
        """释放 Adapter 持有的资源。"""


def build_adapter(version: ResolvedVersion, app: AppContext) -> AgentAdapter:
    """构造 Adapter：运行快照带接入配置 -> HiAgentAdapter；否则 config -> ConfigAdapter，
    code + http / subprocess -> 对应 Adapter。"""
    from evalp.runtime.adapters.config_adapter import ConfigAdapter
    from evalp.runtime.adapters.http_adapter import HttpAdapter
    from evalp.runtime.adapters.subprocess_adapter import SubprocessAdapter

    binding = (version.adapter_snapshot or {}).get("binding")
    if binding:
        from evalp.runtime.adapters.hiagent_adapter import HiAgentAdapter
        from evalp.runtime.adapters.registry import validate_binding

        return HiAgentAdapter(validate_binding(binding), app)
    spec = version.spec_model()
    if isinstance(spec, ConfigSpec):
        return ConfigAdapter(spec, app)
    assert isinstance(spec, CodeSpec)
    if spec.transport == "http":
        return HttpAdapter(spec)
    return SubprocessAdapter(spec)
