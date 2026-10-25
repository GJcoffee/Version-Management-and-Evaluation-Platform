"""子进程辅助：启动参数与进程树终止（Python 工具、SubprocessAdapter、Python 评估器共用）。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def popen_kwargs() -> dict[str, Any]:
    """跨平台的子进程创建参数：Windows 新建进程组，POSIX 新建会话，便于整树终止。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _alive(p: psutil.Process) -> bool:
    """进程是否仍在运行（僵尸进程视为已结束）。"""
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_process_tree(pid: int, *, timeout: float = 3.0) -> int:
    """杀掉 pid 及其全部后代进程，返回被杀进程数。进程已不存在时静默返回。

    POSIX 上子进程以新会话启动（``popen_kwargs``），还会对整个进程组发 SIGKILL，
    以覆盖已脱离父子关系的孤儿进程。
    """
    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess:
        return 0
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            p.kill()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(p) for p in procs):
        time.sleep(0.01)
    return sum(1 for p in procs if not _alive(p))


async def terminate_process(proc: asyncio.subprocess.Process) -> None:
    """终止 asyncio 子进程及其整个进程树，并回收。"""
    if proc.returncode is None:
        await asyncio.to_thread(kill_process_tree, proc.pid)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.shield(proc.wait())


_shim_lock = threading.Lock()
_shim_dir: str | None = None


def toolhost_shim_dir() -> str:
    """生成只含 ``evalp/toolhost.py`` 的临时目录（每个平台进程一份）。

    用户指定的 Python 解释器未必安装了 evalp；把这个目录放进它的 PYTHONPATH，就能
    ``-m evalp.toolhost``，又不会把平台自己的 site-packages 泄漏进去、遮蔽用户环境里的依赖。
    """
    global _shim_dir
    with _shim_lock:
        if _shim_dir is None:
            import evalp.toolhost as host

            src = Path(host.__file__).read_bytes()
            digest = hashlib.sha256(src).hexdigest()[:12]
            root = Path(tempfile.gettempdir()) / f"evalp-toolhost-{digest}"
            pkg = root / "evalp"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text('"""toolhost shim"""\n', encoding="utf-8")
            target = pkg / "toolhost.py"
            if not target.exists() or target.read_bytes() != src:
                tmp = pkg / f".toolhost.{os.getpid()}.tmp"
                tmp.write_bytes(src)
                os.replace(tmp, target)
            _shim_dir = str(root)
        return _shim_dir


def toolhost_command(
    entry: str, *, python: str | None = None, workdir: str | None = None
) -> tuple[list[str], dict[str, str], str | None]:
    """构造运行 ``python -m evalp.toolhost --entry …`` 所需的 (argv, env, cwd)。"""
    exe = python or sys.executable
    argv = [exe, "-m", "evalp.toolhost", "--entry", entry]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if python and os.path.abspath(python) != os.path.abspath(sys.executable):
        shim = toolhost_shim_dir()
        env["PYTHONPATH"] = shim + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cwd: str | None = None
    if workdir:
        argv += ["--workdir", workdir]
        if os.path.isdir(workdir):
            cwd = workdir
    return argv, env, cwd
