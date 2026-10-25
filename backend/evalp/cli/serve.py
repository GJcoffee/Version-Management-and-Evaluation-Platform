"""evalp serve：启动 Web 服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel

from evalp.auth import is_loopback
from evalp.config import load_config

err_console = Console(stderr=True)


def serve(
    host: Annotated[str | None, typer.Option(help="监听地址（默认取配置，127.0.0.1）")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口（默认取配置，7860）")] = None,
    data_dir: Annotated[
        Path | None, typer.Option(help="数据目录（默认 ~/.evalp，或环境变量 EVALP_DATA_DIR）")
    ] = None,
) -> None:
    """启动 EvalP 服务（API + Web UI）。"""
    from evalp.app import create_app

    overrides: dict[str, dict[str, object]] = {}
    if host is not None:
        overrides.setdefault("server", {})["host"] = host
    if port is not None:
        overrides.setdefault("server", {})["port"] = port
    config = load_config(data_dir, overrides)
    bind_host, bind_port = config.server.host, config.server.port

    if not is_loopback(bind_host):
        err_console.print(
            Panel(
                f"服务将监听 [bold]{bind_host}[/bold]，局域网内的其他机器可以访问。\n"
                "所有 /api 请求都必须带 token（见数据目录下的 token 文件），请勿在不可信网络中暴露此服务。",
                title="[bold red]安全警告[/bold red]",
                border_style="red",
            )
        )

    shown = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    err_console.print(f"EvalP 数据目录: {config.data_dir}")
    err_console.print(f"Web UI: http://{shown}:{bind_port}    token 文件: {config.token_path}")
    uvicorn.run(create_app(config), host=bind_host, port=bind_port, log_level="info")
