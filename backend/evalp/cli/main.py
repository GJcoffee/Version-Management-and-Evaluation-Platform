"""EvalP 命令行入口（``evalp``）。

给后续开发者：每个命令组一个模块（``evalp/cli/<组名>.py``），在模块内定义
``app = typer.Typer(help=...)``，然后在下面的 ``register_commands`` 中集中注册::

    from evalp.cli import eval as eval_cmd
    app.add_typer(eval_cmd.app, name="eval")          # 命令组：evalp eval ...
    app.command("gc")(some_module.gc)                 # 单个命令：evalp gc

命令内需要访问服务时调用 REST API（见 CLI 设计），不要直接打开数据库。
"""

from __future__ import annotations

import typer

from evalp import __version__
from evalp.cli import serve as serve_cmd

app = typer.Typer(
    name="evalp",
    help="版本管理与评测平台（EvalP）",
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"evalp {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="显示版本并退出"),
) -> None:
    """EvalP 命令行。"""


def register_commands() -> None:
    """集中注册子命令。后续阶段在此追加 add_typer / command 调用。"""
    app.command("serve")(serve_cmd.serve)
    # --- 后续阶段在此处追加，例如：
    # app.add_typer(agent_cmd.app, name="agent")


register_commands()

if __name__ == "__main__":
    app()
