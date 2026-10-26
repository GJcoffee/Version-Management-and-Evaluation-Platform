"""客户端侧的渲染：把 API 返回的版本 / diff / 评测报告整理成 Markdown（MCP 与 CLI 共用）。

评测接口的返回结构以 ``docs/contract-eval.md`` 为准，同时容忍实现里的少量差异
（如 ``items`` 与 ``cases.items``、``metrics`` 与 ``metric_deltas``），缺字段时跳过而不是报错。
"""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime
from typing import Any

from evalp.utils.time import fmt_local, parse_iso

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


# ---------------------------------------------------------------------- 评测状态与门禁


def eta_seconds(run: dict[str, Any], now: datetime | None = None) -> float | None:
    """按已完成用例的速度估算剩余秒数；还没有进展、已结束或缺少开始时间时返回 None。"""
    if run.get("status") in TERMINAL_STATUSES or not run.get("started_at"):
        return None
    p = run.get("progress") or {}
    total = p.get("total") or 0
    finished = (p.get("done") or 0) + (p.get("failed") or 0) + (p.get("cancelled") or 0)
    try:
        elapsed = ((now or datetime.now(UTC)) - parse_iso(run["started_at"])).total_seconds()
    except (ValueError, TypeError):
        return None
    if total <= 0 or finished <= 0 or elapsed <= 0:
        return None
    return max(total - finished, 0) / (finished / elapsed)


