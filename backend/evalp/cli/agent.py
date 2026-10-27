"""evalp agent conventions show / set：agent 的发布规范（存在服务端，只能通过这里或网页 agent 设置修改）。"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.markup import escape

from evalp import actions, render
from evalp.cli.common import AgentOpt, DataDirOpt, agent_name, cli_errors, console, open_client, run
from evalp.spec.refs import ASSET_KINDS

app = typer.Typer(help="agent 设置", no_args_is_help=True)
conventions_app = typer.Typer(help="发布规范：查看与修改（服务端对所有入口强制）", no_args_is_help=True)
app.add_typer(conventions_app, name="conventions")


def _print(name: str, conv: dict[str, Any]) -> None:
    console.print(f"[bold]{escape(name)}[/bold] 的发布规范")
    for line in render.conventions_lines(conv):
        console.print(escape(line), highlight=False)


@conventions_app.command("show")
@cli_errors
def conventions_show(agent: AgentOpt = None, data_dir: DataDirOpt = None) -> None:
    """显示 agent 当前生效的发布规范。"""
    project = actions.project_or_none()
    name = agent_name(project, agent)

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await c.get(f"/agents/{name}")

    _print(name, run(go())["conventions"])


@conventions_app.command("set")
@cli_errors
def conventions_set(
    tag_from_param: Annotated[
        str | None,
        typer.Option(
            "--tag-from-param",
            help="params 里的参数名（如 hiagent_version）：发布必须带标签 v{参数值}；传空字符串关闭",
        ),
    ] = None,
    require_notes: Annotated[
        bool | None, typer.Option("--require-notes/--no-require-notes", help="发布说明必填")
    ] = None,
    require_source_id: Annotated[
        str | None,
        typer.Option(
            "--require-source-id",
            help="逗号分隔的资产类型（如 skill,mcp）：快照 map 里这些类型的规则必须配置 id；传空字符串关闭",
        ),
    ] = None,
    snapshot_readonly: Annotated[
        bool | None,
        typer.Option("--snapshot-readonly/--no-snapshot-readonly", help="来自快照的资产只允许快照推送写入"),
    ] = None,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """修改发布规范；只改给出的项，其余保持不变。"""
    if (
        tag_from_param is None
        and require_notes is None
        and require_source_id is None
        and snapshot_readonly is None
    ):
        raise typer.BadParameter("请至少指定一项要修改的规范（见 --help）。")
    kinds = [k.strip() for k in (require_source_id or "").split(",") if k.strip()]
    bad = [k for k in kinds if k not in ASSET_KINDS]
    if bad:
        raise typer.BadParameter(
            f"未知的资产类型 {', '.join(bad)}，可选: {', '.join(ASSET_KINDS)}",
            param_hint="--require-source-id",
        )
    project = actions.project_or_none()
    name = agent_name(project, agent)

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            conv = (await c.get(f"/agents/{name}"))["conventions"]
            if tag_from_param is not None:
                conv["tag_from_param"] = tag_from_param.strip() or None
            if require_notes is not None:
                conv["require_notes"] = require_notes
            if require_source_id is not None:
                conv["require_source_id"] = kinds
            if snapshot_readonly is not None:
                conv["snapshot_readonly"] = snapshot_readonly
            return await c.patch(f"/agents/{name}", {"conventions": conv})

    console.print("[green]已更新[/green]")
    _print(name, run(go())["conventions"])
