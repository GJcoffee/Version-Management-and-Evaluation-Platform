"""CLI 与 MCP 共用的高层操作：发布、发起 / 等待评测、取报告与对比、单轮对话。

只依赖 :class:`~evalp.client.EvalpClient` 和 :class:`~evalp.workspace_client.Workspace`，不做任何输出。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evalp.client import EvalpClient, EvalpClientError
from evalp.render import TERMINAL_STATUSES, item_input, item_output, item_score, truncate
from evalp.workspace_client import ProjectConfig, PushResult, Workspace, WorkspaceError

POLL_STATES = TERMINAL_STATUSES | {"paused"}  # 这些状态下不会再有进展，等待可以结束


# ---------------------------------------------------------------------- 引用规范化


def qualify_version(agent: str | None, ref: str) -> str:
    """``v3`` / ``3`` / ``baseline`` -> ``agent:v3`` / ``agent:baseline``；版本 id 与 ``a:x`` 原样返回。"""
    ref = ref.strip()
    if ref.startswith("ver_") or ":" in ref:
        return ref
    if not agent:
        raise WorkspaceError(f"版本 {ref!r} 需要指定 agent（--agent，或在含 .evalp.toml 的目录中运行）。")
    if ref.isdigit():
        ref = f"v{ref}"
    return f"{agent}:{ref}"


def normalize_dataset(ref: str) -> str:
    """数据集名称 / ``dataset/名称`` / 引用 / 修订 id -> API 接受的 ``dataset`` 参数。"""
    ref = ref.strip()
    if ref.startswith(("asset://", "arv_")):
        return ref
    if ref.startswith("dataset/"):
        ref = ref[len("dataset/") :]
    return f"asset://dataset/{ref}" + ("" if "@" in ref else "@latest")


def load_evaluators(
    source: str | Path | list[dict[str, Any]] | None, root: Path | None = None
) -> list[dict[str, Any]]:
    """评估器配置：列表原样返回；路径读取 YAML / JSON（顶层是列表，或 ``{evaluators: [...]}``）。

    ``source`` 为空时尝试项目根目录下的 ``evals.yaml``。
    """
    if isinstance(source, list):
        return source
    path: Path | None = None
    if source:
        path = Path(source)
        if not path.is_absolute() and root is not None:
            path = root / path
    elif root is not None and (root / "evals.yaml").is_file():
        path = root / "evals.yaml"
    if path is None:
        raise WorkspaceError(
            "未指定评估器配置。请用 --evaluators 指定文件，或在项目根目录放一个 evals.yaml。"
        )
    if not path.is_file():
        raise WorkspaceError(f"找不到评估器配置文件: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkspaceError(f"{path} 不是合法的 YAML/JSON: {e}") from e
    if isinstance(data, dict):
        data = data.get("evaluators")
    if not isinstance(data, list) or not data or not all(isinstance(x, dict) for x in data):
        raise WorkspaceError(f"{path} 应是评估器列表（或 evaluators: [...]），每项是一个映射。")
    return data


# ---------------------------------------------------------------------- 发布


@dataclass
class PublishResult:
    push: PushResult
    version: dict[str, Any]
    existed: bool
    tag: str | None = None
    parent_diff: dict[str, Any] | None = None


async def publish(
    client: EvalpClient,
    ws: Workspace,
    *,
    notes: str | None = None,
    tag: str | None = None,
    via: str = "cli",
) -> PublishResult:
    """先 push 再发布；``tag`` 非空时把标签指向该版本。"""
    pushed = await ws.push(client)
    out = await client.post(
        f"/drafts/{pushed.draft_id}/publish",
        {"expected_revision": pushed.revision, "notes": notes, "via": via},
    )
    version, existed = out["version"], bool(out.get("existed"))
    if tag:
        await client.post(f"/versions/{version['id']}/tags", {"tag": tag})
        version["tags"] = sorted({*(version.get("tags") or []), tag})
    diff = None
    parent = version.get("parent_version_id")
    if parent and not existed:
        try:
            diff = await client.get("/versions/diff", params={"left": parent, "right": version["id"]})
        except EvalpClientError:
            diff = None
    return PublishResult(pushed, version, existed, tag, diff)


# ---------------------------------------------------------------------- 评测


@dataclass
class EvalBatch:
    batch_id: str | None
    run_ids: list[str]
    version_refs: list[str]
    baseline_index: int | None = None
    estimate: dict[str, Any] = field(default_factory=dict)


async def create_eval(
    client: EvalpClient,
    *,
    agent: str | None,
    dataset: str,
    evaluators: list[dict[str, Any]],
    versions: list[str] | None = None,
    baseline: str | None = None,
    repeats: int | None = None,
    name: str | None = None,
    via: str = "cli",
) -> EvalBatch:
    """发起评测（每个版本一个 Run）。有 ``baseline`` 时基线排在第一位；否则多个版本时以第一个为基线。"""
    refs: list[str] = []
    for v in versions or ["latest"]:
        q = qualify_version(agent, v)
        if q not in refs:
            refs.append(q)
    base_ref: str | None = None
    if baseline:
        base_ref = qualify_version(agent, baseline)
        if base_ref in refs:
            refs.remove(base_ref)
        refs.insert(0, base_ref)
    body: dict[str, Any] = {
        "version_ids": refs,
        "dataset": normalize_dataset(dataset),
        "evaluators": evaluators,
        "via": via,
    }
    if repeats is not None:
        body["repeats"] = repeats
    if name:
        body["name"] = name
    out = await client.post("/evals", body)
    runs = out.get("runs") or []
    if len(runs) != len(refs):
        raise EvalpClientError(
            f"服务端返回了 {len(runs)} 个评测，与请求的 {len(refs)} 个版本不一致", code="CLIENT_MISMATCH"
        )
    return EvalBatch(
        out.get("batch_id"),
        [r["id"] for r in runs],
        refs,
        baseline_index=0 if len(refs) > 1 else None,
        estimate=out.get("estimate") or {},
    )


async def wait_for_runs(
    client: EvalpClient,
    run_ids: list[str],
    *,
    interval: float = 1.0,
    timeout: float | None = None,
    on_update: Callable[[list[dict[str, Any]]], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """轮询到所有 Run 结束（或暂停）。返回 (各 Run 的最新状态, 是否超时)。"""
    start = time.monotonic()
    while True:
        runs = list(await asyncio.gather(*(client.get(f"/evals/{rid}") for rid in run_ids)))
        if on_update:
            on_update(runs)
        if all(r.get("status") in POLL_STATES for r in runs):
            return runs, False
        if timeout is not None and time.monotonic() - start >= timeout:
            return runs, True
        await asyncio.sleep(interval)


async def fetch_report_items(
    client: EvalpClient, run: dict[str, Any], *, limit: int = 10, failed_only: bool = True
) -> dict[str, Any]:
    """取报告需要的用例：未通过的、执行失败的（以及 failed_only=False 时的其他结果）。"""
    rid = run["id"]
    fetch = max(limit, 1) * 3  # 多取一些，客户端再按得分排序
    errors: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    page = await client.get(
        f"/evals/{rid}/items", params={"passed": "false", "class": "failed", "limit": min(fetch, 200)}
    )
    failed = [
        it for it in page.get("items", []) if it.get("passed") is False and it.get("status") != "failed"
    ]
    stats = run.get("stats") or {}
    if stats.get("failed") or run.get("status") in ("completed_with_errors", "failed"):
        page2 = await client.get(
            f"/evals/{rid}/items", params={"status": "failed", "class": "error", "limit": limit}
        )
        errors = [it for it in page2.get("items", []) if it.get("status") == "failed" or it.get("error")]
    if not failed_only:
        page3 = await client.get(f"/evals/{rid}/items", params={"limit": limit})
        seen = {it["id"] for it in [*failed, *errors]}
        extra = [it for it in page3.get("items", []) if it.get("id") not in seen]
    ranked = sorted(failed, key=lambda it: (item_score(it) is None, item_score(it) or 0.0))
    await _fill_outputs(client, rid, [*ranked[:limit], *errors[:limit], *extra])
    return {
        "failed": failed,
        "failed_more": bool(page.get("next_cursor")),
        "errors": errors,
        "extra": extra,
    }


async def _fill_outputs(client: EvalpClient, run_id: str, items: list[dict[str, Any]]) -> None:
    """列表里没有输出文本时，用单条详情补上（最多补这些条目）。"""
    for it in items:
        if item_output(it) or it.get("status") not in ("done", "failed") or not it.get("id"):
            continue
        try:
            detail = await client.get(f"/evals/{run_id}/items/{it['id']}")
        except EvalpClientError:
            continue
        if detail.get("output"):
            it["output"] = detail["output"]
        if not it.get("expected") and (detail.get("case_detail") or {}).get("expected"):
            it["expected"] = detail["case_detail"]["expected"]


async def fetch_compare(
    client: EvalpClient,
    left: str,
    right: str,
    *,
    case_class: str | None = None,
    limit: int = 50,
    threshold: float | None = None,
) -> dict[str, Any]:
    return await client.get(
        "/evals/compare",
        params={"left": left, "right": right, "class": case_class, "limit": limit, "threshold": threshold},
    )


async def regression_details(
    client: EvalpClient, left_id: str, right_id: str, cases: list[dict[str, Any]], n: int = 5
) -> dict[str, list[str]]:
    """为前 n 个退化用例补充右侧的输出与评审理由（尽力而为）。"""
    out: dict[str, list[str]] = {}
    for c in [c for c in cases if c.get("class") == "regressed"][:n]:
        items: dict[str, dict[str, Any]] = {}
        for side, run_id in (("left", left_id), ("right", right_id)):
            ids = (c.get(side) or {}).get("item_ids") or []
            if not ids:
                continue
            try:
                items[side] = await client.get(f"/evals/{run_id}/items/{ids[0]}")
            except EvalpClientError:
                continue
        lines: list[str] = []
        right = items.get("right") or {}
        if item_input(right):
            lines.append(f"输入: {truncate(item_input(right), 200)}")
        if item_output(items.get("left") or {}):
            lines.append(f"基线输出: {truncate(item_output(items['left']), 240)}")
        if item_output(right):
            lines.append(f"对比输出: {truncate(item_output(right), 240)}")
        for s in right.get("scores") or []:
            if s.get("reason"):
                lines.append(f"对比评审 `{s.get('evaluator_key')}`: {truncate(s['reason'], 240)}")
        if lines:
            out[c["case_key"]] = lines
    return out


# ---------------------------------------------------------------------- 对话


async def create_session(client: EvalpClient, agent: str | None, version: str | None) -> dict[str, Any]:
    """为版本创建对话会话；``version`` 省略时用最新版本。"""
    ref = qualify_version(agent, version or "latest")
    out = await client.post("/sessions", {"version_id": ref})
    return out["session"]


def summarize_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """从 TurnOut 提取：回复文本、工具调用摘要、状态、错误。"""
    output = turn.get("output") or {}
    final = output.get("final") or {}
    content = final.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    results: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    for m in output.get("messages") or []:
        if m.get("role") == "tool":
            results[m.get("tool_call_id")] = m.get("content")
        for tc in m.get("tool_calls") or []:
            calls.append({"id": tc.get("id"), "name": tc.get("name"), "arguments": tc.get("arguments")})
    for c in calls:
        c["result"] = results.get(c["id"])
    return {
        "text": content or "",
        "tool_calls": calls,
        "status": turn.get("status"),
        "error": turn.get("error"),
        "usage": turn.get("usage") or {},
        "latency_ms": turn.get("latency_ms"),
        "cost": turn.get("cost"),
    }


async def chat_turn(
    client: EvalpClient, session_id: str, message: str, *, timeout: float = 600.0
) -> dict[str, Any]:
    """发送一条消息并等到本轮结束（wait=true），返回 TurnOut。"""
    out = await client.post(
        f"/sessions/{session_id}/turns", {"input": message, "wait": True}, timeout=timeout
    )
    return out["turn"]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def project_or_none(start: Path | None = None) -> ProjectConfig | None:
    """找得到项目配置就返回，找不到返回 None（部分命令不需要项目）。"""
    from evalp.workspace_client import ProjectNotFound, find_project

    try:
        return find_project(start)
    except ProjectNotFound:
        return None
