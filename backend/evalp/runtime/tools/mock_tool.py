"""mock 工具（详设 6.9）：按 responses 顺序做参数子集匹配，都不匹配时用 default，再没有则报错。"""

from __future__ import annotations

from typing import Any

from evalp.runtime.tools.base import ToolRunner
from evalp.runtime.tools.definition import MockResponse
from evalp.runtime.tools.result import ToolResult, error_result


def subset_match(expected: Any, actual: Any) -> bool:
    """expected 是 actual 的子集：dict 递归按键比较，其他类型要求相等（布尔与数字不互等）。"""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset_match(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    return bool(expected == actual)


class MockTool(ToolRunner):
    """用预设响应代替真实实现。"""

    mocked = True

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        responses: list[MockResponse],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.responses = responses

    def lookup(self, args: dict[str, Any]) -> tuple[bool, Any]:
        """返回 (是否命中, 输出)：先按顺序找 match 命中的，其次用第一个 default。"""
        default: tuple[bool, Any] = (False, None)
        for r in self.responses:
            if r.match is not None:
                if subset_match(r.match, args):
                    return True, r.output
            elif "default" in r.model_fields_set:
                if not default[0]:
                    default = (True, r.default)
            else:  # 既无 match 也无 default：无条件匹配
                return True, r.output
        return default

    async def run(self, args: dict[str, Any], timeout: float | None) -> ToolResult:
        hit, output = self.lookup(args)
        if not hit:
            return error_result(f"mock 工具 {self.name} 没有匹配参数的响应，且未配置 default", args=args)
        return ToolResult(output=output, mocked=True)
