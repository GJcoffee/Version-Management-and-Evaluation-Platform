"""evalp gc：清理无用数据。"""

from __future__ import annotations

from typing import Any

from evalp import actions
from evalp.cli.asset import human_size
from evalp.cli.common import DataDirOpt, cli_errors, console, open_client, run


@cli_errors
def gc(data_dir: DataDirOpt = None) -> None:
    """清理无用数据：无引用的 blob、已软删除资产的无引用修订，以及超过保留期的评测会话。"""
    project = actions.project_or_none()

    async def go() -> dict[str, Any]:
        async with open_client(project, data_dir) as c:
            return await c.post("/system/gc")

    out = run(go())
    console.print(
        f"[green]清理完成[/green]：删除 {out.get('blobs_deleted', 0)} 个 blob，"
        f"释放 {human_size(out.get('bytes_freed', 0))}"
    )
    n = out.get("eval_sessions_deleted", 0)
    if n:
        console.print(f"  删除了 {n} 个超过保留期的评测会话")
