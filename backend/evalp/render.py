"""客户端侧的渲染：把 API 返回的版本 / diff / 评测报告整理成 Markdown（MCP 与 CLI 共用）。

评测接口的返回结构以 ``docs/contract-eval.md`` 为准，同时容忍实现里的少量差异
（如 ``items`` 与 ``cases.items``、``metrics`` 与 ``metric_deltas``），缺字段时跳过而不是报错。
"""

from __future__ import annotations

import json
from typing import Any

from evalp.utils.time import fmt_local

TERMINAL_STATUSES = {"completed", "completed_with_errors", "cancelled", "failed"}

STATUS_TEXT = {
    "queued": "排队中",
    "running": "运行中",
    "paused": "已暂停",
    "completed": "已完成",
    "completed_with_errors": "已完成（有执行错误）",
    "cancelled": "已取消",
    "failed": "失败",
}


# ---------------------------------------------------------------------- 通用


def truncate(text: Any, limit: int = 200) -> str:
    """压成单行并截断。"""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def cut_block(text: str, limit: int = 600) -> str:
    """保留换行的截断（用于代码块里的输出）。"""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"\n……（共 {len(text)} 字，已截断）"


def pct(x: Any) -> str:
    return "-" if not isinstance(x, int | float) else f"{x * 100:.1f}%"


def num(x: Any, digits: int = 3) -> str:
    if not isinstance(x, int | float):
        return "-"
    return f"{x:.{digits}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def signed(x: Any, digits: int = 3) -> str:
    if not isinstance(x, int | float):
        return "-"
    s = num(abs(x), digits) if x else "0"
    return ("+" if x > 0 else "-" if x < 0 else "") + s


def signed_pct(x: Any) -> str:
    if not isinstance(x, int | float):
        return "-"
    return f"{x * 100:+.1f}pp"


def version_label(seq: Any, fallback: str = "") -> str:
    return f"v{seq}" if seq is not None else fallback


def status_text(status: str | None) -> str:
    return STATUS_TEXT.get(status or "", status or "-")


# ---------------------------------------------------------------------- 版本


def render_versions(agent: str, items: list[dict[str, Any]], base_url: str = "") -> str:
    if not items:
        return f"agent `{agent}` 还没有已发布的版本。用 publish_version 发布第一个版本。"
    lines = [
        f"## {agent} 的版本（最新在前）",
        "",
        "| 版本 | 标签 | 时间 | 来源 | 备注 | id |",
        "|---|---|---|---|---|---|",
    ]
    for v in items:
        tags = ", ".join(v.get("tags") or []) or "-"
        lines.append(
            f"| v{v.get('seq')} | {tags} | {fmt_local(v.get('created_at'))} "
            f"| {v.get('created_via') or '-'} | {truncate(v.get('notes') or '-', 40)} | `{v.get('id')}` |"
        )
    return "\n".join(lines)


def _ref_text(v: Any) -> str:
    if isinstance(v, dict) and "kind" in v and "name" in v:
        return f"{v['kind']}/{v['name']}" + (f"@{v['rev']}" if "rev" in v else "")
    if isinstance(v, dict) and set(v) == {"rev"}:
        return f"rev {v['rev']}"
    return truncate(v, 80)


def diff_change_lines(ch: dict[str, Any]) -> str:
    """一条变更的单行描述。"""
    op, path = ch.get("op", "?"), ch.get("path", "?")
    if op == "add":
        return f"+ {path}: {_ref_text(ch.get('right'))}"
    if op == "remove":
        return f"- {path}: {_ref_text(ch.get('left'))}"
    return f"~ {path}: {_ref_text(ch.get('left'))} -> {_ref_text(ch.get('right'))}"


def diff_side_label(side: dict[str, Any]) -> str:
    """diff 一侧的名称：版本为 ``v12``，草稿为 ``草稿 main(r3)``。"""
    if side.get("draft"):
        return f"草稿 {side.get('name')}(r{side.get('revision')})"
    return f"v{side.get('seq')}"


