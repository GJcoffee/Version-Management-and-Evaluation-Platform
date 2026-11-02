"""evalp versions / diff。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from evalp import actions
from evalp.cli.common import AgentOpt, DataDirOpt, agent_name, cli_errors, console, open_client, run
from evalp.cli.diffview import print_diff
from evalp.utils.time import fmt_local


def _stage(v: dict) -> str:
    """阶段列：里程碑显示阶段名与归档数，已归档的版本标出已归档。"""
    if v.get("milestone"):
        title = escape(v.get("milestone_title") or "阶段性成果")
        return f"[bold yellow]★ {title}[/bold yellow]（已归档 {v.get('archived_count', 0)} 个版本）"
    if v.get("archived_into"):
        return "[dim]已归档[/dim]"
    return ""


@cli_errors
def versions(
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="最多显示多少个版本")] = 20,
    all_: Annotated[bool, typer.Option("--all", help="显示全部版本（含已归档到阶段性成果下的）")] = False,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """列出 agent 的版本（最新在前）；默认隐藏已归档的版本，阶段性成果标 ★。"""
    project = actions.project_or_none()
    name = agent_name(project, agent)
    params: dict = {"limit": limit}
    if not all_:
        params["archived"] = "false"

    async def go():
        async with open_client(project, data_dir) as c:
            return await c.get(f"/agents/{name}/versions", params=params)

    items = run(go())["items"]
    if not items:
        console.print(f"{name} 还没有已发布的版本。运行 [bold]evalp publish[/bold] 发布第一个版本。")
        return
    table = Table(title=f"{name} 的版本", show_lines=False)
    for col in ("版本", "标签", "阶段", "时间", "来源", "备注", "hash"):
        table.add_column(col)
    for v in items:
        table.add_row(
            f"v{v['seq']}",
            ", ".join(v.get("tags") or []),
            _stage(v),
            fmt_local(v.get("created_at")),
            v.get("created_via") or "",
            escape(v.get("notes") or ""),
            (v.get("spec_hash") or "")[:10],
        )
    console.print(table)
    hidden = sum(v.get("archived_count", 0) for v in items)
    if not all_ and hidden:
        console.print(f"[dim]另有 {hidden} 个已归档的版本未显示，加 --all 查看。[/dim]")


@cli_errors
def milestone(
    version: Annotated[str, typer.Argument(help="版本：v12、标签或版本 id")],
    title: Annotated[str | None, typer.Option("--title", help="阶段名，如“第一阶段：语气优化”")] = None,
    remove: Annotated[bool, typer.Option("--remove", help="取消阶段性成果，其下版本移出归档")] = False,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """把版本设为阶段性成果，并把它之前的过程版本归档到它名下（只影响列表展示）。"""
    project = actions.project_or_none()
    name = agent if agent else (project.agent_name if project else None)
    ref = actions.qualify_version(name, version)

    async def go():
        async with open_client(project, data_dir) as c:
            if remove:
                return await c.delete(f"/versions/{ref}/milestone")
            return await c.post(f"/versions/{ref}/milestone", {} if title is None else {"title": title})

    v = run(go())
    if remove:
        console.print(f"已取消 v{v['seq']} 的阶段性成果，其下归档的版本已全部移出归档。")
        return
    label = f"「{escape(v['milestone_title'])}」" if v.get("milestone_title") else ""
    console.print(f"v{v['seq']} 已设为阶段性成果{label}，其下共归档 {v['archived_count']} 个版本。")


@cli_errors
def diff(
    left: Annotated[str, typer.Argument(help="左侧版本：v12、标签、版本 id，或草稿 @main")],
    right: Annotated[str, typer.Argument(help="右侧版本（同左；@草稿名 取服务端已保存的草稿）")],
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """比较两个版本（彩色输出变更和文本 diff）。"""
    project = actions.project_or_none()
    name = agent if agent else (project.agent_name if project else None)
    l_ref, r_ref = actions.qualify_version(name, left), actions.qualify_version(name, right)

    async def go():
        async with open_client(project, data_dir) as c:
            return await c.get("/versions/diff", params={"left": l_ref, "right": r_ref})

    print_diff(console, run(go()))
