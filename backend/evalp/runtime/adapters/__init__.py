"""Adapter：把三类 agent（配置型 / HTTP / 子进程）统一成 ``run_turn()`` 事件流（详设 6.3）。

用法::

    adapter = build_adapter(resolved_version, app_ctx)
    async for event in adapter.run_turn(run_ctx, history, user_input):
        ...
    await adapter.aclose()

通常不要直接迭代 Adapter，而是交给 :class:`evalp.runtime.runner.Runner`（它负责事件顺序校验、超时、取消、
汇总用量并补齐 ``turn.end``）。
"""

from evalp.runtime.adapters.base import (
    AgentAdapter,
    HealthResult,
    RunContext,
    build_adapter,
)

__all__ = ["AgentAdapter", "HealthResult", "RunContext", "build_adapter"]
