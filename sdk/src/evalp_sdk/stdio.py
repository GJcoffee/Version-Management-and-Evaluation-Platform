"""子进程模式：从 stdin 读取一个 ``evalp-agent/1`` 请求，向 stdout 逐行输出 NDJSON 事件。

默认会把进程的 fd 1 重定向到 stderr（事件改走一份复制出来的 stdout），这样 handler 里的 ``print`` 和
子进程输出不会污染协议；平台把 stderr 按行记为 warn 级别的 log 事件。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from typing import TYPE_CHECKING, Any

from .context import _now

if TYPE_CHECKING:
    from .agent import Agent


def run_stdio(agent: Agent, stdin: Any = None, stdout: Any = None) -> int:
    """执行一轮并返回退出码：0 表示已处理（成败都在事件里），2 表示请求本身无法读取。"""
    protect = stdout is None
    stdin = stdin if stdin is not None else sys.stdin
    lock = threading.Lock()
    if protect:
        out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
        os.dup2(2, 1)  # 直接写 fd 1 的输出也走 stderr
        sys.stdout = sys.stderr
    else:
        out = stdout

    def emit(event: dict[str, Any]) -> None:
        with lock:
            out.write(json.dumps(event, ensure_ascii=False) + "\n")
            out.flush()

    try:
        try:
            request = json.loads(stdin.read())
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
        except ValueError as e:
            emit(
                {
                    "type": "error",
                    "ts": _now(),
                    "payload": {
                        "code": "AGENT_ERROR",
                        "message": f"stdin 不是合法的请求: {e}",
                        "retriable": False,
                    },
                }
            )
            emit({"type": "turn.end", "ts": _now(), "payload": {"status": "error"}})
            return 2
        asyncio.run(agent.execute(request, emit))
        return 0
    finally:
        if protect:
            out.flush()
