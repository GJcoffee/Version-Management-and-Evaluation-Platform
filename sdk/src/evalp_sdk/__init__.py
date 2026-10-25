"""evalp-sdk：几行代码把你的 agent 接入 EvalP（``evalp-agent/1`` 协议）。

::

    from evalp_sdk import agent, TurnContext

    @agent
    async def handle(ctx: TurnContext):
        async with ctx.span("retrieve", query=ctx.input.text()) as s:
            docs = await retrieve(ctx.input.text())
            s.set(n=len(docs))
        return f"找到 {len(docs)} 条资料"          # 自动生成 message.assistant 与 turn.end

    if __name__ == "__main__":
        handle.serve_http(port=8001)               # 或 handle.run_stdio()

零第三方依赖，只用标准库。
"""

from .agent import PROTOCOL, Agent, agent
from .context import LLMCall, Span, ToolCall, TurnContext
from .messages import Message

__all__ = ["PROTOCOL", "Agent", "LLMCall", "Message", "Span", "ToolCall", "TurnContext", "agent"]
__version__ = "0.1.0"
