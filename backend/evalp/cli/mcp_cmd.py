"""evalp mcp：启动 MCP Server（stdio）。工作目录就是 agent 项目目录。"""

from __future__ import annotations

import contextlib
import signal
import sys
from pathlib import Path
from typing import Annotated

import typer


def mcp(
    data_dir: Annotated[
        Path | None, typer.Option("--data-dir", help="数据目录（默认 ~/.evalp，或环境变量 EVALP_DATA_DIR）")
    ] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir", help="agent 项目目录（默认当前工作目录；客户端不在项目目录启动时使用）"
        ),
    ] = None,
    no_autostart: Annotated[
        bool,
        typer.Option(
            "--no-autostart",
            help="不要在服务未运行时自动启动 evalp serve（也可在 .evalp.toml 写 [mcp] autostart = false）",
        ),
    ] = False,
) -> None:
    """启动 MCP Server（stdio 传输），供 Claude Code / Cursor 等客户端接入。

    服务（evalp serve）没有运行时，默认在后台自动拉起，MCP 退出时一并停止。
    """
    from evalp.client import resolve_server_url
    from evalp.mcp.autostart import AutostartError, ServerLauncher
    from evalp.mcp.server import build_server
    from evalp.workspace_client import WorkspaceError, find_project

    launcher: ServerLauncher | None = None
    if not no_autostart:
        project = None
        with contextlib.suppress(WorkspaceError):  # 没有项目 / 配置有误时，工具调用会报告具体原因
            project = find_project(project_dir)
        if project is None or project.mcp_autostart:
            launcher = ServerLauncher(data_dir)
            with contextlib.suppress(AutostartError, OSError):  # 启动时尽早拉起；失败留到工具调用时报告
                launcher.spawn_if_down(resolve_server_url(project.server_url if project else None, data_dir))

    # 被 SIGTERM 终止时也要走 finally，停掉自己拉起的服务
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        # stdout 是协议通道，任何提示都不能写到 stdout
        build_server(data_dir=data_dir, project_dir=project_dir, launcher=launcher).run("stdio")
    finally:
        if launcher is not None:
            launcher.close()
