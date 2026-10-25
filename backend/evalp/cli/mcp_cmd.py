"""evalp mcp：启动 MCP Server（stdio）。工作目录就是 agent 项目目录。"""

from __future__ import annotations

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
) -> None:
    """启动 MCP Server（stdio 传输），供 Claude Code / Cursor 等客户端接入。"""
    from evalp.mcp.server import build_server

    # stdout 是协议通道，任何提示都不能写到 stdout
    build_server(data_dir=data_dir, project_dir=project_dir).run("stdio")
