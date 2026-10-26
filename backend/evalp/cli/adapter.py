"""evalp adapter check：对一个版本跑适配器协议自检。"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from evalp import actions, render
from evalp.cli.common import AgentOpt, DataDirOpt, agent_name, cli_errors, console, open_client, run
from evalp.cli.eval import Format, emit

app = typer.Typer(help="适配器：协议契约自检", no_args_is_help=True)

CHECK_TIMEOUT_S = 600.0


@app.command("check")
@cli_errors
def adapter_check(
    version: Annotated[
        str | None, typer.Option("--version", help="版本：v3、标签或版本 id（默认最新版本）")
    ] = None,
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.md,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """对版本跑协议自检（健康检查、单轮、多轮、超时、取消、事件合规）；有失败项时退出码为 1。

    自检会真实调用 agent（配置型会调用 LLM，产生少量费用），不写库、不产生会话。
    """
    project = actions.project_or_none()
    name = agent_name(project, agent)
    ref = actions.qualify_version(name, version or "latest")

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            with console.status("自检中……"):
                return await c.post(f"/versions/{ref}/adapter-check", timeout=CHECK_TIMEOUT_S)

    report = run(go())
    if fmt is Format.json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        emit(render.render_adapter_check(report))
    if not report.get("ok"):
        raise typer.Exit(1)
