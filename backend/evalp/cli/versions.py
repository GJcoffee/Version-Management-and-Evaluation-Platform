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


@cli_errors
def versions(
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="最多显示多少个版本")] = 20,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """列出 agent 的版本（最新在前）。"""
    project = actions.project_or_none()
    name = agent_name(project, agent)

    async def go():
        async with open_client(project, data_dir) as c:
            return await c.get(f"/agents/{name}/versions", params={"limit": limit})

    items = run(go())["items"]
    if not items:
        console.print(f"{name} 还没有已发布的版本。运行 [bold]evalp publish[/bold] 发布第一个版本。")
        return
    table = Table(title=f"{name} 的版本", show_lines=False)
    for col in ("版本", "标签", "时间", "来源", "备注", "hash"):
        table.add_column(col)
    for v in items:
        table.add_row(
            f"v{v['seq']}",
            ", ".join(v.get("tags") or []),
            fmt_local(v.get("created_at")),
            v.get("created_via") or "",
            escape(v.get("notes") or ""),
            (v.get("spec_hash") or "")[:10],
        )
    console.print(table)


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
