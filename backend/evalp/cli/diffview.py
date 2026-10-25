"""版本 diff 的终端彩色输出。"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markup import escape

from evalp.render import _ref_text, diff_side_label

OP_STYLE = {
    "add": ("+", "green"),
    "remove": ("-", "red"),
    "replace": ("~", "yellow"),
    "asset_changed": ("~", "cyan"),
}


def print_diff(console: Console, data: dict[str, Any], *, max_lines: int | None = None) -> None:
    """打印版本 diff：每个变更一行，文本 diff 按 +/-/@@ 着色。"""
    left, right = data.get("left") or {}, data.get("right") or {}
    changes = data.get("changes") or []
    console.print(
        f"[bold]版本 diff[/bold]  {escape(diff_side_label(left))} -> {escape(diff_side_label(right))}"
    )
    if not changes:
        console.print("[dim]两个版本的内容完全相同。[/dim]")
        return
    summary = data.get("summary") or {}
    console.print("[dim]" + "，".join(f"{k} {v}" for k, v in summary.items() if v) + "[/dim]")
    shown = 0
    for ch in changes:
        sign, color = OP_STYLE.get(ch.get("op"), ("?", "white"))
        path, op = escape(str(ch.get("path"))), ch.get("op")
        if op == "add":
            desc = f"[green]{escape(_ref_text(ch.get('right')))}[/green]"
        elif op == "remove":
            desc = f"[red]{escape(_ref_text(ch.get('left')))}[/red]"
        else:
            old, new = escape(_ref_text(ch.get("left"))), escape(_ref_text(ch.get("right")))
            desc = f"[red]{old}[/red] -> [green]{new}[/green]"
        console.print(f"[{color}]{sign}[/{color}] [bold]{path}[/bold]  [dim]({op})[/dim]  {desc}")
        text = ((ch.get("content_diff") or {}).get("text")) or ""
        for line in text.splitlines():
            if max_lines is not None and shown >= max_lines:
                console.print("    [dim]……（文本 diff 已截断）[/dim]")
                return
            shown += 1
            style = (
                "green"
                if line.startswith("+")
                else "red"
                if line.startswith("-")
                else "cyan"
                if line.startswith("@@")
                else "dim"
            )
            console.print(f"    [{style}]{escape(line)}[/{style}]", highlight=False)
