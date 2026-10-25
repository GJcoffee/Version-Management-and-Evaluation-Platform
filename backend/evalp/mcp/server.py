"""EvalP MCP Server（详设 12，一期 9 个工具）。

- 传输：stdio；工作目录就是 agent 项目目录（读取 ``.evalp.toml``）；
- 返回内容面向 LLM 阅读：Markdown 摘要 + 关键 id + Web UI 链接，长内容截断并说明如何获取完整内容；
- 失败时抛 ``ToolError``（客户端会看到 isError=true 和中文原因），不会让进程退出。

``build_server`` 的 ``client_factory`` 用于测试注入（把客户端接到进程内的应用上）。
"""

import functools
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from evalp import actions, render
from evalp.client import EvalpClient, EvalpClientError
from evalp.workspace_client import (
    ProjectConfig,
    ProjectNotFound,
    PushConflict,
    PushResult,
    Workspace,
    WorkspaceError,
    find_project,
)

try:  # mcp 2.x：FastMCP 更名为 MCPServer
    from mcp.server.mcpserver import MCPServer as _FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _FastMCP  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

MAX_OUTPUT_CHARS = 14000
WAIT_TIMEOUT_S = 600.0  # run_eval 最多等待 10 分钟
CHAT_TIMEOUT_S = 600.0

INSTRUCTIONS = """\
EvalP：agent 的版本管理与评测平台。工作目录是 agent 项目目录（含 .evalp.toml）。
典型流程：修改本地 agent.yaml / prompts -> push_draft -> publish_version(tag) -> chat 试聊 ->
run_eval（baseline 对比）-> get_eval_report / get_eval_compare 查看失败与退化用例 -> 修改后重复。
需要先运行 `evalp serve` 启动服务。所有 agent 参数省略时取 .evalp.toml 中的 agent。"""


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n……（输出过长，已截断 {len(text) - limit} 字符。请缩小范围，或在 Web UI 查看完整内容。）"
    )


class Env:
    """一个 MCP 进程的运行环境：数据目录、项目目录、客户端工厂。"""

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        project_dir: str | os.PathLike[str] | None = None,
        client_factory: Callable[[ProjectConfig | None], EvalpClient] | None = None,
        poll_interval: float = 2.0,
        wait_timeout: float = WAIT_TIMEOUT_S,
    ) -> None:
        self.data_dir = data_dir
        self.project_dir = Path(project_dir) if project_dir else None
        self._factory = client_factory
        self.poll_interval = poll_interval
        self.wait_timeout = wait_timeout

    def project(self) -> ProjectConfig | None:
        """每次调用时重新读取（用户可能刚改过 .evalp.toml）。"""
        try:
            return find_project(self.project_dir)
        except ProjectNotFound:
            return None

    def require_project(self) -> ProjectConfig:
        p = self.project()
        if p is None:
            raise WorkspaceError(
                "当前工作目录不是 agent 项目（找不到 .evalp.toml）。"
                "请在项目目录启动 MCP Server，或先运行 evalp init。"
            )
        return p

    def agent_name(self, agent: str | None) -> str:
        if agent:
            return agent
        return self.require_project().agent_name

    @asynccontextmanager
    async def client(self, project: ProjectConfig | None) -> AsyncIterator[EvalpClient]:
        c = (
            self._factory(project)
            if self._factory
            else EvalpClient.create(project.server_url if project else None, self.data_dir)
        )
        try:
            yield c
        finally:
            await c.aclose()


