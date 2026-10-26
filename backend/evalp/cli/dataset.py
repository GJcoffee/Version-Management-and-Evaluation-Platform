"""evalp dataset import：把本地 JSONL / CSV 导入为数据集资产。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from evalp import actions
from evalp.cli.common import DataDirOpt, cli_errors, console, open_client, run

app = typer.Typer(help="数据集：导入", no_args_is_help=True)


@app.command("import")
@cli_errors
def dataset_import(
    file: Annotated[Path, typer.Argument(help="JSONL 或 CSV 文件")],
    name: Annotated[str, typer.Option("--name", help="数据集名称，如 cs-regression（同名则新增修订）")],
    column_map: Annotated[
        str | None,
        typer.Option("--map", help="CSV 列映射：字段=列名，逗号分隔，如 input=问题,expected=答案,tags=标签"),
    ] = None,
    fmt: Annotated[
        str | None, typer.Option("--format", help="文件格式 jsonl / csv（默认按扩展名判断）")
    ] = None,
    description: Annotated[str | None, typer.Option("--description", help="数据集描述")] = None,
    message: Annotated[str | None, typer.Option("-m", "--message", help="修订说明")] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """导入数据集：JSONL 每行一个用例；CSV 需要用 --map 指定 input 列。"""
    if fmt is not None and fmt not in ("jsonl", "csv"):
        raise typer.BadParameter("格式应是 jsonl 或 csv。", param_hint="--format")
    cmap = actions.parse_column_map(column_map)
    ds_name = name.removeprefix("dataset/")
    project = actions.project_or_none()

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await actions.import_dataset(
                c, ds_name, file, fmt=fmt, column_map=cmap, description=description, message=message
            )

    out = run(go())
    r = out["revision"]
    if out.get("created"):
        console.print(f"[green]已创建数据集[/green] {ds_name}  rev {r['rev']}，{out['case_count']} 个用例")
    elif out.get("existed"):
        console.print(
            f"[yellow]内容未变化[/yellow]，{ds_name} 沿用 rev {r['rev']}（{out['case_count']} 个用例）。"
        )
    else:
        console.print(f"[green]已新增修订[/green] {ds_name}  rev {r['rev']}，{out['case_count']} 个用例")
    console.print(f"  引用: dataset/{ds_name}@{r['rev']}")
    warnings = out.get("warnings") or []
    for w in warnings[:10]:
        where = f"第 {w['line']} 行: " if w.get("line") else ""
        console.print(f"  [yellow]警告[/yellow] {where}{escape(str(w.get('message') or w))}")
    if len(warnings) > 10:
        console.print(f"  [dim]……另有 {len(warnings) - 10} 条警告[/dim]")
