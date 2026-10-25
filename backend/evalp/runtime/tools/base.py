"""工具运行器的公共基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from evalp.runtime.tools.result import ToolResult


class ToolRunner(ABC):
    """一个可执行的工具：带 OpenAI schema 与 run()。run() 不负责计时（由 ToolSet 统一计时）。"""

    name: str
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    timeout_s: float | None = None  # 工具自己的超时上限
    mocked: bool = False

    def schema(self) -> dict[str, Any]:
        """OpenAI tools 列表中的一项。"""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    @abstractmethod
    async def run(self, args: dict[str, Any], timeout: float | None) -> ToolResult:
        """执行工具。timeout 是有效超时秒数（已综合工具自身与调用方限制）。"""

    async def aclose(self) -> None:  # noqa: B027 - 默认无资源可释放，子类按需覆盖
        """释放资源。"""