def render_diff_md(data: dict[str, Any], max_lines: int = 200, web_url: str | None = None) -> str:
    """版本 diff 的 Markdown：变更列表加文本 diff，文本 diff 总行数超过 max_lines 时截断。"""
    left, right = data.get("left") or {}, data.get("right") or {}
    summ = data.get("summary") or {}
    changes = data.get("changes") or []
    head = f"## 版本 diff：{diff_side_label(left)} -> {diff_side_label(right)}"
    if not changes:
        return head + "\n\n两个版本的内容完全相同（没有变更）。"
    parts = [head, ""]
    parts.append("变更统计：" + "，".join(f"{k} {v}" for k, v in summ.items() if v) if summ else "")
    parts += ["", "### 变更列表", ""]
    parts += [f"- `{diff_change_lines(c)}`" for c in changes]
    used, omitted = 0, 0
    blocks: list[str] = []
    for c in changes:
        text = ((c.get("content_diff") or {}).get("text")) or ""
        if not text:
            continue
        rows = text.splitlines()
        room = max_lines - used
        if room <= 0:
            omitted += len(rows)
            continue
        shown = rows[:room]
        omitted += len(rows) - len(shown)
        used += len(shown)
        blocks.append(f"#### {c.get('path')}\n\n```diff\n" + "\n".join(shown) + "\n```")
    if blocks:
        parts += ["", "### 文本 diff", ""] + blocks
    if omitted:
        parts += [
            "",
            f"（文本 diff 已截断，省略 {omitted} 行。完整内容见 Web UI 或运行 `evalp diff v{left.get('seq')} "
            f"v{right.get('seq')}`。）",
        ]
    if web_url:
        parts += ["", f"Web UI: {web_url}"]
    return "\n".join(p for p in parts if p is not None)


# ---------------------------------------------------------------------- 评测


def run_headline(run: dict[str, Any]) -> str:
    ds = run.get("dataset") if isinstance(run.get("dataset"), dict) else {}
    ds_name = ds.get("name") or run.get("dataset_name")
    ds_rev = ds.get("rev") or run.get("dataset_rev")
    seq = run.get("version_seq")
    agent = run.get("agent_name") or run.get("agent_id") or ""
    text = f"{agent} {version_label(seq, run.get('version_id') or '')}".strip()
    if ds_name:
        text += f" · 数据集 {ds_name}" + (f"@{ds_rev}" if ds_rev is not None else "")
    return text


def stats_lines(stats: dict[str, Any] | None) -> list[str]:
    """汇总指标，每行一项。"""
    if not stats:
        return []
    lines = []
    done, failed = stats.get("done"), stats.get("failed")
    lines.append(
        f"- 通过率: **{pct(stats.get('pass_rate'))}**（用例 {stats.get('cases', '-')}，"
        f"执行 {stats.get('items', '-')}，完成 {done if done is not None else '-'}，"
        f"执行失败 {failed if failed is not None else '-'}，缓存命中 {stats.get('cached', 0)}）"
    )
    for key, ev in (stats.get("evaluators") or {}).items():
        extra = f"，通过率 {pct(ev['pass_rate'])}" if isinstance(ev.get("pass_rate"), int | float) else ""
        err = f"，出错 {ev['errors']}" if ev.get("errors") else ""
        lines.append(f"- 评估器 `{key}`: 均分 {num(ev.get('mean'), 3)}{extra}{err}")
    lat = stats.get("latency_ms") or {}
    if lat:
        lines.append(f"- 延迟: p50 {num(lat.get('p50'), 0)} ms，p95 {num(lat.get('p95'), 0)} ms")
    tok = stats.get("tokens") or {}
    if tok:
        lines.append(f"- tokens: 输入 {tok.get('prompt', '-')}，输出 {tok.get('completion', '-')}")
    if stats.get("cost") is not None:
        lines.append(f"- 成本: {num(stats.get('cost'), 4)}")
    return lines


def progress_text(run: dict[str, Any]) -> str:
    p = run.get("progress") or {}
    return (
        f"{p.get('done', 0)}/{p.get('total', '?')}（失败 {p.get('failed', 0)}，运行中 {p.get('running', 0)}，"
        f"缓存 {p.get('cached', 0)}）"
    )


def render_run_summary(run: dict[str, Any], web_url: str | None = None) -> str:
    lines = [f"### {run.get('id')}：{run_headline(run)}", "", f"- 状态: {status_text(run.get('status'))}"]
    if run.get("status") not in TERMINAL_STATUSES:
        lines.append(f"- 进度: {progress_text(run)}")
    if run.get("error"):
        lines.append(f"- 错误: {truncate(run['error'], 200)}")
    lines += stats_lines(run.get("stats"))
    if web_url:
        lines.append(f"- Web UI: {web_url}")
    return "\n".join(lines)


def item_key(it: dict[str, Any]) -> str:
    return it.get("case_key") or (it.get("case") or {}).get("case_key") or it.get("id") or "?"


def _turn_text(t: Any) -> str:
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        c = t.get("content")
        return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    return str(t)


