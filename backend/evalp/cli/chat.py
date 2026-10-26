"""evalp chat：终端交互式对话（订阅会话 SSE，流式显示回复）。"""

from __future__ import annotations

import asyncio
import json
import signal
from typing import Annotated, Any

import typer
from rich.markup import escape

from evalp import actions
from evalp.cli.common import AgentOpt, DataDirOpt, agent_name, cli_errors, console, open_client, run
from evalp.client import EvalpClient, EvalpClientError


class TurnPrinter:
    """把一轮的事件渲染到终端：文本流式输出，工具调用显示成简短的一行。"""

    def __init__(self) -> None:
        self.streamed = False  # 本轮是否收到过 llm.delta
        self.open_line = False  # 当前行有未换行的文本
        self.final_text = ""
        self.status: str | None = None
        self.footer = ""

    def _newline(self) -> None:
        if self.open_line:
            console.print()
            self.open_line = False

    def handle(self, ev: str, data: dict[str, Any]) -> None:
        payload = data.get("payload") or {}
        if ev == "llm.delta":
            text = payload.get("text")
            if text:
                self.streamed = True
                self.open_line = True
                console.print(text, end="", markup=False, highlight=False, soft_wrap=True)
        elif ev == "tool.call":
            self._newline()
            args = payload.get("arguments")
            args_text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            console.print(
                f"[dim]  -> 调用工具 {escape(str(payload.get('name')))}"
                f"({escape(_short(args_text, 80))})[/dim]"
            )
        elif ev == "tool.result":
            self._newline()
            mark = "[red]失败[/red]" if payload.get("is_error") else "完成"
            out = payload.get("output")
            out_text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            ms = payload.get("latency_ms")
            console.print(
                f"[dim]  <- {escape(str(payload.get('name')))} {mark}"
                f"{f' {ms}ms' if ms is not None else ''}: {escape(_short(out_text, 80))}[/dim]"
            )
        elif ev == "message.assistant":
            msg = payload.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            self.final_text = content or ""
        elif ev in ("agent.error", "error"):  # 旧版服务端的帧名是 error
            self._newline()
            console.print(
                f"[red]出错 {escape(str(payload.get('code', '')))}: "
                f"{escape(str(payload.get('message', '')))}[/red]"
            )
        elif ev == "turn.end":
            self.status = {"completed": "ok"}.get(payload.get("status"), payload.get("status"))
            usage = payload.get("usage") or {}
            bits = []
            if payload.get("latency_ms") is not None:
                bits.append(f"{payload['latency_ms'] / 1000:.1f}s")
            if usage.get("prompt_tokens") is not None:
                bits.append(f"tokens {usage.get('prompt_tokens')}/{usage.get('completion_tokens')}")
            if payload.get("cost"):
                bits.append(f"成本 {payload['cost']:.4f}")
            self.footer = " · ".join(bits)

    def finish(self) -> None:
        if not self.streamed and self.final_text:
            console.print(self.final_text, markup=False, highlight=False)
        else:
            self._newline()
        if self.status in ("cancelled",):
            console.print("[yellow]（本轮已取消）[/yellow]")
        elif self.status == "timeout":
            console.print("[yellow]（本轮超时）[/yellow]")
        if self.footer:
            console.print(f"[dim]{self.footer}[/dim]")


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def run_turn(client: EvalpClient, session_id: str, message: str) -> str | None:
    """发送一条消息并流式显示。Ctrl+C 取消本轮。返回本轮状态。"""
    accepted = await client.post(f"/sessions/{session_id}/turns", {"input": message})
    turn_id = accepted["turn_id"]
    printer = TurnPrinter()
    interrupted = asyncio.Event()
    loop = asyncio.get_running_loop()
    has_handler = False
    try:
        loop.add_signal_handler(signal.SIGINT, interrupted.set)
        has_handler = True
    except (NotImplementedError, RuntimeError, ValueError):
        pass  # Windows 等不支持时，退回 KeyboardInterrupt（见 chat 命令）

    async def consume() -> None:
        async for ev in client.stream(accepted["stream_url"], raw=True):
            if ev.event == "idle":
                return
            data = ev.data if isinstance(ev.data, dict) else {}
            if data.get("turn_id") not in (None, turn_id):
                continue
            printer.handle(ev.event, data)
            if ev.event == "turn.end":
                return

    task = asyncio.create_task(consume())
    waiter = asyncio.create_task(interrupted.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
        else:
            task.cancel()
            console.print("\n[yellow]正在中断本轮……[/yellow]")
            await client.post(f"/turns/{turn_id}/cancel")
            printer.status = "cancelled"
    except BaseException:
        task.cancel()
        raise
    finally:
        waiter.cancel()
        if has_handler:
            loop.remove_signal_handler(signal.SIGINT)
    if printer.status is None:  # 流提前结束（idle）：从会话里取最终状态
        detail = await client.get(f"/sessions/{session_id}", params={"include": "turns"})
        turn = next((t for t in detail.get("turns", []) if t["id"] == turn_id), None)
        if turn:
            s = actions.summarize_turn(turn)
            printer.final_text, printer.status = s["text"], turn.get("status")
    printer.finish()
    return printer.status


@cli_errors
def chat(
    version: Annotated[
        str | None, typer.Option("--version", help="版本：v3、标签或版本 id（默认最新版本）")
    ] = None,
    message: Annotated[str | None, typer.Option("-m", "--message", help="只发送这一条消息然后退出")] = None,
    agent: AgentOpt = None,
    data_dir: DataDirOpt = None,
) -> None:
    """和 agent 的某个已发布版本对话（输入 /exit 或 Ctrl+D 退出，Ctrl+C 中断当前这一轮）。"""
    from evalp.workspace_client import ProjectNotFound, find_project

    try:
        project = find_project()
    except ProjectNotFound:
        project = None
    name = agent_name(project, agent)

    async def start() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await actions.create_session(c, name, version)

    session = run(start())
    console.print(
        f"[bold]{escape(session.get('agent_name') or name)} v{session.get('version_seq')}[/bold]  "
        f"会话 {session['id']}  [dim](/exit 退出，Ctrl+C 中断当前回复)[/dim]"
    )

    def one(text: str) -> None:
        async def go() -> None:
            async with open_client(project, data_dir) as c:
                await run_turn(c, session["id"], text)

        try:
            run(go())
        except EvalpClientError as e:
            console.print(f"[red]{escape(e.friendly())}[/red]")
        except KeyboardInterrupt:  # 不支持信号处理器的平台
            console.print("\n[yellow]已中断本轮。[/yellow]")

    if message is not None:
        one(message)
        return
    while True:
        try:
            text = console.input("[bold cyan]你> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if text in ("/exit", "/quit", "exit", "quit"):
            return
        if text:
            one(text)
