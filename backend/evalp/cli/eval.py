"""evalp eval run / status / report / compare / gate。"""

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

app = typer.Typer(help="评测：发起、状态、报告、对比、CI 门禁", no_args_is_help=True)


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
        str | None,
        typer.Option(
            "--dataset",
            help="数据集名称或引用，如 cs-regression、dataset/cs-regression@3、arv_…"
            "（默认取 .evalp.toml [eval].dataset）",
        ),
    ] = None,
    versions: Annotated[
        str | None,
        typer.Option(
            "--versions", help="逗号分隔的版本，如 v3,baseline（默认取 [eval].versions，再默认最新版本）"
        ),
    ] = None,
    evaluators: Annotated[
        Path | None,
        typer.Option(
            "--evaluators", help="评估器配置文件（默认取 [eval].evaluators，再默认项目根目录的 evals.yaml）"
        ),
    ] = None,
    repeats: Annotated[
        int | None,
        typer.Option("--repeats", min=1, help="每个用例重复次数（默认取 [eval].repeats，再取服务端设置）"),
    ] = None,
    concurrency: Annotated[
        int | None,
        typer.Option("--concurrency", min=1, max=512, help="每个评测的并发上限（默认取 [eval].concurrency）"),
    ] = None,
    baseline: Annotated[
        str | None,
        typer.Option(
            "--baseline",
            help="基线版本；多个版本对比时，基线在左侧（默认取 [eval].baseline，再以第一个版本为基线）",
        ),
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="评测名称")] = None,
    wait: Annotated[bool, typer.Option("--wait", help="等待评测结束，显示进度条和报告摘要")] = False,
    poll_interval: Annotated[float, typer.Option("--poll-interval", hidden=True)] = 1.0,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """发起评测：每个版本一个 Run，同属一个批次。

    --dataset / --evaluators / --repeats / --baseline / --versions / --concurrency 省略时，
    读取 .evalp.toml 的 [eval] 段；命令行参数优先。
    """
    project = actions.project_or_none()
    agent_ = agent or (project.agent_name if project else None)
    refs = [v.strip() for v in versions.split(",") if v.strip()] if versions else None
    args = actions.resolve_eval_args(
        project,
        dataset=dataset,
        versions=refs,
        evaluators=evaluators,
        repeats=repeats,
        baseline=baseline,
        concurrency=concurrency,
    )

    async def go() -> int:
        async with open_client(project, data_dir) as c:
            batch = await actions.create_eval(
                c,
                agent=agent_,
                dataset=args.dataset,
                evaluators=args.evaluators,
                versions=args.versions,
                baseline=args.baseline,
                repeats=args.repeats,
                concurrency=args.concurrency,
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


@app.command("status")
@cli_errors
def eval_status(
    run_id: Annotated[str, typer.Argument(help="评测 Run id（evr_…）")],
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.md,
    data_dir: DataDirOpt = None,
) -> None:
    """查看评测的运行状态：进度和预计剩余时间（用例明细用 evalp eval report）。"""
    project = actions.project_or_none()

    async def go() -> tuple[dict[str, Any], str]:
        async with open_client(project, data_dir) as c:
            return await c.get(f"/evals/{run_id}"), c.web_url(f"/evals/{run_id}")

    r, url = run(go())
    if fmt is Format.json:
        out = {"run": r, "eta_seconds": render.eta_seconds(r)}
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    emit(render.render_eval_status(r, url))


@app.command("gate")
@cli_errors
def eval_gate(
    run_id: Annotated[str | None, typer.Argument(help="被检查的评测 Run id（也可用 --run 指定）")] = None,
    run_opt: Annotated[str | None, typer.Option("--run", help="被检查的评测 Run id（同位置参数）")] = None,
    baseline: Annotated[
        str | None, typer.Option("--baseline", help="基线 Run id；指定后检查相对基线的退化用例数")
    ] = None,
    max_regressions: Annotated[
        int, typer.Option("--max-regressions", min=0, help="允许相对基线退化的用例数")
    ] = 0,
    min_pass_rate: Annotated[
        float | None,
        typer.Option("--min-pass-rate", min=0, max=1, help="最低通过率，0 到 1，如 0.9"),
    ] = None,
    threshold: Annotated[
        float | None, typer.Option("--threshold", min=0, help="|Δ| 小于该值视为不变（默认取服务端）")
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help="评测（和基线）还没结束时先等待")] = False,
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="--wait 最多等待多少秒（默认一直等）")
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="输出 JSON（等同 --format json）")] = False,
    fmt: Annotated[Format, typer.Option("--format", help="输出格式")] = Format.md,
    poll_interval: Annotated[float, typer.Option("--poll-interval", hidden=True)] = 2.0,
    data_dir: DataDirOpt = None,
) -> None:
    """CI 门禁：不满足条件时以退出码 1 结束（通过为 0）。

    条件：评测已成功结束；相对基线退化的用例数不超过 --max-regressions（需要 --baseline）；
    通过率不低于 --min-pass-rate。接口或连接错误同样以退出码 1 结束，并把原因写到 stderr。
    """
    if run_id and run_opt and run_id != run_opt:
        raise typer.BadParameter("位置参数与 --run 指定了不同的评测 id。")
    target = run_id or run_opt
    if not target:
        raise typer.BadParameter("请指定评测 Run id（位置参数或 --run）。")
    project = actions.project_or_none()

    async def go() -> tuple[dict[str, Any], str]:
        async with open_client(project, data_dir) as c:
            if wait:
                ids = [target, *([baseline] if baseline else [])]
                err_console.print("等待评测结束……")
                await actions.wait_for_runs(c, ids, interval=poll_interval, timeout=timeout)
            result = await c.get(
                f"/evals/{target}/gate",
                params={
                    "baseline": baseline,
                    "max_regressions": max_regressions,
                    "min_pass_rate": min_pass_rate,
                    "threshold": threshold,
                },
            )
            return result, c.web_url(f"/evals/{target}")

    result, url = run(go())
    if json_out or fmt is Format.json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        emit(render.render_gate(result, url))
    raise typer.Exit(0 if result.get("passed") else 1)
