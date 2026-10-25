"""python 工具（详设 6.9）：子进程执行 ``{python} -m evalp.toolhost --entry mod:func``。

stdin 传 JSON 参数，stdout 返回协议 JSON；超时（或被取消）时用 psutil 杀掉整个进程树。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from evalp.runtime.procutil import popen_kwargs, terminate_process, toolhost_command
from evalp.runtime.tools.base import ToolRunner
from evalp.runtime.tools.definition import PythonImpl, ToolDefinition
from evalp.runtime.tools.result import ToolResult, error_result

STDERR_TAIL_CHARS = 4000


async def run_toolhost(
    entry: str,
    args: Any,
    *,
    python: str | None,
    workdir: str | None,
    timeout: float | None,
) -> tuple[dict[str, Any] | None, str, str]:
    """运行一次 toolhost。返回 (协议响应或 None, stderr 文本, 失败原因)。

    失败原因非空表示未得到合法响应：``timeout`` / ``spawn: …`` / ``protocol: …`` / ``exit: N``。
    供 Python 工具与 Python 评估器共用。
    """
    argv, env, cwd = toolhost_command(entry, python=python, workdir=workdir)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **popen_kwargs(),
        )
    except (OSError, ValueError) as e:
        return None, "", f"spawn: {type(e).__name__}: {e}"
    payload = json.dumps(args, ensure_ascii=False, default=str).encode("utf-8")
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await proc.communicate(payload)
    except TimeoutError:
        await terminate_process(proc)
        return None, "", "timeout"
    except BaseException:  # 取消等：不留孤儿进程
        await terminate_process(proc)
        raise
    err_text = stderr.decode("utf-8", errors="replace")
    lines = [ln for ln in stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    if lines:
        try:
            resp = json.loads(lines[-1])
            if isinstance(resp, dict) and "ok" in resp:
                return resp, err_text, ""
        except ValueError:
            pass
    reason = f"exit: {proc.returncode}" if proc.returncode else "protocol: 子进程没有返回合法的结果 JSON"
    return None, err_text, reason


class PythonTool(ToolRunner):
    def __init__(self, definition: ToolDefinition) -> None:
        assert isinstance(definition.impl, PythonImpl)
        self.impl: PythonImpl = definition.impl
        self.name = definition.name
        self.description = definition.description
        self.parameters = definition.parameters
        self.timeout_s = self.impl.timeout_s

    async def run(self, args: dict[str, Any], timeout: float | None) -> ToolResult:
        eff = timeout or self.impl.timeout_s
        resp, stderr, failure = await run_toolhost(
            self.impl.entry, args, python=self.impl.python, workdir=self.impl.workdir, timeout=eff
        )
        tail = stderr[-STDERR_TAIL_CHARS:] if stderr else None
        if failure == "timeout":
            return error_result(f"Python 工具执行超时（{eff:g}s），进程已终止", stderr=tail)
        if resp is None:
            return error_result(
                f"Python 工具执行失败（{failure}）: {(tail or '').strip()[-500:]}", stderr=tail
            )
        if resp.get("ok"):
            return ToolResult(output=resp.get("result"), detail={"stderr": tail} if tail else None)
        err = resp.get("error") or {}
        return error_result(
            f"{err.get('type', 'Error')}: {err.get('message', '')}",
            traceback=err.get("traceback"),
            stderr=tail,
        )
