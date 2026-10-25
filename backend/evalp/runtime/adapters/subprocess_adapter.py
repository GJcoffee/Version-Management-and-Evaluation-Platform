"""SubprocessAdapter：每轮启动一个进程调用代码型 agent（详设 6.6）。

- 把请求 JSON（同 ``evalp-agent/1``）写入 stdin 后关闭；stdout 逐行读取 NDJSON 事件（L2），
  或整个 stdout 是一个 ``{"output": …}`` JSON 对象（L1，允许多行排版）；
- 非事件的 stdout 行（调试输出）转为 warn 级别的 ``log`` 事件；stderr 按行转为 warn 级别的 ``log`` 事件；
- 超时 / 取消 / 生成器被关闭时，终止整个进程树（``runtime/procutil``）；
- 环境变量：继承平台进程的环境，叠加 ``subprocess.env``（支持 ``${env:NAME}``），并注入
  ``EVALP_SESSION_ID`` 与 ``EVALP_TURN_ID``；
- ``cwd`` 为相对路径时以 ``source.repo`` 为基准（``repo`` 本身为相对路径时相对服务进程的当前目录）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from evalp.logging import get_logger
from evalp.runtime.adapters.base import AgentAdapter, HealthResult, RunContext
from evalp.runtime.adapters.protocol import (
    ProtocolError,
    build_request,
    event_from_wire,
    l1_events,
    protocol_error_event,
)
from evalp.runtime.events import ALL_TYPES, AgentEvent, ev
from evalp.runtime.messages import Message
from evalp.runtime.procutil import popen_kwargs, terminate_process
from evalp.spec.models import CodeSpec
from evalp.utils.envref import resolve_env, resolve_env_deep

log = get_logger(__name__)

STREAM_LIMIT = 64 * 1024 * 1024  # 单行输出上限
MAX_LOG_EVENTS = 500  # 每轮最多转成事件的 stderr / 噪声行数，超出后只保留尾部用于报错
LOG_LINE_LIMIT = 4000
EXIT_WAIT_S = 5.0
END_GRACE_S = 2.0  # 收到 agent 的 turn.end 后，等待管道关闭（收齐 stderr）的最长时间
TAIL_LINES = 20


class _StdoutParser:
    """逐行解析 stdout：识别 L2 事件、L1 响应与调试输出。"""

    def __init__(self, user_input: Message, ctx: RunContext) -> None:
        self.user_input = user_input
        self.ctx = ctx
        self.events_mode = False
        self.l1: Any = None
        self.noise: list[str] = []
        self.lineno = 0
        self.saw_final = False

    def feed(self, line: str) -> list[AgentEvent]:
        self.lineno += 1
        text = line.strip()
        if not text:
            return []
        try:
            obj = json.loads(text)
        except ValueError:
            return self._noise(line)
        if isinstance(obj, dict) and "type" in obj:
            if obj["type"] not in ALL_TYPES:
                raise ProtocolError(
                    f"stdout 第 {self.lineno} 行: 未知的事件类型 {obj['type']!r}",
                    line=self.lineno,
                    allowed=sorted(ALL_TYPES),
                )
            try:
                event = event_from_wire(obj, user_input=self.user_input)
            except ProtocolError as e:
                e.message = f"stdout 第 {self.lineno} 行: {e.message}"
                e.details.setdefault("line", self.lineno)
                e.details.setdefault("excerpt", text[:200])
                raise
            out = self._flush_noise()
            self.events_mode = True
            if event.type in ("message.assistant", "turn.end"):
                self.saw_final = True
            out.append(event)
            return out
        if isinstance(obj, dict) and "output" in obj and not self.events_mode:
            self.l1 = obj
            return []
        return self._noise(line)

    def _noise(self, line: str) -> list[AgentEvent]:
        if self.events_mode:
            return [self._log(line)]
        self.noise.append(line)
        return []

    @staticmethod
    def _log(line: str) -> AgentEvent:
        return ev("log", level="warn", message=f"[stdout] {line.rstrip()[:LOG_LINE_LIMIT]}")

    def _flush_noise(self) -> list[AgentEvent]:
        out = [self._log(ln) for ln in self.noise[:MAX_LOG_EVENTS] if ln.strip()]
        self.noise.clear()
        return out

    def finish(self) -> list[AgentEvent]:
        """stdout 结束：产出 L1 事件，或报告协议违例。"""
        if self.events_mode:
            return self._flush_noise()
        obj = self.l1
        if obj is None and self.noise:
            joined = "".join(self.noise)
            try:
                obj = json.loads(joined)
            except ValueError:
                raise ProtocolError(
                    'stdout 既不是事件流（每行一个 JSON 事件）也不是 {"output": …} 对象',
                    excerpt=joined.strip()[:200],
                ) from None
        if obj is None:
            raise ProtocolError("进程没有向 stdout 输出任何内容")
        events = l1_events(obj, self.ctx)
        self.saw_final = True
        return events


class SubprocessAdapter(AgentAdapter):
    """子进程代码型 agent。"""

    def __init__(self, spec: CodeSpec) -> None:
        assert spec.subprocess is not None
        self.spec = spec
        self.cfg = spec.subprocess
        self.last_pid: int | None = None  # 最近一次启动的进程 pid（协议检查用它确认进程树已被清理）

    # ------------------------------------------------------------ 命令 / 环境

    def resolve_cwd(self) -> Path:
        """工作目录：相对路径以 ``source.repo`` 为基准。"""
        cwd = Path(resolve_env(self.cfg.cwd)).expanduser()
        if not cwd.is_absolute():
            cwd = Path(resolve_env(self.spec.source.repo)).expanduser() / cwd
        return cwd.resolve()

    def resolve_command(self) -> list[str]:
        """命令行；``python`` / ``python3`` 在 PATH 中找不到时退化为平台自身的解释器。"""
        cmd = [resolve_env(c) for c in self.cfg.command]
        if cmd[0] in ("python", "python3") and shutil.which(cmd[0]) is None:
            cmd[0] = sys.executable
        return cmd

    def build_env(self, ctx: RunContext) -> dict[str, str]:
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in resolve_env_deep(dict(self.cfg.env)).items()})
        env.update(
            {
                "EVALP_SESSION_ID": ctx.session_id,
                "EVALP_TURN_ID": ctx.turn_id,
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    # ------------------------------------------------------------ 执行

    async def run_turn(
        self, ctx: RunContext, history: list[Message], user_input: Message
    ) -> AsyncIterator[AgentEvent]:
        cwd = self.resolve_cwd()
        if not cwd.is_dir():
            yield self._fatal("AGENT_START_FAILED", f"工作目录不存在: {cwd}（subprocess.cwd / source.repo）")
            return
        command = self.resolve_command()
        env = self.build_env(ctx)
        payload = json.dumps(build_request(ctx, self.spec, history, user_input), ensure_ascii=False) + "\n"
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=env,
                limit=STREAM_LIMIT,
                **popen_kwargs(),
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
            yield self._fatal("AGENT_START_FAILED", f"无法启动命令 {command[0]!r}: {e}")
            return
        self.last_pid = proc.pid

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._pump(proc.stdout, "out", queue)),
            asyncio.create_task(self._pump(proc.stderr, "err", queue)),
            asyncio.create_task(self._feed(proc, payload)),
        ]
        parser = _StdoutParser(user_input, ctx)
        stderr_tail: deque[str] = deque(maxlen=TAIL_LINES)
        stderr_events = 0
        open_streams = 2
        deferred_end: AgentEvent | None = None  # turn.end 推迟到管道关闭后再产出，避免丢掉随后到达的 stderr
        grace_expired = False
        try:
            while open_streams:
                try:
                    if deferred_end is None:
                        kind, data = await queue.get()
                    else:
                        kind, data = await asyncio.wait_for(queue.get(), END_GRACE_S)
                except TimeoutError:
                    grace_expired = True
                    break
                if kind == "eof":
                    open_streams -= 1
                elif kind == "err":
                    line = data.rstrip()
                    stderr_tail.append(line)
                    if stderr_events < MAX_LOG_EVENTS:
                        stderr_events += 1
                        yield ev("log", level="warn", message=line[:LOG_LINE_LIMIT], source="stderr")
                    elif stderr_events == MAX_LOG_EVENTS:
                        stderr_events += 1
                        yield ev(
                            "log", level="warn", message="stderr 输出过多，后续行不再记录", source="stderr"
                        )
                elif kind == "out":
                    for event in parser.feed(data):
                        if event.type == "turn.end":
                            deferred_end = event
                        elif deferred_end is None:
                            yield event
                else:  # "out_error" / "err_error"：单行过长等读取错误
                    raise ProtocolError(data)
            if grace_expired:
                await terminate_process(proc)
                code = 0
            else:
                code = await self._wait_exit(proc)
            try:
                tail = parser.finish()
            except ProtocolError:
                if code != 0:  # 进程崩溃时，退出原因比"输出格式不对"更有用
                    yield self._exit_error(code, stderr_tail)
                    return
                raise
            if code != 0 and parser.saw_final:
                yield ev("log", level="warn", message=f"agent 进程已输出结果，但退出码为 {code}")
            for event in tail:
                yield event
            if deferred_end is not None:
                yield deferred_end
            elif code != 0 and not parser.saw_final:
                yield self._exit_error(code, stderr_tail)
        except ProtocolError as e:
            yield protocol_error_event(e, stderr_tail="\n".join(stderr_tail)[-1000:] or None)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if proc.returncode is None:
                await terminate_process(proc)  # 超时 / 取消 / 提前关闭：杀掉整个进程树

    @staticmethod
    def _fatal(code: str, message: str) -> AgentEvent:
        return ev("error", code=code, message=message, retriable=False)

    @staticmethod
    def _exit_error(code: int, tail: deque[str]) -> AgentEvent:
        detail = "\n".join(tail)[-1000:]
        msg = f"agent 进程异常退出（退出码 {code}），且没有输出最终回复"
        if detail:
            msg += f"。stderr 末尾:\n{detail}"
        return ev("error", code="AGENT_ERROR", message=msg, retriable=False, exit_code=code)

    async def _wait_exit(self, proc: asyncio.subprocess.Process) -> int:
        """管道关闭后等待进程退出；迟迟不退出的杀掉。"""
        try:
            return await asyncio.wait_for(proc.wait(), EXIT_WAIT_S)
        except TimeoutError:
            await terminate_process(proc)
            return proc.returncode if proc.returncode is not None else -9

    @staticmethod
    async def _pump(stream: asyncio.StreamReader | None, kind: str, queue: asyncio.Queue) -> None:
        """把一个管道按行读入队列，结束时放入 eof。"""
        name = "stdout" if kind == "out" else "stderr"
        try:
            if stream is not None:
                while True:
                    try:
                        line = await stream.readline()
                    except ValueError:  # 单行超过 STREAM_LIMIT
                        await queue.put(
                            (f"{kind}_error", f"{name} 单行输出超过 {STREAM_LIMIT // (1024 * 1024)}MB")
                        )
                        break
                    if not line:
                        break
                    await queue.put((kind, line.decode("utf-8", "replace")))
        finally:
            await queue.put(("eof", kind))

    @staticmethod
    async def _feed(proc: asyncio.subprocess.Process, payload: str) -> None:
        """写入请求后关闭 stdin；进程提前退出（管道已断）时忽略。"""
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(payload.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with contextlib.suppress(Exception):
                proc.stdin.close()

    # ------------------------------------------------------------ 健康检查

    async def healthcheck(self) -> HealthResult:
        """检查命令可执行、工作目录存在（不启动进程）。"""
        start = time.perf_counter()
        cwd = self.resolve_cwd()
        command = self.resolve_command()
        details: dict[str, Any] = {"command": command, "cwd": str(cwd)}
        if not cwd.is_dir():
            return HealthResult(ok=False, message=f"工作目录不存在: {cwd}", details=details)
        exe = command[0]
        if os.sep in exe or (os.altsep and os.altsep in exe):
            p = Path(exe) if Path(exe).is_absolute() else cwd / exe
            found = str(p) if p.exists() and os.access(p, os.X_OK) else None
        else:
            found = shutil.which(exe)
        if found is None:
            return HealthResult(ok=False, message=f"找不到可执行文件: {exe}", details=details)
        details["executable"] = found
        return HealthResult(
            ok=True,
            message="命令可执行",
            latency_ms=int((time.perf_counter() - start) * 1000),
            details=details,
        )