def item_input(it: dict[str, Any]) -> str:
    if it.get("input_preview"):
        return it["input_preview"]
    inp = (it.get("case") or {}).get("input") or it.get("input")
    if isinstance(inp, dict) and inp.get("turns"):
        return " / ".join(_turn_text(t) for t in inp["turns"])
    return _turn_text(inp) if inp else ""


def item_output(it: dict[str, Any]) -> str:
    return it.get("output_preview") or it.get("output") or ""


def item_score(it: dict[str, Any]) -> float | None:
    """用例得分：优先用 score 字段，否则取各评估器分数的平均。"""
    if isinstance(it.get("score"), int | float):
        return it["score"]
    vals = [s["score"] for s in it.get("scores") or [] if isinstance(s.get("score"), int | float)]
    return sum(vals) / len(vals) if vals else None


def render_item(it: dict[str, Any], *, text_limit: int = 300) -> list[str]:
    """一条评测结果（失败 / 低分用例）的 Markdown 片段。"""
    score = item_score(it)
    rep = it.get("repeat_idx")
    title = f"#### `{item_key(it)}`" + (f"（第 {rep + 1} 次重复）" if isinstance(rep, int) and rep else "")
    if score is not None:
        title += f" 得分 {num(score, 2)}"
    if it.get("status") == "failed" or it.get("error"):
        title += " 执行失败"
    lines = [title, ""]
    if item_input(it):
        lines.append(f"- 输入: {truncate(item_input(it), text_limit)}")
    if it.get("expected"):
        lines.append(f"- 期望: {truncate(it['expected'], text_limit)}")
    if item_output(it):
        lines.append(f"- 输出: {truncate(item_output(it), text_limit)}")
    err = it.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else err
        lines.append(f"- 错误: {truncate(msg, text_limit)}")
    for s in it.get("scores") or []:
        mark = "通过" if s.get("passed") else "未通过" if s.get("passed") is False else s.get("status", "-")
        reason = f"：{truncate(s['reason'], text_limit)}" if s.get("reason") else ""
        lines.append(f"- 评估器 `{s.get('evaluator_key')}`: {num(s.get('score'), 2)}（{mark}）{reason}")
    if it.get("session_id"):
        lines.append(f"- 会话: `{it['session_id']}`")
    lines.append("")
    return lines


def render_report(
    run: dict[str, Any], items: dict[str, Any], *, limit: int = 10, web_url: str | None = None
) -> str:
    """评测报告：指标摘要加失败 / 低分用例（按得分从低到高）。

    ``items`` 是 ``actions.fetch_report_items`` 的返回值（failed / failed_more / errors / extra）。
    """
    failed, errors = items["failed"], items["errors"]
    extra, failed_more = items.get("extra") or [], items.get("failed_more", False)
    parts = ["## 评测报告", "", render_run_summary(run, web_url), ""]
    if run.get("status") not in TERMINAL_STATUSES:
        parts.append("评测尚未结束，以上指标是暂时的。")
        parts.append("")
    ranked = sorted(failed, key=lambda it: (item_score(it) is None, item_score(it) or 0.0))
    total = len(ranked)
    total_text = f"{total}+" if failed_more else str(total)
    if ranked:
        parts += [
            f"### 未通过的用例（共 {total_text} 条，按得分从低到高显示前 {min(limit, len(ranked))} 条）",
            "",
        ]
        for it in ranked[:limit]:
            parts += render_item(it)
    elif run.get("status") in TERMINAL_STATUSES:
        parts += ["### 未通过的用例", "", "没有未通过的用例。", ""]
    if errors:
        parts += [f"### 执行失败的用例（前 {min(limit, len(errors))} 条）", ""]
        for it in errors[:limit]:
            parts += render_item(it)
    if extra:
        parts += [f"### 其他结果（前 {len(extra)} 条）", ""]
        for it in extra:
            parts += render_item(it)
    if total > limit or failed_more:
        parts.append(
            "（已截断。完整列表请在 Web UI 查看，"
            f"或运行 `evalp eval report {run.get('id')} --format json`。）"
        )
    return "\n".join(parts).rstrip() + "\n"


def compare_cases(data: dict[str, Any]) -> list[dict[str, Any]]:
    return (data.get("cases") or {}).get("items") or data.get("items") or []


