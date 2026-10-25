"""会话对齐（详设 9.2，契约 contract-eval 第 3 节）。全部是纯函数，不访问数据库。

- 轮次对齐：``pair_index`` 或 ``idx``（:func:`pair_turns`）；
- 步骤对齐：对步骤签名序列做 LCS（``difflib.SequenceMatcher``），只输出 ``equal / insert / delete``
  （签名不同的步骤拆成一个 delete 加一个 insert）；
- 对齐上的工具调用：参数做 JSON diff（:func:`json_diff`）；
- 每轮两侧的指标汇总与 final_text。

步骤编号：事件有 ``step_id`` 时直接使用；``message.assistant`` 等没有 ``step_id`` 的步骤，``final``
用固定编号 ``"final"``，其他用 ``"e{seq}"``。step_id 每轮都从 s1 开始，需与 turn_id 组合才唯一。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from evalp.runtime.events import AgentEvent, step_signature

__all__ = [
    "AlignTurn",
    "Step",
    "align_pair",
    "align_steps",
    "extract_steps",
    "json_diff",
    "pair_turns",
    "turn_metrics",
]

FINAL_STEP_ID = "final"


@dataclass(frozen=True)
class Step:
    """一个可对齐的步骤。"""

    id: str
    sig: str
    args: Any = None  # 仅工具调用有值


@dataclass
class AlignTurn:
    """参与对齐的一轮（由服务层从数据库行构造）。"""

    id: str
    idx: int
    pair_index: int | None = None
    status: str = "ok"
    input_text: str | None = None
    final_text: str | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    events: list[AgentEvent | Mapping[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- 步骤


def extract_steps(events: Iterable[AgentEvent | Mapping[str, Any]]) -> list[Step]:
    """事件序列 -> 步骤序列（只含有签名的事件，见 ``step_signature``）。"""
    out: list[Step] = []
    for e in events:
        sig = step_signature(e)
        if sig is None:
            continue
        if isinstance(e, Mapping):
            step_id, seq, payload = e.get("step_id"), e.get("seq"), e.get("payload") or {}
        else:
            step_id, seq, payload = e.step_id, e.seq, e.payload
        if not step_id:
            step_id = FINAL_STEP_ID if sig == "final" else f"e{seq}"
        out.append(Step(str(step_id), sig, payload.get("arguments") if sig.startswith("tool:") else None))
    return out


def align_steps(left: Sequence[Step], right: Sequence[Step]) -> list[dict[str, Any]]:
    """步骤签名序列的 LCS 对齐，返回 ``[{op, left?, right?, sig}]``。"""
    matcher = SequenceMatcher(None, [s.sig for s in left], [s.sig for s in right], autojunk=False)
    out: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(
                {"op": "equal", "left": left[i].id, "right": right[j].id, "sig": left[i].sig}
                for i, j in zip(range(i1, i2), range(j1, j2), strict=True)
            )
            continue
        out.extend({"op": "delete", "left": s.id, "sig": s.sig} for s in left[i1:i2])
        out.extend({"op": "insert", "right": s.id, "sig": s.sig} for s in right[j1:j2])
    return out


# ---------------------------------------------------------------- JSON diff


def json_diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    """两个 JSON 值的差异：``[{path, left, right}]``。

    对象按键递归（``a.b``），数组按下标递归（``items[0]``），叶子或类型不同时整体比较；
    某一侧缺少该键时对应的值为 None，并附带 ``op``（``add`` / ``remove`` / ``change``）。
    """
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        out: list[dict[str, Any]] = []
        for k in list(left) + [k for k in right if k not in left]:
            p = f"{path}.{k}" if path else str(k)
            if k not in right:
                out.append({"path": p, "left": left[k], "right": None, "op": "remove"})
            elif k not in left:
                out.append({"path": p, "left": None, "right": right[k], "op": "add"})
            else:
                out.extend(json_diff(left[k], right[k], p))
        return out
    if isinstance(left, list) and isinstance(right, list):
        out = []
        for i in range(max(len(left), len(right))):
            p = f"{path}[{i}]"
            if i >= len(right):
                out.append({"path": p, "left": left[i], "right": None, "op": "remove"})
            elif i >= len(left):
                out.append({"path": p, "left": None, "right": right[i], "op": "add"})
            else:
                out.extend(json_diff(left[i], right[i], p))
        return out
    if left == right and type(left) is type(right):
        return []
    return [{"path": path, "left": left, "right": right, "op": "change"}]


# ---------------------------------------------------------------- 指标 / 轮次


def turn_metrics(t: AlignTurn | None) -> dict[str, Any] | None:
    """一轮的指标；该侧没有这一轮时为 None。"""
    if t is None:
        return None
    u = t.usage or {}
    return {
        "latency_ms": t.latency_ms,
        "ttft_ms": t.ttft_ms,
        "tokens": int(u.get("prompt_tokens", 0) or 0) + int(u.get("completion_tokens", 0) or 0),
        "cost": t.cost or 0.0,
        "llm_calls": int(u.get("llm_calls", 0) or 0),
        "tool_calls": int(u.get("tool_calls", 0) or 0),
    }


def pair_turns(
    left: Sequence[AlignTurn],
    right: Sequence[AlignTurn],
    by: str = "pair_index",
    *,
    left_by: str | None = None,
    right_by: str | None = None,
) -> list[tuple[int, AlignTurn | None, AlignTurn | None]]:
    """轮次对齐，返回按 key 升序的 ``(pair, left, right)``。

    ``by``：``"pair_index"`` 或 ``"idx"``；``left_by`` / ``right_by`` 可分别覆盖一侧（重放时原会话用 idx，
    新会话用 pair_index）。key 为 None 的轮次（单侧消息）不参与。一侧有多个同 key 的轮次时取最后一个。
    """

    def index(turns: Sequence[AlignTurn], how: str) -> dict[int, AlignTurn]:
        out: dict[int, AlignTurn] = {}
        for t in turns:
            key = t.pair_index if how == "pair_index" else t.idx
            if key is not None:
                out[key] = t
        return out

    li, ri = index(left, left_by or by), index(right, right_by or by)
    return [(k, li.get(k), ri.get(k)) for k in sorted(set(li) | set(ri))]


def align_pair(pair: int, left: AlignTurn | None, right: AlignTurn | None) -> dict[str, Any]:
    """对齐一组轮次，得到契约里 ``turns[]`` 的一项（附加 status / input 等便于前端展示的字段）。"""
    ls = extract_steps(left.events) if left else []
    rs = extract_steps(right.events) if right else []
    steps = align_steps(ls, rs)
    lmap, rmap = {s.id: s for s in ls}, {s.id: s for s in rs}
    args_diff: list[dict[str, Any]] = []
    for st in steps:
        if st["op"] == "equal" and st["sig"].startswith("tool:"):
            changes = json_diff(lmap[st["left"]].args, rmap[st["right"]].args)
            if changes:
                args_diff.append({"left": st["left"], "right": st["right"], "changes": changes})
    return {
        "pair": pair,
        "left_turn_id": left.id if left else None,
        "right_turn_id": right.id if right else None,
        "steps": steps,
        "tool_args_diff": args_diff,
        "metrics": {"left": turn_metrics(left), "right": turn_metrics(right)},
        "final_text": {
            "left": left.final_text if left else None,
            "right": right.final_text if right else None,
        },
        "status": {"left": left.status if left else None, "right": right.status if right else None},
        "input": {
            "left": left.input_text if left else None,
            "right": right.input_text if right else None,
        },
    }
