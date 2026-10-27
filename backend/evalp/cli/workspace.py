"""evalp push / pull / publish：工作区同步与发布。"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.markup import escape

from evalp import actions
from evalp.cli.common import DataDirOpt, cli_errors, console, err_console, open_client, project_required, run
from evalp.cli.diffview import print_diff
from evalp.workspace_client import PushConflict, PushResult, Workspace


def print_validation(v: dict[str, Any] | None) -> None:
    """打印草稿校验结果（错误红色、警告黄色）。"""
    if v is None:
        return
    if v.get("ok"):
        console.print("草稿校验: [green]通过[/green]")
    else:
        console.print("草稿校验: [red]未通过[/red]（发布前需要修正）")
    for e in v.get("errors") or []:
        console.print(
            f"  [red]错误[/red] {escape(str(e.get('path', '')))}: {escape(str(e.get('message', '')))}"
        )
    for w in v.get("warnings") or []:
        console.print(
            f"  [yellow]警告[/yellow] {escape(str(w.get('path', '')))}: {escape(str(w.get('message', '')))}"
        )


def print_push(r: PushResult) -> None:
    if r.unchanged:
        console.print(f"[green]没有变化[/green]，草稿 {r.agent_name} 保持在 revision {r.revision}。")
    else:
        head = "已创建 agent 并推送" if r.agent_created else "已推送"
        console.print(f"[green]{head}[/green] {r.agent_name}  草稿 revision {r.revision}")
        for u in r.uploaded:
            note = "（内容已存在）" if u.get("existed") else ""
            console.print(f"  上传 {escape(u['path'])} -> {u['asset']} rev {u.get('rev')}{note}")
        if r.skipped:
            console.print(f"  [dim]跳过 {len(r.skipped)} 个未变化的文件[/dim]")
    for u in [*r.uploaded, *r.skipped]:
        if u.get("renamed_from"):
            console.print(f"  [cyan]改名[/cyan] {u['renamed_from']} -> {u['asset']}（修订历史已保留）")
    print_validation(r.validation)


def print_conflict(e: PushConflict) -> None:
    err_console.print(
        f"[bold red]冲突[/bold red] 服务端草稿已被修改（当前 revision={e.current_revision}），"
        "可能有人在 Web UI 中编辑过。"
    )
    if e.diff.get("changes"):
        err_console.print("下面是服务端（左）与本地（右）的差异：")
        print_diff(err_console, e.diff, max_lines=80)
    err_console.print(
        "\n请执行 [bold]evalp pull[/bold] 合并服务端的修改，"
        "或 [bold]evalp push --force[/bold] 用本地覆盖服务端。"
    )


@cli_errors
def push(
    force: Annotated[bool, typer.Option("--force", help="忽略服务端的修改，用本地内容覆盖草稿")] = False,
    data_dir: DataDirOpt = None,
) -> None:
    """把本地 agent.yaml 和变化的文件推送到服务端草稿。"""
    project = project_required()

    async def go() -> PushResult:
        async with open_client(project, data_dir) as c:
            return await Workspace(project).push(c, force=force)

    try:
        print_push(run(go()))
    except PushConflict as e:
        print_conflict(e)
        raise typer.Exit(1) from None


@cli_errors
def pull(
    force: Annotated[bool, typer.Option("--force", help="本地有未推送的修改时也覆盖")] = False,
    data_dir: DataDirOpt = None,
) -> None:
    """拉取服务端草稿，把资产内容写回本地文件。"""
    project = project_required()

    async def go():
        async with open_client(project, data_dir) as c:
            return await Workspace(project).pull(c, force=force)

    r = run(go())
    console.print(f"[green]已拉取[/green] {r.agent_name}  草稿 revision {r.revision}")
    for path in r.written:
        console.print(f"  更新 {escape(path)}")
    if r.unchanged:
        console.print(f"  [dim]{len(r.unchanged)} 个文件无变化[/dim]")
    for key in r.missing:
        console.print(f"  [yellow]警告[/yellow] 服务端找不到资产 {key}")


@cli_errors
def publish(
    notes: Annotated[str | None, typer.Option("-m", "--notes", help="版本备注")] = None,
    tag: Annotated[str | None, typer.Option("--tag", help="发布后把该标签指向新版本，如 baseline")] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """先 push，再把草稿发布为不可变版本。"""
    project = project_required()

    async def go() -> actions.PublishResult:
        async with open_client(project, data_dir) as c:
            return await actions.publish(c, Workspace(project), notes=notes, tag=tag, via="cli")

    try:
        r = run(go())
    except PushConflict as e:
        print_conflict(e)
        raise typer.Exit(1) from None
    print_push(r.push)
    v = r.version
    if r.existed:
        console.print(f"[yellow]版本已存在[/yellow]：内容与 v{v['seq']} 完全相同，没有创建新版本。")
    else:
        console.print(f"[green]已发布版本 v{v['seq']}[/green]")
    console.print(f"  id {v['id']}  spec_hash {v['spec_hash'][:12]}")
    if tag:
        console.print(f"  标签 [bold]{escape(tag)}[/bold] 已指向 v{v['seq']}")
    if r.parent_diff and r.parent_diff.get("changes"):
        console.print(
            f"  相对 v{r.parent_diff['left'].get('seq')} 的变更: "
            f"{len(r.parent_diff['changes'])} 项（evalp diff 查看）"
        )