def _guard(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """把客户端 / 工作区错误转成 ToolError，并限制输出长度。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return _clip(await fn(*args, **kwargs))
        except PushConflict as e:
            raise ToolError(_conflict_text(e)) from None
        except EvalpClientError as e:
            raise ToolError(e.friendly()) from None
        except WorkspaceError as e:
            raise ToolError(str(e)) from None

    return wrapper


def _conflict_text(e: PushConflict) -> str:
    lines = [
        f"草稿冲突：服务端草稿已被修改（当前 revision={e.current_revision}），可能有人在 Web UI 中编辑过。",
        "",
        "服务端（左）与本地（右）的差异：",
    ]
    changes = e.diff.get("changes") or []
    lines += [f"  {render.diff_change_lines(c)}" for c in changes[:30]]
    if len(changes) > 30:
        lines.append(f"  ……另有 {len(changes) - 30} 项")
    lines += [
        "",
        "处理方式：先调用 pull_draft 合并服务端的修改；确认要用本地覆盖时调用 push_draft(force=true)。",
    ]
    return "\n".join(lines)


def _validation_md(v: dict[str, Any] | None) -> list[str]:
    if v is None:
        return []
    out = ["- 草稿校验: " + ("通过" if v.get("ok") else "**未通过**（发布前需要修正）")]
    out += [f"  - 错误 `{e.get('path', '')}`: {e.get('message', '')}" for e in (v.get("errors") or [])[:10]]
    out += [f"  - 警告 `{w.get('path', '')}`: {w.get('message', '')}" for w in (v.get("warnings") or [])[:10]]
    return out


def _push_md(r: PushResult, web: str) -> list[str]:
    if r.unchanged:
        lines = ["## 草稿没有变化\n", f"- 草稿 `{r.agent_name}` 保持在 revision {r.revision}，无需推送。"]
    else:
        lines = [
            "## 已推送草稿\n",
            f"- agent: `{r.agent_name}`" + ("（本次新建）" if r.agent_created else ""),
            f"- 草稿 revision: {r.revision}（draft_id `{r.draft_id}`）",
        ]
        if r.uploaded:
            lines.append(f"- 上传了 {len(r.uploaded)} 个文件:")
            lines += [f"  - `{u['path']}` -> {u['asset']} rev {u.get('rev')}" for u in r.uploaded]
        else:
            lines.append("- 没有需要上传的文件")
        if r.skipped:
            lines.append(f"- 跳过 {len(r.skipped)} 个未变化的文件")
    lines += _validation_md(r.validation)
    if r.agent_id:
        lines.append(f"- Web UI: {web}/agents/{r.agent_id}")
    return lines


def build_server(
    data_dir: str | os.PathLike[str] | None = None,
    project_dir: str | os.PathLike[str] | None = None,
    client_factory: Callable[[ProjectConfig | None], EvalpClient] | None = None,
    poll_interval: float = 2.0,
    wait_timeout: float = WAIT_TIMEOUT_S,
) -> Any:
    """构造 MCP Server 并注册 9 个工具。"""
    env = Env(data_dir, project_dir, client_factory, poll_interval, wait_timeout)
    mcp = _FastMCP("evalp", instructions=INSTRUCTIONS)

    Agent = Annotated[str | None, Field(description="agent 名称；省略时取 .evalp.toml 中的 agent")]

    @mcp.tool()
    @_guard
    async def push_draft(
        force: Annotated[bool, Field(description="服务端草稿被改过（冲突）时，是否用本地内容覆盖")] = False,
    ) -> str:
        """把本地 agent.yaml 和有变化的 prompt / 工具 / 数据集文件推送到服务端草稿。

        返回上传了哪些文件、草稿 revision 和校验结果。
        """
        project = env.require_project()
        async with env.client(project) as c:
            r = await Workspace(project).push(c, force=force)
            return "\n".join(_push_md(r, c.base_url))

    @mcp.tool()
    @_guard
    async def pull_draft(
        force: Annotated[bool, Field(description="本地有未推送的修改时是否也覆盖")] = False,
    ) -> str:
        """拉取服务端草稿（例如有人在 Web UI 里改过），把资产内容写回本地文件，返回写回了哪些文件。"""
        project = env.require_project()
        async with env.client(project) as c:
            r = await Workspace(project).pull(c, force=force)
            lines = [f"## 已拉取草稿 `{r.agent_name}`（revision {r.revision}）\n"]
            lines += [f"- 更新 `{p}`" for p in r.written] or ["- 本地文件已是最新，没有写入任何文件"]
            if r.unchanged:
                lines.append(f"- {len(r.unchanged)} 个文件无变化")
            lines += [f"- 警告：服务端找不到资产 `{m}`" for m in r.missing]
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def publish_version(
        notes: Annotated[str | None, Field(description="版本备注，说明这次改了什么")] = None,
        tag: Annotated[str | None, Field(description="发布后指向该版本的标签，如 baseline、prod")] = None,
    ) -> str:
        """先 push 再把草稿发布为不可变版本。

        返回版本号、spec_hash、是否已存在（内容相同则不会新建）和相对父版本的变更摘要。
        """
        project = env.require_project()
        async with env.client(project) as c:
            r = await actions.publish(c, Workspace(project), notes=notes, tag=tag, via="mcp")
            v = r.version
            lines = list(_push_md(r.push, c.base_url))
            lines += [""]
            if r.existed:
                lines.append(f"## 版本已存在：v{v['seq']}\n")
                lines.append("草稿内容与已有版本完全相同，没有创建新版本。")
            else:
                lines.append(f"## 已发布版本 v{v['seq']}\n")
            lines += [f"- version_id: `{v['id']}`", f"- spec_hash: `{v['spec_hash'][:16]}`"]
            if notes:
                lines.append(f"- 备注: {notes}")
            if tag:
                lines.append(f"- 标签 `{tag}` 已指向 v{v['seq']}")
            d = r.parent_diff
            if d and d.get("changes"):
                lines.append(f"- 相对 v{d['left'].get('seq')} 的变更（{len(d['changes'])} 项）:")
                lines += [f"  - `{render.diff_change_lines(ch)}`" for ch in d["changes"][:15]]
                if len(d["changes"]) > 15:
                    lines.append("  - ……（更多变更请用 diff_versions 查看）")
            lines.append(f"- Web UI: {c.base_url}/agents/{v['agent_id']}")
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def list_versions(
        agent: Agent = None,
        limit: Annotated[int, Field(ge=1, le=100, description="最多返回多少个版本")] = 20,
    ) -> str:
        """列出 agent 已发布的版本（最新在前）：版本号、标签、时间、来源、备注和 id。"""
        name = env.agent_name(agent)
        async with env.client(env.project()) as c:
            page = await c.get(f"/agents/{name}/versions", params={"limit": limit})
            text = render.render_versions(name, page["items"])
            if page.get("next_cursor"):
                text += f"\n\n（只显示最新 {limit} 个，更多版本可调大 limit 或在 Web UI 查看。）"
            return text + f"\n\nWeb UI: {c.base_url}/agents/{name}"

    @mcp.tool()
    @_guard
    async def diff_versions(
        left: Annotated[str, Field(description="左侧版本：v12、标签（如 baseline）或版本 id")],
        right: Annotated[str, Field(description="右侧版本")],
        agent: Agent = None,
        max_lines: Annotated[int, Field(ge=10, le=1000, description="文本 diff 最多显示多少行")] = 200,
    ) -> str:
        """比较两个版本。

        返回变更列表（路径、操作、新旧值）加 prompt 等文本的 unified diff（默认截断到 200 行）。
        """
        project = env.project()
        name = agent or (project.agent_name if project else None)
        async with env.client(project) as c:
            data = await c.get(
                "/versions/diff",
                params={
                    "left": actions.qualify_version(name, left),
                    "right": actions.qualify_version(name, right),
                },
            )
            url = (
                f"{c.base_url}/agents/{name}/versions/diff?left={data['left']['id']}&right={data['right']['id']}"
                if name
                else None
            )
            return render.render_diff_md(data, max_lines=max_lines, web_url=url)

    @mcp.tool()
    @_guard
    async def chat(
        message: Annotated[str, Field(description="发给 agent 的用户消息")],
        version: Annotated[str | None, Field(description="版本：v3、标签或版本 id；省略时用最新版本")] = None,
        session_id: Annotated[
            str | None, Field(description="继续已有会话（上一次 chat 返回的 session_id）")
        ] = None,
        agent: Agent = None,
    ) -> str:
        """和 agent 的某个已发布版本对话一轮：返回回复内容、工具调用摘要和 session_id（继续对话时传回）。"""
        project = env.project()
        async with env.client(project) as c:
            ver_label = ""
            if session_id is None:
                session = await actions.create_session(c, env.agent_name(agent), version)
                session_id = session["id"]
                ver_label = f"v{session.get('version_seq')}"
            turn = await actions.chat_turn(c, session_id, message, timeout=CHAT_TIMEOUT_S)
            s = actions.summarize_turn(turn)
            lines = [f"## agent 回复{f'（{ver_label}）' if ver_label else ''}\n"]
            if s["status"] not in ("ok", None):
                err = s["error"] or {}
                lines.append(f"**本轮状态: {s['status']}**" + (f"：{err.get('message', '')}" if err else ""))
                lines.append("")
            lines.append(render.cut_block(s["text"], 4000) if s["text"] else "（没有文本回复）")
            if s["tool_calls"]:
                lines += ["", "工具调用:"]
                for tc in s["tool_calls"]:
                    lines.append(
                        f"- `{tc['name']}({render.truncate(tc['arguments'], 120)})`"
                        + (
                            f" -> {render.truncate(tc['result'], 160)}"
                            if tc.get("result") is not None
                            else ""
                        )
                    )
            meta = []
            if s["latency_ms"] is not None:
                meta.append(f"耗时 {s['latency_ms'] / 1000:.1f}s")
            u = s["usage"]
            if u.get("prompt_tokens") is not None:
                meta.append(f"tokens {u.get('prompt_tokens')}/{u.get('completion_tokens')}")
            lines += ["", f"- session_id: `{session_id}`（继续对话时传入 session_id）"]
            if meta:
                lines.append("- " + "，".join(meta))
            lines.append(f"- Web UI（含 trace）: {c.base_url}/sessions/{session_id}")
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def run_eval(
        dataset: Annotated[
            str, Field(description="数据集名称或引用，如 cs-regression、dataset/cs-regression@3")
        ],
        versions: Annotated[
            list[str] | None, Field(description="要评测的版本列表，如 ['v3','baseline']；省略时评测最新版本")
        ] = None,
        evaluators: Annotated[
            str | list[dict[str, Any]] | None,
            Field(
                description="评估器配置：本地文件路径（相对项目目录），或评估器对象列表；"
                "省略时用项目根目录的 evals.yaml"
            ),
        ] = None,
        repeats: Annotated[int | None, Field(ge=1, description="每个用例重复次数")] = None,
        baseline: Annotated[
            str | None, Field(description="基线版本（标签 / vN）；设置后每个版本都与它对比")
        ] = None,
        wait: Annotated[
            bool, Field(description="是否等待评测结束（最多 10 分钟）；false 时只返回 run id")
        ] = True,
        agent: Agent = None,
    ) -> str:
        """发起评测。

        wait=true（默认）时等到结束，返回报告摘要（失败用例）和与基线的对比摘要；
        否则返回 run id，稍后用 get_eval_report 查看。
        """
        project = env.project()
        ev = actions.load_evaluators(evaluators, project.root if project else Path.cwd())
        name = agent or (project.agent_name if project else None)
        async with env.client(project) as c:
            batch = await actions.create_eval(
                c,
                agent=name,
                dataset=dataset,
                evaluators=ev,
                versions=versions,
                baseline=baseline,
                repeats=repeats,
                via="mcp",
            )
            lines = ["## 评测已发起\n"]
            for rid, ref in zip(batch.run_ids, batch.version_refs, strict=True):
                lines.append(f"- `{rid}`（{ref}）: {c.base_url}/evals/{rid}")
            if not wait:
                lines += ["", "评测在后台运行。稍后调用 get_eval_report(run_id) 查看结果。"]
                return "\n".join(lines)
            runs, timed_out = await actions.wait_for_runs(
                c, batch.run_ids, interval=env.poll_interval, timeout=env.wait_timeout
            )
            if timed_out:
                lines += [
                    "",
                    f"**等待超过 {int(env.wait_timeout // 60)} 分钟，评测仍在运行**"
                    "（评测不会中断）。当前进度:",
                ]
                lines += [
                    f"- `{r['id']}`: {render.status_text(r.get('status'))}，{render.progress_text(r)}"
                    for r in runs
                ]
                lines.append("\n稍后调用 get_eval_report(run_id) 查看结果。")
                return "\n".join(lines)
            lines.append("")
            base_idx = batch.baseline_index
            for i, r in enumerate(runs):
                if i == base_idx:
                    lines += [render.render_run_summary(r, f"{c.base_url}/evals/{r['id']}"), ""]
                    continue
                items = await actions.fetch_report_items(c, r, limit=5)
                lines += [render.render_report(r, items, limit=5, web_url=f"{c.base_url}/evals/{r['id']}")]
            if base_idx is not None and len(runs) > 1:
                base = runs[base_idx]
                for i, r in enumerate(runs):
                    if i == base_idx:
                        continue
                    data = await actions.fetch_compare(c, base["id"], r["id"], limit=20)
                    details = await actions.regression_details(
                        c, base["id"], r["id"], render.compare_cases(data), n=3
                    )
                    url = f"{c.base_url}/evals/compare?left={base['id']}&right={r['id']}"
                    lines.append(render.render_compare(data, limit=5, web_url=url, details=details))
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def get_eval_report(
        run_id: Annotated[str, Field(description="评测 Run id（evr_…）")],
        failed_only: Annotated[
            bool, Field(description="true 只列未通过 / 执行失败的用例；false 同时列出其他用例")
        ] = True,
        limit: Annotated[int, Field(ge=1, le=50, description="最多列出多少条用例")] = 10,
    ) -> str:
        """评测报告：通过率等指标摘要，加未通过 / 低分 / 执行失败的用例。

        每条用例给出输入、期望、输出和评审理由（均已截断）。
        """
        async with env.client(env.project()) as c:
            r = await c.get(f"/evals/{run_id}")
            items = await actions.fetch_report_items(c, r, limit=limit, failed_only=failed_only)
            return render.render_report(r, items, limit=limit, web_url=f"{c.base_url}/evals/{run_id}")

    @mcp.tool()
    @_guard
    async def get_eval_compare(
        left: Annotated[str, Field(description="基线 Run id")],
        right: Annotated[str, Field(description="对比 Run id")],
        case_class: Annotated[
            str | None, Field(description="只看某一类用例：regressed / improved / unchanged / new / missing")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="最多列出多少条用例")] = 10,
    ) -> str:
        """对比两次评测。

        返回整体结论（Δ、置信区间、改善 / 退化计数）和退化用例明细（突出显示，附两侧输出和评审理由）。
        """
        async with env.client(env.project()) as c:
            data = await actions.fetch_compare(c, left, right, case_class=case_class, limit=max(limit, 50))
            details = await actions.regression_details(
                c, left, right, render.compare_cases(data), n=min(limit, 5)
            )
            url = f"{c.base_url}/evals/compare?left={left}&right={right}"
            return render.render_compare(
                data, limit=limit, web_url=url, case_class=case_class, details=details
            )

    return mcp
