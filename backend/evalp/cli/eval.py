"""evalp eval run / report / compare。"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markdown import Markdown
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from evalp import actions, render
from evalp.cli.common import AgentOpt, DataDirOpt, cli_errors, console, err_console, open_client, run
from evalp.client import EvalpClient

app = typer.Typer(help="评测：发起、报告、对比", no_args_is_help=True)


class Format(StrEnum):
    md = "md"
    json = "json"


def emit(text: str, as_markdown: bool = True) -> None:
    """输出 Markdown：终端里用 Rich 渲染，管道 / 重定向时输出原文。"""
    if as_markdown and console.is_terminal:
        console.print(Markdown(text))
    else:
        typer.echo(text)


def _label(run_: dict[str, Any]) -> str:
    return f"{run_['id']} {render.version_label(run_.get('version_seq'))}".strip()


@app.command("run")
@cli_errors
def eval_run(
    dataset: Annotated[
        str,
        typer.Option("--dataset", help="数据集名称或引用，如 cs-regression、dataset/cs-regression@3、arv_…"),
    ],
    versions: Annotated[
        str | None, typer.Option("--versions", help="逗号分隔的版本，如 v3,baseline（默认最新版本）")
    ] = None,
    evaluators: Annotated[
        Path | None, typer.Option("--evaluators", help="评估器配置文件（默认项目根目录的 evals.yaml）")
    ] = None,
    repeats: Annotated[
        int | None, typer.Option("--repeats", min=1, help="每个用例重复次数（默认取服务端设置）")
    ] = None,
    baseline: Annotated[
        str | None,
        typer.Option("--baseline", help="基线版本；多个版本对比时，基线在左侧（默认以第一个版本为基线）"),
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="评测名称")] = None,
    wait: Annotated[bool, typer.Option("--wait", help="等待评测结束，显示进度条和报告摘要")] = False,
    poll_interval: Annotated[float, typer.Option("--poll-interval", hidden=True)] = 1.0,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """发起评测：每个版本一个 Run，同属一个批次。"""
    project = actions.project_or_none()
    agent_ = agent or (project.agent_name if project else None)
    ev_list = actions.load_evaluators(evaluators, project.root if project else Path.cwd())
    refs = [v.strip() for v in versions.split(",") if v.strip()] if versions else None

    async def go() -> int:
        async with open_client(project, data_dir) as c:
            batch = await actions.create_eval(
                c,
                agent=agent_,
                dataset=dataset,
                evaluators=ev_list,
                versions=refs,
                baseline=baseline,
                repeats=repeats,
                name=name,
            )
            console.print(f"[green]已发起评测[/green] 批次 {batch.batch_id}")
            for rid, ref in zip(batch.run_ids, batch.version_refs, strict=True):
                console.print(f"  {rid}  {ref}   {c.web_url('/evals/' + rid)}")
            if batch.estimate:
                console.print(f"  [dim]预估: {json.dumps(batch.estimate, ensure_ascii=False)}[/dim]")
            if not wait:
                console.print(
                    f"\n稍后用 [bold]evalp eval report {batch.run_ids[0]}[/bold] 查看结果，或加 --wait 等待。"
                )
                return 0
            return await _wait_and_report(c, batch, poll_interval)

    code = run(go())
    if code:
        raise typer.Exit(code)


async def _wait_and_report(c: EvalpClient, batch: actions.EvalBatch, interval: float) -> int:
    """进度条等待所有 Run 结束，输出每个 Run 的摘要和（多版本时）对比摘要。"""
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("{task.fields[state]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    tasks: dict[str, TaskID] = {}

    def update(runs: list[dict[str, Any]]) -> None:
        for r in runs:
            p = r.get("progress") or {}
            total = p.get("total") or 0
            finished = min(total, (p.get("done") or 0) + (p.get("failed") or 0) + (p.get("cancelled") or 0))
            if r["status"] in render.TERMINAL_STATUSES and total:
                finished = total if r["status"] != "cancelled" else finished
            tid = tasks.get(r["id"])
            if tid is None:
                tid = tasks[r["id"]] = progress.add_task(_label(r), total=max(total, 1), state="")
            progress.update(
                tid, total=max(total, 1), completed=finished, state=render.status_text(r["status"])
            )

    try:
        with progress:
            runs, _ = await actions.wait_for_runs(c, batch.run_ids, interval=interval, on_update=update)
    except KeyboardInterrupt:
        err_console.print("\n已停止等待；评测仍在服务端继续运行。")
        return 130
    code = 0
    for r in runs:
        emit(render.render_run_summary(r, c.web_url(f"/evals/{r['id']}")))
        if r["status"] not in ("completed", "completed_with_errors"):
            code = 1
    if len(runs) > 1 and batch.baseline_index is not None:
        base = runs[batch.baseline_index]
        for i, r in enumerate(runs):
            if i == batch.baseline_index:
                continue
            data = await actions.fetch_compare(c, base["id"], r["id"], limit=20)
            cases = render.compare_cases(data)
            details = await actions.regression_details(c, base["id"], r["id"], cases, n=3)
            url = c.web_url(f"/evals/compare?left={base['id']}&right={r['id']}")
            emit(render.render_compare(data, limit=10, web_url=url, details=details))
    return code


@app.command("report")
@cli_errors
def eval_report(
    run_id: Annotated[str, typer.Argument(help="评测 Run id（evr_…）")],
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.md,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100, help="最多显示多少条失败 / 低分用例")] = 10,
    data_dir: DataDirOpt = None,
) -> None:
    """查看评测报告：指标摘要加未通过的用例。"""
    project = actions.project_or_none()

    async def go() -> tuple[dict[str, Any], dict[str, Any], str]:
        async with open_client(project, data_dir) as c:
            r = await c.get(f"/evals/{run_id}")
            items = await actions.fetch_report_items(c, r, limit=limit)
            return r, items, c.web_url(f"/evals/{run_id}")

    r, items, url = run(go())
    if fmt is Format.json:
        typer.echo(
            json.dumps(
                {"run": r, "failed": items["failed"], "errors": items["errors"]}, ensure_ascii=False, indent=2
            )
        )
        return
    emit(render.render_report(r, items, limit=limit, web_url=url))


@app.command("compare")
@cli_errors
def eval_compare(
    left: Annotated[str, typer.Argument(help="基线 Run id")],
    right: Annotated[str, typer.Argument(help="对比 Run id")],
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.md,
    case_class: Annotated[
        str | None,
        typer.Option("--class", help="只看某一类用例：regressed / improved / unchanged / new / missing"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="最多显示多少条用例")] = 10,
    threshold: Annotated[
        float | None, typer.Option("--threshold", min=0, help="|Δ| 小于该值视为不变（默认取服务端）")
    ] = None,
    data_dir: DataDirOpt = None,
) -> None:
    """对比两次评测：整体结论加逐用例的改善 / 退化。"""
    project = actions.project_or_none()

    async def go() -> tuple[dict[str, Any], dict[str, list[str]], str]:
        async with open_client(project, data_dir) as c:
            data = await actions.fetch_compare(
                c, left, right, case_class=case_class, limit=max(limit, 50), threshold=threshold
            )
            details = {}
            if fmt is Format.md:
                details = await actions.regression_details(c, left, right, render.compare_cases(data), n=3)
            return data, details, c.web_url(f"/evals/compare?left={left}&right={right}")

    data, details, url = run(go())
    if fmt is Format.json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    emit(render.render_compare(data, limit=limit, web_url=url, case_class=case_class, details=details))
