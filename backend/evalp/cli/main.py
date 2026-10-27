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
from evalp.cli import adapter as adapter_cmd
from evalp.cli import agent as agent_cmd
from evalp.cli import asset as asset_cmd
from evalp.cli import chat as chat_cmd
from evalp.cli import dataset as dataset_cmd
from evalp.cli import eval as eval_cmd
from evalp.cli import gc as gc_cmd
from evalp.cli import init as init_cmd
from evalp.cli import mcp_cmd
from evalp.cli import replay as replay_cmd
from evalp.cli import serve as serve_cmd
from evalp.cli import versions as versions_cmd
from evalp.cli import workspace as workspace_cmd

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
    app.command("init")(init_cmd.init)
    app.command("push")(workspace_cmd.push)
    app.command("pull")(workspace_cmd.pull)
    app.command("publish")(workspace_cmd.publish)
    app.command("versions")(versions_cmd.versions)
    app.command("diff")(versions_cmd.diff)
    app.command("chat")(chat_cmd.chat)
    app.add_typer(agent_cmd.app, name="agent")
    app.add_typer(asset_cmd.app, name="asset")
    app.add_typer(dataset_cmd.app, name="dataset")
    app.add_typer(eval_cmd.app, name="eval")
    app.command("replay")(replay_cmd.replay)
    app.add_typer(adapter_cmd.app, name="adapter")
    app.command("mcp")(mcp_cmd.mcp)
    app.command("gc")(gc_cmd.gc)
    # --- 后续阶段在此处追加


register_commands()

if __name__ == "__main__":
    app()
