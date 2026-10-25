"""工具执行结果。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

MAX_TEXT_CHARS = 16 * 1024  # 详设 6.4：超过 16K 字符截断
TRUNCATED_MARK = "[truncated]"


class ToolResult(BaseModel):
    """一次工具调用的结果。output 可以是字符串或任意 JSON 值。"""

    output: Any = None
    is_error: bool = False
    latency_ms: int = 0
    mocked: bool = False
    detail: dict[str, Any] | None = (
        None  # 调试信息（如 Python 工具的 traceback / stderr），不进入给模型的文本
    )

    def as_text(self) -> str:
        """转成给模型看的文本：str 原样，其他用 ensure_ascii=False 的 JSON；超长截断并标注 [truncated]。"""
        out = self.output
        if isinstance(out, str):
            text = out
        elif out is None:
            text = ""
        else:
            try:
                text = json.dumps(out, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(out)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n" + TRUNCATED_MARK
        return text


def error_result(message: str, **detail: Any) -> ToolResult:
    """构造错误结果。"""
    return ToolResult(output=message, is_error=True, detail=detail or None)
