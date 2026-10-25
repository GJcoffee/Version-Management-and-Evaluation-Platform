"""CLI 公共部分：错误处理、客户端构造、Rich 控制台。"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from rich.console import Console

from evalp.client import EvalpClient, EvalpClientError
from evalp.workspace_client import ProjectConfig, WorkspaceError, find_project

console = Console()
err_console = Console(stderr=True)

DataDirOpt = Annotated[
    Path | None,
    typer.Option("--data-dir", help="数据目录（默认 ~/.evalp，或环境变量 EVALP_DATA_DIR），用于读取 token"),
]
AgentOpt = Annotated[
    str | None, typer.Option("--agent", help="agent 名称（默认取 .evalp.toml 中 agent 段的 name）")
]

T = TypeVar("T")


def cli_errors(fn: Callable[..., T]) -> Callable[..., T]:
    """统一处理客户端 / 工作区错误：打印中文提示并以退出码 1 结束。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except EvalpClientError as e:
            err_console.print("[bold red]错误[/bold red] " + _esc(e.friendly()), highlight=False)
            raise typer.Exit(1) from None
        except WorkspaceError as e:
            err_console.print("[bold red]错误[/bold red] " + _esc(str(e)), highlight=False)
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            err_console.print("\n已中断。")
            raise typer.Exit(130) from None

    return wrapper


def _esc(text: str) -> str:
    from rich.markup import escape

    return escape(text)


def run(coro: Coroutine[Any, Any, T]) -> T:
    """在新的事件循环中运行协程。"""
    return asyncio.run(coro)


def project_required() -> ProjectConfig:
    return find_project()


def open_client(project: ProjectConfig | None, data_dir: Path | None, **kw: Any) -> EvalpClient:
    """按 ``.evalp.toml`` / 数据目录构造客户端（token 缺失时抛 EvalpClientError）。"""
    return EvalpClient.create(project.server_url if project else None, data_dir, **kw)


def agent_name(project: ProjectConfig | None, agent: str | None) -> str:
    name = agent or (project.agent_name if project else None)
    if not name:
        raise WorkspaceError("请用 --agent 指定 agent，或在含 .evalp.toml 的目录中运行。")
    return name