def _flat_metric_deltas(summary: dict[str, Any]) -> list[str]:
    m = summary.get("metric_deltas") or summary.get("metrics") or {}
    out: list[str] = []
    if "pass_rate" in m:
        out.append(f"通过率 {signed_pct(m['pass_rate'])}")
    lat = m.get("latency_ms")
    p50 = m.get("latency_p50_ms", lat.get("p50") if isinstance(lat, dict) else None)
    if p50 is not None:
        out.append(f"延迟 p50 {signed(p50, 0)} ms")
    p95 = m.get("latency_p95_ms", lat.get("p95") if isinstance(lat, dict) else None)
    if p95 is not None:
        out.append(f"延迟 p95 {signed(p95, 0)} ms")
    if "tokens_total" in m:
        out.append(f"tokens {signed(m['tokens_total'], 0)}")
    elif isinstance(m.get("tokens"), dict):
        out.append(f"tokens {signed(sum(v for v in m['tokens'].values() if isinstance(v, int | float)), 0)}")
    if "cost" in m:
        out.append(f"成本 {signed(m['cost'], 4)}")
    return out


def _warning_text(w: Any) -> str:
    return w.get("message", str(w)) if isinstance(w, dict) else str(w)


def side_label(side: dict[str, Any]) -> str:
    seq = side.get("version_seq")
    base = f"{side.get('agent_name') or ''} {version_label(seq)}".strip() or side.get("version_id", "")
    return f"{base}（{side.get('id')}）"


def case_line(c: dict[str, Any]) -> str:
    left, right = c.get("left") or {}, c.get("right") or {}
    tags = f" [{', '.join(c['tags'])}]" if c.get("tags") else ""
    flaky = " (不稳定)" if c.get("flaky") else ""
    return (
        f"`{c.get('case_key')}`{tags}: {num(left.get('score'), 2)} -> {num(right.get('score'), 2)}"
        f"（Δ {signed(c.get('delta'), 2)}）{flaky}"
    )


def render_compare(
    data: dict[str, Any],
    *,
    limit: int = 10,
    web_url: str | None = None,
    case_class: str | None = None,
    details: dict[str, list[str]] | None = None,
) -> str:
    """评测对比：整体结论 + 退化用例（突出）+ 改善用例。``details`` 为 case_key -> 补充行。"""
    left, right = data.get("left") or {}, data.get("right") or {}
    s = data.get("summary") or {}
    parts = ["## 评测对比", "", f"- 基线（左）: {side_label(left)}", f"- 对比（右）: {side_label(right)}", ""]
    verdict = (
        f"**退化 {s.get('regressed', 0)}**，改善 {s.get('improved', 0)}，不变 {s.get('unchanged', 0)}"
        f"，新增 {s.get('new', 0)}，缺失 {s.get('missing', 0)}"
    )
    parts += ["### 整体结论", "", verdict, ""]
    if isinstance(s.get("mean_delta"), int | float):
        ci = s.get("ci95")
        ci_text = f"，95% 置信区间 [{signed(ci[0])}, {signed(ci[1])}]" if ci and len(ci) == 2 else ""
        p = s.get("sign_test_p")
        p_text = f"，符号检验 p={num(p, 3)}" if isinstance(p, int | float) else ""
        parts.append(f"- 平均 Δ（右 - 左）: {signed(s['mean_delta'])}{ci_text}{p_text}")
    md = _flat_metric_deltas(s)
    if md:
        parts.append("- 指标变化（右 - 左）: " + "；".join(md))
    for w in data.get("warnings") or []:
        parts.append(f"- 注意: {_warning_text(w)}")
    cases = compare_cases(data)
    regressed = [c for c in cases if c.get("class") == "regressed"]
    improved = [c for c in cases if c.get("class") == "improved"]
    others = [c for c in cases if c.get("class") not in ("regressed", "improved")]
    details = details or {}

    def section(title: str, rows: list[dict[str, Any]], n: int) -> None:
        if not rows:
            return
        parts.extend(["", f"### {title}（显示 {min(n, len(rows))}/{len(rows)}）", ""])
        for c in rows[:n]:
            parts.append(f"- {case_line(c)}")
            parts.extend(f"  - {d}" for d in details.get(c.get("case_key", ""), []))

    section("退化的用例", regressed, limit)
    if case_class in (None, "improved"):
        section("改善的用例", improved, min(limit, 5) if case_class is None else limit)
    if case_class not in (None, "regressed", "improved"):
        section(f"{case_class} 的用例", others, limit)
    if data.get("next_cursor") or (data.get("cases") or {}).get("next_cursor"):
        parts += ["", "（明细还有更多，已截断；可用 case_class 过滤，或在 Web UI 查看。）"]
    if web_url:
        parts += ["", f"Web UI: {web_url}"]
    return "\n".join(parts) + "\n"
