"""evalp replay：用另一个版本重放历史会话（锚定模式）。"""

from __future__ import annotations

from typing import Annotated

import typer

from evalp import actions, render
from evalp.cli.common import AgentOpt, DataDirOpt, cli_errors, console, err_console, open_client, run
from evalp.cli.eval import emit
from evalp.workspace_client import WorkspaceError


def _parse_turns(text: str | None) -> list[int] | None:
    if not text:
        return None
    try:
        return sorted({int(x) for x in text.replace("，", ",").split(",") if x.strip()})
    except ValueError:
        raise WorkspaceError(f"--turns 应是逗号分隔的轮次编号（从 0 开始），如 0,2,3: {text!r}") from None


@cli_errors
def replay(
    session_id: Annotated[str, typer.Argument(help="要重放的历史会话 id（ses_…）")],
    to: Annotated[str, typer.Option("--to", help="用哪个版本重放：v13、标签或版本 id")],
    mode: Annotated[str, typer.Option("--mode", help="重放模式（目前只支持 anchored）")] = "anchored",
    turns: Annotated[
        str | None, typer.Option("--turns", help="只重放这些轮次（从 0 开始，逗号分隔）；默认全部")
    ] = None,
    wait: Annotated[bool, typer.Option("--wait", help="等待重放结束并显示逐轮对比摘要")] = False,
    poll_interval: Annotated[float, typer.Option("--poll-interval", hidden=True)] = 1.0,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """用另一个版本重放历史会话：新会话的每一轮都以原会话的历史为上下文（锚定模式）。"""
    if mode != "anchored":
        raise typer.BadParameter("目前只支持 anchored（锚定模式）。", param_hint="--mode")
    project = actions.project_or_none()
    agent_ = agent or (project.agent_name if project else None)
    selected = _parse_turns(turns)

    async def go() -> int:
        async with open_client(project, data_dir) as c:
            out = await actions.replay_session(c, session_id, to, agent=agent_, turns=selected, mode=mode)
            new_id, compare_id = out["session_id"], out["compare_id"]
            console.print(f"[green]已创建重放[/green] 新会话 {new_id}（对比组 {compare_id}）")
            console.print(f"  {c.web_url('/compare/' + compare_id)}")
            if not wait:
                console.print("\n重放在后台执行：打开上面的链接查看，或加 --wait 等待结束并显示对比摘要。")
                return 0
            try:
                with console.status("重放中……"):
                    alignment, _ = await actions.wait_replay(c, compare_id, interval=poll_interval)
            except KeyboardInterrupt:
                err_console.print("\n已停止等待；重放仍在服务端继续运行。")
                return 130
            group = await c.get(f"/compare/{compare_id}")
            emit(render.render_alignment(group, alignment, web_url=c.web_url(f"/compare/{compare_id}")))
            return 0

    code = run(go())
    if code:
        raise typer.Exit(code)


__all__ = ["replay"]
