"""MCP 启动时自动拉起 ``evalp serve``（详设 12.1）。

- 服务已在运行（任意 HTTP 响应，包括 401）：直接复用，不拉起；
- 没有运行且地址是本机：以子进程启动 ``python -m evalp serve --host … --port …``，日志写到数据目录，
  然后轮询健康检查直到可用（超时或进程提前退出时报错，附日志末尾）；
- 子进程由本进程拥有：``close()`` 时先 SIGTERM（Windows 为 TerminateProcess）并等待，超时再杀整个进程树，
  最后 ``wait()`` 回收，不留僵尸进程；``atexit`` 也会兜底调用。
  与其他 MCP 进程共用同一个服务时，任何一个退出都可能带走服务——下次工具调用会再次自动拉起。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

import httpx

from evalp.auth import is_loopback
from evalp.client import EvalpClientError
from evalp.config import load_config
from evalp.runtime.procutil import kill_process_tree, popen_kwargs

STARTUP_TIMEOUT_S = 30.0
PROBE_TIMEOUT_S = 1.5
LOG_NAME = "mcp-autostart.log"


class AutostartError(EvalpClientError):
    """自动拉起服务失败（给用户的提示里包含日志位置）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CLIENT_AUTOSTART_FAILED")


def probe(base_url: str, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """服务是否在监听：任何 HTTP 响应都算在运行（带不带 token 都一样），连接失败才算没有。"""
    try:
        host = urlparse(base_url).hostname
        httpx.get(
            f"{base_url.rstrip('/')}/api/v1/health",
            timeout=timeout,
            trust_env=not is_loopback(host),
        )
        return True
    except httpx.HTTPError:
        return False


class ServerLauncher:
    """按需拉起并持有一个 ``evalp serve`` 子进程。

    ``command`` 用于测试替换启动命令（默认 ``[sys.executable, -m, evalp, serve, --host, H, --port, P]``）。
    """

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        startup_timeout: float = STARTUP_TIMEOUT_S,
        command: Sequence[str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.startup_timeout = startup_timeout
        self._command = list(command) if command else None
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_path: Path | None = None
        self._lock: asyncio.Lock | None = None
        self._atexit = False

    # -- 状态

    @property
    def started(self) -> bool:
        """本进程是否拉起过服务且它仍在运行。"""
        return self._proc is not None and self._proc.poll() is None

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    # -- 拉起

    def spawn_if_down(self, base_url: str) -> bool:
        """服务没有运行时（本机地址）启动子进程并立刻返回，不等待就绪。返回是否新启动了进程。"""
        if self.started or probe(base_url):
            return False
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        if not is_loopback(host):
            raise AutostartError(
                f"无法连接到 EvalP 服务（{base_url}），且它不是本机地址，不能自动启动。"
                "请先在服务所在机器运行 evalp serve。"
            )
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        cfg = load_config(self.data_dir)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = cfg.data_dir / LOG_NAME
        argv = self._command or [sys.executable, "-m", "evalp", "serve", "--host", host, "--port", str(port)]
        if self._command is None and self.data_dir is not None:
            argv += ["--data-dir", str(self.data_dir)]
        # stdin 必须断开、stdout/stderr 必须离开：MCP 的 stdio 就是协议通道
        with self._log_path.open("ab") as log:
            self._proc = subprocess.Popen(  # noqa: S603 - 命令完全由本模块构造
                argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **popen_kwargs()
            )
        if not self._atexit:
            atexit.register(self.close)
            self._atexit = True
        return True

    async def ensure(self, base_url: str) -> None:
        """保证服务可用：没有运行就拉起并等待健康检查通过。"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if await asyncio.to_thread(probe, base_url):
                return
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None  # 之前拉起的进程已退出（被杀 / 崩溃），重新来过
            await asyncio.to_thread(self.spawn_if_down, base_url)
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if await asyncio.to_thread(probe, base_url):
                    return
                if self._proc is not None and self._proc.poll() is not None:
                    raise AutostartError(
                        self._failure_text(f"evalp serve 启动后立即退出（退出码 {self._proc.returncode}）")
                    )
                await asyncio.sleep(0.2)
            raise AutostartError(
                self._failure_text(f"等待 evalp serve 就绪超过 {self.startup_timeout:.0f} 秒")
            )

    def _failure_text(self, reason: str) -> str:
        tail = ""
        if self._log_path is not None:
            with contextlib.suppress(OSError):
                tail = self._log_path.read_text(encoding="utf-8", errors="replace")[-800:].strip()
        text = f"自动启动 EvalP 服务失败：{reason}。可以手动运行 evalp serve 查看原因。"
        if self._log_path is not None:
            text += f"\n日志: {self._log_path}"
        if tail:
            text += f"\n{tail}"
        return text

    # -- 关闭

    def close(self, grace: float = 8.0) -> None:
        """停止自己拉起的服务并回收子进程；没有拉起过则什么都不做。可重复调用。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(grace)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid)
        with contextlib.suppress(Exception):
            proc.wait(5)  # 回收，避免僵尸进程