def duration_text(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        return f"{s // 60} 分 {s % 60} 秒"
    return f"{s // 3600} 小时 {s % 3600 // 60} 分"


def render_eval_status(run: dict[str, Any], web_url: str | None = None, now: datetime | None = None) -> str:
    """评测进度：状态、进度、预计剩余时间（不含用例明细）。"""
    p = run.get("progress") or {}
    lines = [
        f"## 评测状态 `{run.get('id')}`",
        "",
        f"- {run_headline(run)}",
        f"- 状态: {status_text(run.get('status'))}",
    ]
    if run.get("pause_reason"):
        lines.append(f"- 暂停原因: {run['pause_reason']}")
    total = p.get("total") or 0
    finished = (p.get("done") or 0) + (p.get("failed") or 0) + (p.get("cancelled") or 0)
    pct_text = f"（{finished / total * 100:.0f}%）" if total else ""
    lines.append(f"- 进度: {finished}/{total or '?'}{pct_text}；{progress_text(run)}")
    if run.get("status") not in TERMINAL_STATUSES:
        eta = eta_seconds(run, now)
        lines.append(f"- 预计剩余: {duration_text(eta)}" if eta is not None else "- 预计剩余: 暂时无法估算")
    if p.get("cost_new") is not None:
        lines.append(f"- 已产生成本: {num(p.get('cost_new'), 4)}")
    if run.get("error"):
        lines.append(f"- 错误: {truncate(run['error'], 200)}")
    if run.get("status") in TERMINAL_STATUSES and run.get("stats"):
        lines += stats_lines(run["stats"])[:1]
    if web_url:
        lines.append(f"- Web UI: {web_url}")
    return "\n".join(lines) + "\n"


def render_gate(result: dict[str, Any], web_url: str | None = None) -> str:
    """CI 门禁结果：结论、原因、关键数字和退化用例。"""
    s = result.get("summary") or {}
    passed = bool(result.get("passed"))
    lines = [
        f"## 门禁{'通过' if passed else '未通过'}",
        "",
        f"- 评测: `{s.get('run_id')}`（{status_text(s.get('status'))}）",
    ]
    lines.append(
        f"- 通过率: {pct(s.get('pass_rate'))}"
        + (
            f"，要求不低于 {pct(s['min_pass_rate'])}"
            if s.get("min_pass_rate") is not None
            else "（未设置 --min-pass-rate，通过率不参与判定）"
        )
    )
    if s.get("failed_items"):
        lines.append(f"- 执行失败的用例: {s['failed_items']}")
    if s.get("baseline_run_id"):
        lines.append(
            f"- 基线: `{s['baseline_run_id']}`（{status_text(s.get('baseline_status'))}），"
            f"通过率 {pct(s.get('baseline_pass_rate'))}"
        )
        lines.append(
            f"- 相对基线: 退化 {s.get('regressions', '-')}（允许 {s.get('max_regressions')}），"
            f"改善 {s.get('improved', '-')}，不变 {s.get('unchanged', '-')}"
        )
        if isinstance(s.get("mean_delta"), int | float):
            ci = s.get("ci95")
            ci_text = f"，95% 置信区间 [{signed(ci[0])}, {signed(ci[1])}]" if ci and len(ci) == 2 else ""
            lines.append(f"- 平均 Δ（右 - 左）: {signed(s['mean_delta'])}{ci_text}")
    elif s.get("max_regressions") is not None:
        lines.append("- 未指定基线，不检查退化用例（用 --baseline 指定）")
    if result.get("reasons"):
        lines += ["", "未通过原因:"] + [f"- {r}" for r in result["reasons"]]
    cases = s.get("regressed_cases") or []
    if cases:
        lines += ["", f"退化的用例（显示 {min(len(cases), 10)}/{s.get('regressions', len(cases))}）:"]
        lines += [f"- `{c.get('case_key')}`（Δ {signed(c.get('delta'), 2)}）" for c in cases[:10]]
    if web_url:
        lines += ["", f"Web UI: {web_url}"]
    return "\n".join(lines) + "\n"


def render_adapter_check(report: dict[str, Any]) -> str:
    """Adapter 自检报告：每个检查项一行。"""
    marks = {"pass": "通过", "fail": "失败", "warn": "警告", "skip": "跳过"}
    summ = report.get("summary") or {}
    head = "## 适配器自检：" + ("通过" if report.get("ok") else "**未通过**")
    lines = [
        head,
        "",
        f"- {report.get('agent_name') or ''} · {report.get('kind') or '-'}"
        + (f" · {report['transport']}" if report.get("transport") else "")
        + f" · 耗时 {(report.get('duration_ms') or 0) / 1000:.1f}s",
        "- " + "，".join(f"{marks.get(k, k)} {v}" for k, v in summ.items() if v),
        "",
    ]
    for it in report.get("items") or []:
        lines.append(
            f"- [{marks.get(it.get('status'), it.get('status'))}] {it.get('title') or it.get('name')}"
            + (f"：{truncate(it['message'], 200)}" if it.get("message") else "")
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- agent / 资产 / 草稿


def render_agents(items: list[dict[str, Any]]) -> str:
    if not items:
        return "还没有 agent。在项目目录运行 push_draft（evalp push）创建第一个。"
    lines = ["## agent 列表", "", "| agent | 类型 | 最新版本 | 标签 | 说明 |", "|---|---|---|---|---|"]
    for a in items:
        latest = a.get("latest_version") or {}
        ver = f"v{latest['seq']}（{fmt_local(latest.get('created_at'))}）" if latest.get("seq") else "未发布"
        lines.append(
            f"| `{a.get('name')}`{' (已归档)' if a.get('archived') else ''} | {a.get('kind')} | {ver} "
            f"| {', '.join(a.get('tags') or []) or '-'} | {truncate(a.get('description') or '-', 40)} |"
        )
    return "\n".join(lines)


def render_assets(items: list[dict[str, Any]]) -> str:
    if not items:
        return "没有匹配的资产。"
    lines = ["| 资产 | 最新修订 | 大小 | 创建时间 | 说明 |", "|---|---|---|---|---|"]
    for a in items:
        latest = a.get("latest") or {}
        size = f"{latest['size']} B" if latest.get("size") is not None else "-"
        lines.append(
            f"| `{a.get('kind')}/{a.get('name')}` | rev {a.get('latest_rev')} | {size} "
            f"| {fmt_local(a.get('created_at'))} | {truncate(a.get('description') or '-', 40)} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------- 会话与对比


def _msg_text(msg: Any) -> str:
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, list):
            return "".join(p.get("text", "") for p in c if isinstance(p, dict))
        return c if isinstance(c, str) else ""
    return msg if isinstance(msg, str) else ""


def _turn_summary(turn: dict[str, Any]) -> dict[str, Any]:
    """TurnOut -> 文本、工具调用（含结果）；与 actions.summarize_turn 一致（render 不依赖 actions）。"""
    output = turn.get("output") or {}
    text = _msg_text(output.get("final")) or output.get("partial_text") or ""
    results: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    for m in output.get("messages") or []:
        if m.get("role") == "tool":
            results[m.get("tool_call_id")] = m.get("content")
        for tc in m.get("tool_calls") or []:
            calls.append({"id": tc.get("id"), "name": tc.get("name"), "arguments": tc.get("arguments")})
    for c in calls:
        c["result"] = results.get(c["id"])
    return {"text": text, "tool_calls": calls}


def _event_brief(ev: dict[str, Any]) -> str:
    p = ev.get("payload") or {}
    if "$blob" in p:
        return "（内容较大，已转存，请在 Web UI 查看）"
    t = ev.get("type")
    if t == "llm.request":
        return f"模型 {p.get('model')}，{len(p.get('messages') or [])} 条消息"
    if t == "llm.response":
        u = p.get("usage") or {}
        return truncate(
            f"{p.get('finish_reason') or ''} "
            f"tokens {u.get('prompt_tokens', '-')}/{u.get('completion_tokens', '-')}"
            + (f" 工具调用 {len(p['tool_calls'])} 个" if p.get("tool_calls") else ""),
            120,
        )
    if t == "tool.call":
        return f"{p.get('name')}({truncate(p.get('arguments'), 100)})"
    if t == "tool.result":
        return (
            f"{p.get('name')} {'失败' if p.get('is_error') else '完成'}"
            + (f" {p['latency_ms']}ms" if p.get("latency_ms") is not None else "")
            + f": {truncate(p.get('output'), 100)}"
        )
    if t == "error":
        return f"{p.get('code', '')} {truncate(p.get('message'), 120)}".strip()
    if t in ("span.start", "span.end"):
        return truncate(p.get("name") or p, 100)
    if t == "turn.end":
        return f"状态 {p.get('status')}" + (
            f"，{p['latency_ms']}ms" if p.get("latency_ms") is not None else ""
        )
    return truncate(p, 100) if p else ""


def render_session(
    detail: dict[str, Any],
    *,
    turn: int | None = None,
    full: bool = False,
    max_turns: int = 30,
    web_url: str | None = None,
) -> str:
    """会话记录：默认每轮一段摘要；``turn`` 指定时展开该轮；``full`` 且带 events 时附上 trace 步骤。"""
    s = detail.get("session") or {}
    turns = detail.get("turns") or []
    stats = s.get("stats") or {}
    lines = [
        f"## 会话 `{s.get('id')}`",
        "",
        f"- agent: `{s.get('agent_name') or s.get('agent_id')}` {version_label(s.get('version_seq'))}"
        f" · 模式 {s.get('mode')} · 状态 {s.get('status')} · 共 {len(turns)} 轮",
    ]
    if s.get("title"):
        lines.append(f"- 标题: {truncate(s['title'], 80)}")
    if s.get("source_session_id"):
        lines.append(f"- 重放自: `{s['source_session_id']}`")
    if stats.get("cost") or stats.get("prompt_tokens"):
        lines.append(
            f"- 累计: tokens {stats.get('prompt_tokens', 0)}/{stats.get('completion_tokens', 0)}，"
            f"成本 {num(stats.get('cost') or 0.0, 4)}"
        )
    if web_url:
        lines.append(f"- Web UI（含 trace）: {web_url}")
    events = detail.get("events")
    if turn is not None:
        chosen = [t for t in turns if t.get("idx") == turn]
        if not chosen:
            have = ", ".join(str(t.get("idx")) for t in turns[:20])
            return "\n".join(lines + ["", f"**没有第 {turn} 轮**（现有轮次 idx: {have or '无'}）。"])
        shown = chosen
    else:
        shown = turns[-max_turns:]
        if len(turns) > len(shown):
            lines += [
                "",
                f"（共 {len(turns)} 轮，只显示最后 {len(shown)} 轮；用 turn 参数查看某一轮的详情。）",
            ]
    for t in shown:
        sm = _turn_summary(t)
        u = t.get("usage") or {}
        meta = [f"状态 {t.get('status')}"]
        if t.get("latency_ms") is not None:
            meta.append(f"{t['latency_ms'] / 1000:.1f}s")
        if u.get("prompt_tokens") is not None:
            meta.append(f"tokens {u.get('prompt_tokens')}/{u.get('completion_tokens')}")
        if sm["tool_calls"]:
            meta.append(f"工具 {len(sm['tool_calls'])} 次")
        lines += ["", f"### 第 {t.get('idx')} 轮（{'，'.join(meta)}）", ""]
        detail_mode = turn is not None
        inp, reply = _msg_text(t.get("input")), sm["text"]
        if detail_mode:
            lines += [
                "用户:",
                "",
                cut_block(inp, 1500),
                "",
                "agent:",
                "",
                cut_block(reply, 4000) or "（没有文本回复）",
            ]
        else:
            lines.append(f"- 用户: {truncate(inp, 160)}")
            lines.append(f"- agent: {truncate(reply, 300)}" if reply else "- agent: （没有文本回复）")
        if t.get("error"):
            err = t["error"]
            lines.append(f"- 错误: {err.get('code', '')} {truncate(err.get('message'), 300)}")
        calls = sm["tool_calls"]
        limit = len(calls) if detail_mode else 5
        for c in calls[:limit]:
            arg = truncate(c["arguments"], 400 if detail_mode else 80)
            res = (
                f" -> {truncate(c['result'], 400 if detail_mode else 80)}"
                if c.get("result") is not None
                else ""
            )
            lines.append(f"- 工具 `{c['name']}({arg})`{res}")
        if len(calls) > limit:
            lines.append(f"- ……另有 {len(calls) - limit} 次工具调用")
        if full and events is not None:
            evs = [e for e in events if e.get("turn_id") == t.get("id") and e.get("type") != "llm.delta"]
            lines += ["", "trace:"]
            for e in evs[:60]:
                lines.append(f"  - #{e.get('seq')} `{e.get('type')}` {_event_brief(e)}".rstrip())
            if len(evs) > 60:
                lines.append(f"  - ……另有 {len(evs) - 60} 个事件，请在 Web UI 查看完整 trace")
    if full and turn is None:
        lines += ["", "（detail=full 的 trace 步骤只在指定 turn 时展开，避免输出过长。）"]
    return "\n".join(lines) + "\n"


def _pair_differs(p: dict[str, Any]) -> bool:
    ft = p.get("final_text") or {}
    return (
        any(st.get("op") != "equal" for st in p.get("steps") or [])
        or ft.get("left") != ft.get("right")
        or bool(p.get("tool_args_diff"))
    )


def _metric_delta(label: str, left: Any, right: Any, digits: int = 0, unit: str = "") -> str | None:
    if not isinstance(left, int | float) or not isinstance(right, int | float) or left == right:
        return None
    return (
        f"{label} {num(left, digits)}{unit} -> {num(right, digits)}{unit}（{signed(right - left, digits)}）"
    )


def render_alignment(
    group: dict[str, Any],
    alignment: dict[str, Any],
    *,
    max_turns: int = 5,
    diff_lines: int = 20,
    web_url: str | None = None,
    unfinished: bool = False,
) -> str:
    """会话对比摘要：逐轮的步骤增减、指标差值、工具参数差异和回复 diff（只展开有差异的前几轮）。"""
    left, right = group.get("left") or {}, group.get("right") or {}

    def name(side: dict[str, Any]) -> str:
        who = f"{side.get('agent_name') or ''} {version_label(side.get('version_seq'))}".strip()
        return f"`{side.get('session_id')}`" + (f"（{who}）" if who else "")

    pairs = alignment.get("turns") or []
    differing = [p for p in pairs if _pair_differs(p)]
    lines = [
        "## 会话对比",
        "",
        f"- 左: {name(left)}",
        f"- 右: {name(right)}",
        f"- 共 {len(pairs)} 轮对齐，其中 {len(differing)} 轮有差异",
    ]
    if unfinished:
        lines.append("- **重放尚未完成**，以下为当前进度。")
    for p in differing[:max_turns]:
        steps = p.get("steps") or []
        deleted = [st["sig"] for st in steps if st.get("op") == "delete"]
        inserted = [st["sig"] for st in steps if st.get("op") == "insert"]
        status = p.get("status") or {}
        lines += ["", f"### 第 {p.get('pair')} 轮"]
        inp = (p.get("input") or {}).get("left") or (p.get("input") or {}).get("right")
        if inp:
            lines.append(f"- 输入: {truncate(inp, 160)}")
        if status.get("left") != status.get("right"):
            lines.append(f"- 状态: {status.get('left')} -> {status.get('right')}")
        if deleted or inserted:
            lines.append(
                f"- 步骤: 左侧独有 {len(deleted)} 个（{truncate(', '.join(deleted), 120) or '-'}），"
                f"右侧独有 {len(inserted)} 个（{truncate(', '.join(inserted), 120) or '-'}）"
            )
        m = p.get("metrics") or {}
        lm, rm = m.get("left") or {}, m.get("right") or {}
        deltas = [
            d
            for d in (
                _metric_delta("延迟", lm.get("latency_ms"), rm.get("latency_ms"), 0, " ms"),
                _metric_delta("tokens", lm.get("tokens"), rm.get("tokens")),
                _metric_delta("成本", lm.get("cost"), rm.get("cost"), 4),
                _metric_delta("工具调用", lm.get("tool_calls"), rm.get("tool_calls")),
            )
            if d
        ]
        if deltas:
            lines.append("- 指标: " + "；".join(deltas))
        for d in (p.get("tool_args_diff") or [])[:3]:
            for ch in (d.get("changes") or [])[:3]:
                old, new = truncate(ch.get("left"), 60), truncate(ch.get("right"), 60)
                lines.append(f"- 工具参数 `{ch.get('path')}`: {old} -> {new}")
        ft = p.get("final_text") or {}
        if ft.get("left") != ft.get("right"):
            diff = list(
                difflib.unified_diff(
                    (ft.get("left") or "").splitlines(),
                    (ft.get("right") or "").splitlines(),
                    "左",
                    "右",
                    lineterm="",
                    n=1,
                )
            )
            shown = diff[:diff_lines]
            lines += [
                "- 回复差异:",
                "",
                "```diff",
                *[truncate(x, 200) if len(x) > 200 else x for x in shown],
                "```",
            ]
            if len(diff) > len(shown):
                lines.append(f"（回复 diff 已截断，省略 {len(diff) - len(shown)} 行）")
    if len(differing) > max_turns:
        lines += [
            "",
            f"（还有 {len(differing) - max_turns} 轮有差异未展开，可调大 max_turns 或在 Web UI 查看。）",
        ]
    if web_url:
        lines += ["", f"Web UI: {web_url}"]
    return "\n".join(lines) + "\n"
