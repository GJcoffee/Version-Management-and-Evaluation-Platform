"""MCP 自动拉起 evalp serve：真实子进程的启动、复用、失败报告和清理。"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest
import respx
from evalp.client import EvalpClient
from evalp.mcp.autostart import AutostartError, ServerLauncher, probe
from evalp.mcp.server import build_server
from mcp import Client, StdioServerParameters

from tests.integration.p4.test_mcp import text


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(cond, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


def alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def test_spawns_serve_then_close_reaps_it(tmp_path):
    data, port = tmp_path / "data", free_port()
    url = f"http://127.0.0.1:{port}"
    launcher = ServerLauncher(data, startup_timeout=60)
    assert not probe(url)
    try:
        await launcher.ensure(url)
        assert probe(url) and launcher.started
        pid = launcher._proc.pid
        # 服务真的可用：token 已生成，健康检查通过
        async with EvalpClient.create(url, data) as c:
            assert (await c.get("/health"))["ok"] is True
        assert (data / "mcp-autostart.log").is_file()
        await launcher.ensure(url)  # 已在运行：不会再拉起第二个
        assert launcher._proc.pid == pid
    finally:
        launcher.close()
    assert not launcher.started and not alive(pid) and not probe(url)
    launcher.close()  # 可重复调用


async def test_reuses_running_server_and_does_not_own_it(tmp_path):
    data, port = tmp_path / "data", free_port()
    url = f"http://127.0.0.1:{port}"
    first = ServerLauncher(data, startup_timeout=60)
    try:
        await first.ensure(url)
        second = ServerLauncher(data, startup_timeout=60)
        assert second.spawn_if_down(url) is False
        await second.ensure(url)
        assert not second.started
        second.close()  # 没有拉起过：不会停掉别人的服务
        assert probe(url)
    finally:
        first.close()
    assert not probe(url)


async def test_remote_url_is_never_started(tmp_path):
    launcher = ServerLauncher(tmp_path / "data")
    with pytest.raises(AutostartError, match="不是本机地址"):
        await launcher.ensure("http://evalp.invalid:7860")
    assert not launcher.started


async def test_exit_during_startup_reports_exit_code_and_log(tmp_path):
    cmd = [sys.executable, "-c", "import sys; print('boom: 端口被占用'); sys.exit(3)"]
    launcher = ServerLauncher(tmp_path / "data", startup_timeout=20, command=cmd)
    with pytest.raises(AutostartError) as ei:
        await launcher.ensure(f"http://127.0.0.1:{free_port()}")
    msg = str(ei.value)
    assert "退出码 3" in msg and "boom: 端口被占用" in msg and "mcp-autostart.log" in msg
    assert "手动运行 evalp serve" in msg
    launcher.close()


async def test_startup_timeout_stops_the_child(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(120)"]
    launcher = ServerLauncher(tmp_path / "data", startup_timeout=1, command=cmd)
    try:
        with pytest.raises(AutostartError, match="超过 1 秒"):
            await launcher.ensure(f"http://127.0.0.1:{free_port()}")
        pid = launcher._proc.pid
        assert alive(pid)
    finally:
        launcher.close()
    assert not alive(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 信号语义")
async def test_close_kills_a_child_that_ignores_sigterm(tmp_path):
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
    launcher = ServerLauncher(tmp_path / "data", command=[sys.executable, "-c", code])
    launcher.spawn_if_down(f"http://127.0.0.1:{free_port()}")
    pid = launcher._proc.pid
    await asyncio.sleep(0.5)  # 等子进程装好信号处理器
    t0 = time.monotonic()
    launcher.close(grace=0.5)
    assert time.monotonic() - t0 < 10 and not alive(pid)


# ---------------------------------------------------------------- 接入 MCP


class FakeLauncher:
    def __init__(self, error: Exception | None = None) -> None:
        self.urls: list[str] = []
        self.error = error

    async def ensure(self, url: str) -> None:
        self.urls.append(url)
        if self.error:
            raise self.error


async def test_tool_calls_ensure_server_first(project_dir, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "token").write_text("tok", encoding="utf-8")
    launcher = FakeLauncher()
    server = build_server(data_dir=data, project_dir=project_dir, launcher=launcher)
    with respx.mock(base_url="http://test/api/v1") as api:
        api.get("/agents").mock(return_value=httpx.Response(200, json={"items": [], "next_cursor": None}))
        async with Client(server) as c:
            assert not (await c.call_tool("list_agents", {})).is_error
            assert not (await c.call_tool("list_agents", {})).is_error
    assert launcher.urls == ["http://test", "http://test"]  # 每次调用前都确认（服务中途退出会被重新拉起）

    failing = FakeLauncher(AutostartError("自动启动 EvalP 服务失败：端口被占用"))
    server = build_server(data_dir=data, project_dir=project_dir, launcher=failing)
    async with Client(server) as c:
        r = await c.call_tool("list_agents", {})
    assert r.is_error and "自动启动 EvalP 服务失败" in text(r)


def stdio_params(project: Path, data: Path, *extra: str) -> StdioServerParameters:
    import os

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "evalp", "mcp", *extra],
        cwd=str(project),
        env={"EVALP_DATA_DIR": str(data), "PATH": os.environ.get("PATH", "")},
    )


def local_project(project_dir: Path, port: int, extra_toml: str = "") -> Path:
    toml = project_dir / ".evalp.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("http://test", f"http://127.0.0.1:{port}") + extra_toml,
        encoding="utf-8",
    )
    return project_dir


async def test_evalp_mcp_autostarts_server_and_stops_it_on_exit(project_dir, tmp_path):
    port, data = free_port(), tmp_path / "data"
    url = f"http://127.0.0.1:{port}"
    local_project(project_dir, port)
    assert not probe(url)
    async with Client(stdio_params(project_dir, data)) as c:
        out = await c.call_tool("list_agents", {})
        assert not out.is_error and "还没有 agent" in text(out)
        assert probe(url)
    # MCP 退出（stdin 关闭）后，它拉起的服务一并停止，不留后台进程
    assert wait_until(lambda: not probe(url)), "MCP 退出后 evalp serve 仍在运行"


@pytest.mark.parametrize("how", ["flag", "toml"])
async def test_autostart_can_be_disabled(project_dir, tmp_path, how):
    port, data = free_port(), tmp_path / "data"
    url = f"http://127.0.0.1:{port}"
    local_project(project_dir, port, "\n[mcp]\nautostart = false\n" if how == "toml" else "")
    args = ("--no-autostart",) if how == "flag" else ()
    async with Client(stdio_params(project_dir, data, *args)) as c:
        r = await c.call_tool("list_agents", {})
        assert r.is_error and "evalp serve" in text(r)
        assert not probe(url)
