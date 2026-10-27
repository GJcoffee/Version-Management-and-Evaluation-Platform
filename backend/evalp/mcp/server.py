"""EvalP MCP Server（详设 12，20 个工具）。

- 传输：stdio；工作目录就是 agent 项目目录（读取 ``.evalp.toml``）；
- 服务没有运行时可自动拉起 ``evalp serve``（``launcher``，见 :mod:`evalp.mcp.autostart`）；
- 返回内容面向 LLM 阅读：Markdown 摘要 + 关键 id + Web UI 链接，长内容截断并说明如何获取完整内容；
- 失败时抛 ``ToolError``（客户端会看到 isError=true 和中文原因），不会让进程退出。

``build_server`` 的 ``client_factory`` 用于测试注入（把客户端接到进程内的应用上）。
"""

import functools
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field

from evalp import actions, render
from evalp.client import EvalpClient, EvalpClientError, resolve_server_url
from evalp.mcp.autostart import ServerLauncher
from evalp.spec.refs import AssetKindName
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
**发布、推送前先调用 get_conventions**：agent 的发布规范（标签怎么取、说明是否必填、快照 map 要不要配 id、
快照资产是否只读）由服务端对所有入口强制，违反时请求会被拒绝；被拒绝时把返回的中文说明照着改，不要绕过。
典型流程：修改本地 agent.yaml / prompts -> push_draft -> publish_version(tag) -> chat 试聊 ->
run_eval（baseline 对比）-> get_eval_report / get_eval_compare 查看失败与退化用例 -> 修改后重复。
其他：list_agents / get_draft / update_draft（直接改服务端草稿）、
list_assets / upsert_asset / import_dataset、
get_eval_status（评测进度）、get_session / replay_session / compare_sessions（会话查看、重放与对比）。
服务没有运行时会自动启动 `evalp serve`（可用 --no-autostart 关闭）。
所有 agent 参数省略时取 .evalp.toml 中的 agent；run_eval 的数据集等参数省略时取 .evalp.toml 的 [eval] 段。"""


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
        launcher: ServerLauncher | None = None,
    ) -> None:
        self.launcher = launcher
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

    def resolve_path(self, raw: str) -> Path:
        """工具参数里的本地文件路径：相对路径按项目目录解析，且必须位于项目目录之内。"""
        root = self.require_project().root.resolve()
        path = Path(raw)
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root):
            raise WorkspaceError(f"path 必须在项目目录内（{root}）: {raw}")
        return path

    @asynccontextmanager
    async def client(self, project: ProjectConfig | None) -> AsyncIterator[EvalpClient]:
        if self.launcher is not None and self._factory is None:
            await self.launcher.ensure(
                resolve_server_url(project.server_url if project else None, self.data_dir)
            )
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
        lines += [
            f"- 资产改名: `{u['renamed_from']}` -> `{u['asset']}`（按来源 ID 识别，修订历史已保留）"
            for u in [*r.uploaded, *r.skipped]
            if u.get("renamed_from")
        ]
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
    launcher: ServerLauncher | None = None,
) -> Any:
    """构造 MCP Server 并注册 20 个工具。``launcher`` 非空时，服务没有运行就先拉起（注入客户端时不用）。"""
    env = Env(data_dir, project_dir, client_factory, poll_interval, wait_timeout, launcher)
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
        """先 push 再把草稿发布为不可变版本。发布前先调用 get_conventions 了解 agent 的发布规范。

        标签与创建版本原子完成；违反规范（如缺少必填的 notes / tag）时返回服务端的中文说明。
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
        left: Annotated[str, Field(description="左侧版本：v12、标签（如 baseline）、版本 id，或草稿 @main")],
        right: Annotated[str, Field(description="右侧版本（同左；@草稿名 取服务端已保存的草稿）")],
        agent: Agent = None,
        max_lines: Annotated[int, Field(ge=10, le=1000, description="文本 diff 最多显示多少行")] = 200,
    ) -> str:
        """比较两个版本（或已保存的草稿，写 @main）。

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
            str | None,
            Field(
                description="数据集名称或引用，如 cs-regression、dataset/cs-regression@3；"
                "省略时取 .evalp.toml [eval].dataset"
            ),
        ] = None,
        versions: Annotated[
            list[str] | None, Field(description="要评测的版本列表，如 ['v3','baseline']；省略时评测最新版本")
        ] = None,
        evaluators: Annotated[
            str | list[dict[str, Any]] | None,
            Field(
                description="评估器配置：本地文件路径（相对项目目录），或评估器对象列表；"
                "省略时取 .evalp.toml [eval].evaluators，再省略则用项目根目录的 evals.yaml"
            ),
        ] = None,
        repeats: Annotated[
            int | None,
            Field(ge=1, description="每个用例重复次数；省略时取 [eval].repeats，再省略取服务端设置"),
        ] = None,
        baseline: Annotated[
            str | None,
            Field(description="基线版本（标签 / vN）；设置后每个版本都与它对比；省略时取 [eval].baseline"),
        ] = None,
        concurrency: Annotated[
            int | None, Field(ge=1, le=512, description="每个评测的并发上限；省略时取 [eval].concurrency")
        ] = None,
        wait: Annotated[
            bool, Field(description="是否等待评测结束（最多 10 分钟）；false 时只返回 run id")
        ] = True,
        agent: Agent = None,
    ) -> str:
        """发起评测。

        wait=true（默认）时等到结束，返回报告摘要（失败用例）和与基线的对比摘要；
        否则返回 run id，稍后用 get_eval_report 查看。
        dataset / evaluators / repeats / baseline / versions / concurrency 省略时读 .evalp.toml 的 [eval] 段。
        """
        project = env.project()
        args = actions.resolve_eval_args(
            project,
            dataset=dataset,
            versions=versions,
            evaluators=evaluators,
            repeats=repeats,
            baseline=baseline,
            concurrency=concurrency,
        )
        name = agent or (project.agent_name if project else None)
        async with env.client(project) as c:
            batch = await actions.create_eval(
                c,
                agent=name,
                dataset=args.dataset,
                evaluators=args.evaluators,
                versions=args.versions,
                baseline=args.baseline,
                repeats=args.repeats,
                concurrency=args.concurrency,
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

    # ------------------------------------------------------------------ agent / 草稿

    @mcp.tool()
    @_guard
    async def list_agents(
        limit: Annotated[int, Field(ge=1, le=100, description="最多返回多少个 agent")] = 50,
    ) -> str:
        """列出平台上的 agent：类型、最新版本、标签。"""
        async with env.client(env.project()) as c:
            page = await c.get("/agents", params={"limit": limit})
            text = render.render_agents(page["items"])
            if page.get("next_cursor"):
                text += f"\n\n（只显示前 {limit} 个，可调大 limit 或在 Web UI 查看。）"
            return text + f"\n\nWeb UI: {c.base_url}/agents"

    @mcp.tool()
    @_guard
    async def get_conventions(agent: Agent = None) -> str:
        """agent 生效的发布规范（服务端强制）与快照 map 规则说明。发布、推送前先调用。"""
        project = env.project()
        name = env.agent_name(agent)
        async with env.client(project) as c:
            try:
                conv = (await c.get(f"/agents/{name}"))["conventions"]
            except EvalpClientError as e:
                if e.code != "NOT_FOUND":
                    raise
                conv = None  # 还没有 push 过：agent 尚未创建
            spec = Workspace(project).load_spec() if project and name == project.agent_name else None
            return (
                render.render_conventions(name, conv, spec)
                + f"\n\nWeb UI（agent 设置）: {c.base_url}/agents/{name}"
            )

    def draft_name_for(project: ProjectConfig | None, agent: str | None) -> str:
        """项目的 agent 用 .evalp.toml 里的草稿名；其他 agent 用 main。"""
        if project and (agent is None or agent == project.agent_name):
            return project.draft_name
        return "main"

    @mcp.tool()
    @_guard
    async def get_draft(agent: Agent = None) -> str:
        """服务端草稿：完整 YAML、revision 和相对基础版本的变更摘要（update_draft 前先调用取得 revision）。"""
        project = env.project()
        name = env.agent_name(agent)
        async with env.client(project) as c:
            d = await actions.find_draft(c, name, draft_name_for(project, agent))
            lines = [
                f"## 草稿 `{name}/{d['name']}`（revision {d['revision']}）\n",
                f"- draft_id: `{d['id']}`，更新于 {render.fmt_local(d.get('updated_at'))}",
            ]
            if d.get("base_version_id"):
                try:
                    diff = await c.get(
                        "/versions/diff", params={"left": d["base_version_id"], "right": d["id"]}
                    )
                except EvalpClientError as e:
                    diff = None
                    lines.append(f"- 变更摘要不可用（草稿暂时无法与基础版本比较）：{e.message}")
                if diff is not None:
                    changes = diff.get("changes") or []
                    base = f"v{diff['left'].get('seq')}"
                    if changes:
                        lines.append(f"- 相对基础版本 {base} 的变更（{len(changes)} 项）:")
                        lines += [f"  - `{render.diff_change_lines(ch)}`" for ch in changes[:15]]
                        if len(changes) > 15:
                            lines.append("  - ……（更多变更请用 diff_versions 查看）")
                    else:
                        lines.append(f"- 与基础版本 {base} 相同（没有未发布的变更）")
            else:
                lines.append("- 草稿没有基础版本（还没有从某个已发布版本派生）")
            spec_yaml = yaml.safe_dump(
                d["spec"], allow_unicode=True, sort_keys=False, default_flow_style=False
            )
            lines += ["", "```yaml", render.cut_block(spec_yaml, 8000), "```"]
            lines.append(f"\nWeb UI: {c.base_url}/agents/{d['agent_id']}")
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def update_draft(
        patch: Annotated[
            dict[str, Any],
            Field(
                description="JSON Merge Patch（RFC 7396）：对象逐键合并，值为 null 的键会被删除，"
                '数组整体替换。例如 {"model": {"params": {"temperature": 0.2}}}'
            ),
        ],
        expected_revision: Annotated[int, Field(ge=1, description="get_draft 返回的 revision（乐观锁）")],
        agent: Agent = None,
    ) -> str:
        """直接修改服务端草稿（不经过本地文件）。返回新 revision 和校验结果。

        本地 agent.yaml 不会被改动：之后需要 pull_draft 同步回本地，否则下次 push_draft 会报冲突。
        """
        project = env.project()
        name = env.agent_name(agent)
        async with env.client(project) as c:
            d = await actions.find_draft(c, name, draft_name_for(project, agent))
            try:
                saved, validation = await actions.update_draft(c, d, patch, expected_revision)
            except EvalpClientError as e:
                if e.code == "DRAFT_CONFLICT":
                    raise ToolError(
                        f"草稿已被修改（当前 revision={e.details.get('current_revision', d['revision'])}，"
                        f"你传入的是 {expected_revision}）。请先调用 get_draft 查看最新内容，再重新提交补丁。"
                    ) from None
                raise
            lines = [
                "## 已更新草稿\n",
                f"- 草稿 `{name}/{saved['name']}`：revision {d['revision']} -> {saved['revision']}",
                *_validation_md(validation),
                "- 提示：本地文件没有改动。需要同步回本地时调用 pull_draft（本地有未推送的修改会拒绝覆盖）；"
                "否则下次 push_draft 会提示草稿冲突。",
                f"- Web UI: {c.base_url}/agents/{saved['agent_id']}",
            ]
            return "\n".join(lines)

    # ------------------------------------------------------------------ 资产与数据集

    @mcp.tool()
    @_guard
    async def list_assets(
        kind: Annotated[AssetKindName | None, Field(description="资产类型")] = None,
        q: Annotated[str | None, Field(description="按名称 / 描述模糊搜索")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="最多返回多少个资产")] = 50,
    ) -> str:
        """列出资产（提示词 / 工具 / 知识库 / Skill / MCP 插件 / 数据库 / 应用配置 / 数据集 / 文件）。"""
        async with env.client(env.project()) as c:
            page = await c.get("/assets", params={"kind": kind, "q": q, "limit": limit})
            text = "## 资产\n\n" + render.render_assets(page["items"])
            if page.get("next_cursor"):
                text += f"\n\n（只显示前 {limit} 个，可用 kind / q 缩小范围或调大 limit。）"
            return text + f"\n\nWeb UI: {c.base_url}/assets"

    @mcp.tool()
    @_guard
    async def upsert_asset(
        kind: Annotated[AssetKindName, Field(description="资产类型")],
        name: Annotated[str, Field(description="资产名称，如 cs-system")],
        content: Annotated[str | None, Field(description="文本内容；与 path 二选一")] = None,
        path: Annotated[
            str | None, Field(description="项目目录内的文件路径（相对项目目录）；与 content 二选一")
        ] = None,
        message: Annotated[str | None, Field(description="修订说明")] = None,
    ) -> str:
        """创建资产，或给同名资产新增修订（内容与已有修订相同则不新增）。返回修订号和是否已存在。

        agent 开启 snapshot_readonly 规范时，来自 agent 快照的资产会被拒绝（返回服务端的中文说明）。
        """
        file_path = env.resolve_path(path) if path is not None else None
        async with env.client(env.project()) as c:
            out = await actions.upsert_asset(c, kind, name, content=content, path=file_path, message=message)
            a, r = out["asset"], out["revision"]
            if out.get("created"):
                head = f"已创建资产 `{kind}/{name}`，rev {r['rev']}"
            elif out.get("existed"):
                head = f"资产 `{kind}/{name}` 内容与 rev {r['rev']} 相同，没有新增修订"
            else:
                head = f"资产 `{kind}/{name}` 已新增修订 rev {r['rev']}"
            lines = [
                f"## {head}\n",
                f"- asset_id: `{a['id']}`，修订 id: `{r['id']}`，大小 {r['size']} B",
                f"- 在 agent.yaml 中引用: `asset://{kind}/{name}@latest`（或 `@{r['rev']}` 固定到此修订）",
                f"- Web UI: {c.base_url}/assets/{a['id']}",
            ]
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def import_dataset(
        name: Annotated[str, Field(description="数据集名称，如 cs-regression（同名则新增修订）")],
        path: Annotated[str, Field(description="项目目录内的 JSONL / CSV 文件路径（相对项目目录）")],
        format: Annotated[
            Literal["jsonl", "csv"] | None, Field(description="文件格式；省略时按扩展名判断（.csv 为 csv）")
        ] = None,
        column_map: Annotated[
            dict[str, str] | None,
            Field(description='CSV 的列映射 {字段名: 列名}，如 {"input": "问题", "expected": "答案"}'),
        ] = None,
    ) -> str:
        """把本地 JSONL / CSV 导入为数据集资产。返回用例数、修订号和解析警告。"""
        file_path = env.resolve_path(path)
        async with env.client(env.project()) as c:
            ds_name = name.removeprefix("dataset/")
            out = await actions.import_dataset(c, ds_name, file_path, fmt=format, column_map=column_map)
            r = out["revision"]
            if out.get("created"):
                head = f"已创建数据集 `{ds_name}`"
            elif out.get("existed"):
                head = f"数据集 `{ds_name}` 内容未变化，沿用 rev {r['rev']}"
            else:
                head = f"数据集 `{ds_name}` 已新增修订 rev {r['rev']}"
            lines = [
                f"## {head}\n",
                f"- 用例数: {out['case_count']}",
                f"- 引用: `dataset/{ds_name}@{r['rev']}`（修订 id `{r['id']}`）",
            ]
            warnings = out.get("warnings") or []
            if warnings:
                lines.append(f"- 警告（{len(warnings)} 条）:")
                for w in warnings[:10]:
                    where = f"第 {w['line']} 行: " if w.get("line") else ""
                    lines.append(f"  - {where}{render.truncate(w.get('message') or w, 160)}")
                if len(warnings) > 10:
                    lines.append(f"  - ……另有 {len(warnings) - 10} 条")
            lines.append(f"- Web UI: {c.base_url}/assets/{out['asset']['id']}")
            return "\n".join(lines)

    # ------------------------------------------------------------------ 评测状态

    @mcp.tool()
    @_guard
    async def get_eval_status(run_id: Annotated[str, Field(description="评测 Run id（evr_…）")]) -> str:
        """评测的运行状态：进度、预计剩余时间（不含用例明细，明细用 get_eval_report）。"""
        async with env.client(env.project()) as c:
            r = await c.get(f"/evals/{run_id}")
            return render.render_eval_status(r, f"{c.base_url}/evals/{run_id}")

    # ------------------------------------------------------------------ 会话

    @mcp.tool()
    @_guard
    async def get_session(
        session_id: Annotated[str, Field(description="会话 id（ses_…），chat / 重放 / 评测报告里都能拿到")],
        turn: Annotated[int | None, Field(ge=0, description="只看这一轮（idx，从 0 开始）并展开详情")] = None,
        detail: Annotated[
            Literal["summary", "full"],
            Field(description="summary：每轮一段摘要；full：指定 turn 时再附上 trace 步骤"),
        ] = "summary",
    ) -> str:
        """会话记录和 trace 摘要：每轮的用户输入、回复、工具调用、耗时与 tokens。

        不指定 turn 时只给每轮摘要（最多最近 30 轮）；指定 turn 展开该轮，detail=full 还会列出事件步骤。
        """
        want_events = detail == "full" and turn is not None
        async with env.client(env.project()) as c:
            data = await c.get(
                f"/sessions/{session_id}", params={"include": "turns,events" if want_events else "turns"}
            )
            return render.render_session(
                data, turn=turn, full=detail == "full", web_url=f"{c.base_url}/sessions/{session_id}"
            )

    @mcp.tool()
    @_guard
    async def replay_session(
        session_id: Annotated[str, Field(description="要重放的历史会话 id")],
        target_version: Annotated[str, Field(description="用哪个版本重放：v13、标签或版本 id")],
        mode: Annotated[
            Literal["anchored", "free"],
            Field(
                description="重放模式：anchored（锚定，默认：每轮的上下文用原会话的历史，各轮并发）；"
                "free（自由：新版本用自己的回复续写上下文，逐轮串行，更慢）"
            ),
        ] = "anchored",
        turns: Annotated[list[int] | None, Field(description="只重放这些轮次（idx）；省略为全部")] = None,
        wait: Annotated[bool, Field(description="是否等待重放结束（最多 10 分钟）并给出对比摘要")] = True,
        agent: Agent = None,
    ) -> str:
        """用另一个版本重放历史会话：返回新会话 id 和与原会话的逐轮对比摘要。"""
        async with env.client(env.project()) as c:
            out = await actions.replay_session(
                c, session_id, target_version, agent=agent, turns=turns or None, mode=mode
            )
            new_id, compare_id = out["session_id"], out["compare_id"]
            lines = [
                "## 已创建重放\n",
                f"- 新会话: `{new_id}`（原会话 `{session_id}`）",
                f"- 对比组: `{compare_id}`: {c.base_url}/compare/{compare_id}",
            ]
            if not wait:
                lines += [
                    "",
                    "重放在后台执行。稍后用 get_session 查看新会话，或用 compare_sessions 对比。",
                ]
                return "\n".join(lines)
            alignment, timed_out = await actions.wait_replay(
                c, compare_id, interval=env.poll_interval, timeout=env.wait_timeout
            )
            group = await c.get(f"/compare/{compare_id}")
            lines += ["", render.render_alignment(group, alignment, unfinished=timed_out)]
            return "\n".join(lines)

    @mcp.tool()
    @_guard
    async def compare_sessions(
        left: Annotated[str, Field(description="左侧会话 id")],
        right: Annotated[str, Field(description="右侧会话 id")],
        max_turns: Annotated[int, Field(ge=1, le=20, description="最多展开多少个有差异的轮次")] = 5,
    ) -> str:
        """逐轮对比两个会话：步骤增减、指标差值、工具参数差异和回复 diff（只展开有差异的轮次）。"""
        async with env.client(env.project()) as c:
            group, alignment = await actions.compare_sessions(c, left, right)
            return render.render_alignment(
                group, alignment, max_turns=max_turns, web_url=f"{c.base_url}/compare/{group['id']}"
            )

    return mcp
