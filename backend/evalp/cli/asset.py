"""evalp asset ls / show / add / diff：资产管理。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from evalp import actions
from evalp.cli.common import DataDirOpt, cli_errors, console, err_console, open_client, run
from evalp.client import EvalpClient, EvalpClientError
from evalp.spec.refs import ASSET_KINDS
from evalp.utils.time import fmt_local
from evalp.workspace_client import WorkspaceError

app = typer.Typer(help="资产管理：列表、查看、上传、比较修订", no_args_is_help=True)

KINDS = ASSET_KINDS


def human_size(n: int | None) -> str:
    if n is None:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


@app.command("ls")
@cli_errors
def asset_ls(
    kind: Annotated[str | None, typer.Option("--kind", help=f"资产类型：{' / '.join(KINDS)}")] = None,
    q: Annotated[str | None, typer.Option("--q", help="按名称 / 描述模糊搜索")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="最多显示多少个资产")] = 50,
    data_dir: DataDirOpt = None,
) -> None:
    """列出资产（不含已删除的）。"""
    if kind is not None and kind not in KINDS:
        raise typer.BadParameter(f"kind 应是 {' / '.join(KINDS)} 之一。", param_hint="--kind")
    project = actions.project_or_none()

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await c.get("/assets", params={"kind": kind, "q": q, "limit": limit})

    page = run(go())
    items = page["items"]
    if not items:
        console.print("没有匹配的资产。")
        return
    table = Table(title="资产")
    for col in ("资产", "最新修订", "大小", "创建时间", "说明"):
        table.add_column(col)
    for a in items:
        latest = a.get("latest") or {}
        table.add_row(
            f"{a['kind']}/{a['name']}",
            f"rev {a['latest_rev']}",
            human_size(latest.get("size")),
            fmt_local(a.get("created_at")),
            escape(a.get("description") or ""),
        )
    console.print(table)
    if page.get("next_cursor"):
        console.print(f"[dim]只显示前 {limit} 个，可用 --kind / --q 缩小范围或调大 --limit。[/dim]")


@app.command("show")
@cli_errors
def asset_show(
    ref: Annotated[str, typer.Argument(help="资产：kind/name、kind/name@3、asset://… 或 ast_…")],
    rev: Annotated[int | None, typer.Option("--rev", min=1, help="修订号（默认最新）")] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """查看资产某个修订：基本信息写到 stderr，内容写到 stdout（可直接重定向到文件）。"""
    project = actions.project_or_none()
    _, ref_rev = actions.parse_asset_ref(ref)
    want = rev if rev is not None else ref_rev

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        async with open_client(project, data_dir) as c:
            asset = await actions.find_asset(c, ref)
            detail = await c.get(f"/assets/{asset['id']}/revisions/{want or 'latest'}")
            return asset, detail

    asset, d = run(go())
    head = (
        f"{d['kind']}/{d['name']}  rev {d['rev']}/{asset['latest_rev']}  "
        f"{human_size(d['size'])}  {d['content_type']}"
    )
    err_console.print(f"[bold]{escape(head)}[/bold]  [dim]{fmt_local(d['created_at'])}  {d['id']}[/dim]")
    if d.get("message"):
        err_console.print(f"[dim]修订说明: {escape(d['message'])}[/dim]")
    if d.get("content") is None:
        err_console.print(
            "[yellow]内容不是文本或超过 5MB，无法直接显示。[/yellow]"
            f"可在 Web UI 下载，或 GET /api/v1/assets/revisions/{d['id']}/raw。"
        )
        raise typer.Exit(1)
    sys.stdout.write(d["content"] if d["content"].endswith("\n") else d["content"] + "\n")


@app.command("add")
@cli_errors
def asset_add(
    ref: Annotated[str, typer.Argument(help="资产名称：kind/name，如 prompt/cs-system")],
    file: Annotated[Path, typer.Argument(help="要上传的本地文件")],
    message: Annotated[str | None, typer.Option("-m", "--message", help="修订说明")] = None,
    description: Annotated[str | None, typer.Option("--description", help="资产描述（新建时）")] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """上传文件：资产不存在就创建，已存在就新增修订（内容相同则不新增）。"""
    base, rev = actions.parse_asset_ref(ref)
    if rev is not None or base.startswith("ast_"):
        raise WorkspaceError("asset add 的资产名应写成 kind/name（不带修订号）。")
    kind, name = actions.split_kind_name(base)
    project = actions.project_or_none()

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await actions.upsert_asset(
                c, kind, name, path=file, message=message, description=description
            )

    out = run(go())
    r = out["revision"]
    if out.get("created"):
        console.print(f"[green]已创建[/green] {kind}/{name}  rev {r['rev']}  {human_size(r['size'])}")
    elif out.get("existed"):
        console.print(f"[yellow]内容未变化[/yellow]，{kind}/{name} 沿用 rev {r['rev']}。")
    else:
        console.print(f"[green]已新增修订[/green] {kind}/{name}  rev {r['rev']}  {human_size(r['size'])}")
    console.print(f"  引用: asset://{kind}/{name}@latest")


async def _revision_id(c: EvalpClient, asset: dict[str, Any], rev: int) -> str:
    return (await c.get(f"/assets/{asset['id']}/revisions/{rev}"))["id"]


def print_asset_diff(data: dict[str, Any]) -> None:
    """资产修订 diff 的终端输出：文本类按行着色，数据集给增删改统计。"""
    left, right = data["left"], data["right"]
    console.print(
        f"[bold]资产 diff[/bold]  {escape(str(left['name']))}@{left['rev']} -> "
        f"{escape(str(right['name']))}@{right['rev']}"
    )
    if data.get("identical"):
        console.print("[dim]两个修订的内容完全相同。[/dim]")
        return
    kind = data.get("type")
    if kind == "dataset":
        s = data.get("summary") or {}
        console.print(
            f"用例 {s.get('left_count')} -> {s.get('right_count')}："
            f"[green]新增 {s.get('added', 0)}[/green]，[red]删除 {s.get('removed', 0)}[/red]，"
            f"[yellow]修改 {s.get('modified', 0)}[/yellow]，未变 {s.get('unchanged', 0)}"
        )
        for key, color, sign in (("added", "green", "+"), ("removed", "red", "-")):
            for c in data.get(key) or []:
                console.print(f"[{color}]{sign} {escape(str(c['case_key']))}[/{color}]")
        for c in data.get("modified") or []:
            fields = ", ".join(c["changed_fields"])
            console.print(f"[yellow]~ {escape(str(c['case_key']))}[/yellow]  [dim]{fields}[/dim]")
        if data.get("truncated"):
            console.print("[dim]……明细已截断，完整差异请在 Web UI 查看。[/dim]")
    elif kind == "binary":
        sizes = data.get("sizes") or {}
        console.print(
            f"[dim]二进制内容，大小 {human_size(sizes.get('left'))} -> {human_size(sizes.get('right'))}[/dim]"
        )
    else:
        for line in ((data.get("content_diff") or {}).get("text") or "").splitlines():
            style = (
                "green"
                if line.startswith("+")
                else "red"
                if line.startswith("-")
                else "cyan"
                if line.startswith("@@")
                else "dim"
            )
            console.print(f"[{style}]{escape(line)}[/{style}]", highlight=False)


@app.command("diff")
@cli_errors
def asset_diff(
    ref: Annotated[str, typer.Argument(help="资产：kind/name 或 ast_…")],
    left: Annotated[int | None, typer.Option("--from", min=1, help="左侧修订号（默认右侧的上一个）")] = None,
    right: Annotated[int | None, typer.Option("--to", min=1, help="右侧修订号（默认最新）")] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """比较资产的两个修订（默认：最新修订与它的上一个）。"""
    project = actions.project_or_none()

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            asset = await actions.find_asset(c, ref)
            r = right or asset["latest_rev"]
            lft = left or r - 1
            if lft < 1:
                raise EvalpClientError(
                    f"{asset['kind']}/{asset['name']} 只有 1 个修订，没有可比较的。用 --from / --to 指定。",
                    code="CLIENT_INVALID",
                )
            return await c.get(
                "/assets/diff",
                params={"left": await _revision_id(c, asset, lft), "right": await _revision_id(c, asset, r)},
            )

    print_asset_diff(run(go()))
