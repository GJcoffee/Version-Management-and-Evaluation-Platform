"""对比与重放服务（详设 9，契约 contract-eval 第 3 节）。

- 对比组 ``CompareGroup(kind=session)``：``left_ref`` / ``right_ref`` 保存两侧会话 id；
  ``live`` 自动创建两个 chat 会话，``history`` 对比已有会话，``replay`` 由重放自动生成；
- 双栏发送 :meth:`run_pair`：两侧并发 ``run_turn(wait=False)``，任意一侧忙则 409；
- 对齐 :meth:`alignment`：把数据库行转成 :class:`AlignTurn` 后交给 ``compare.align`` 的纯函数；
- 重放（只做锚定模式）:meth:`replay`：创建 ``mode=replay`` 的新会话和对比组后，在后台逐轮执行，立即返回。
  一个会话同一时间只能执行一轮（SessionService 的约束），所以同一次重放内部按原轮次顺序串行，
  不同重放之间最多并发 ``REPLAY_CONCURRENCY`` 个；见 docs/DEVIATIONS.md。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from sqlalchemy import func, select

from evalp.api.pagination import Page, paginate
from evalp.compare.align import AlignTurn, align_pair, pair_turns
from evalp.context import AppContext
from evalp.errors import InvalidState, NotFound, SessionBusy, ValidationFailed
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.runtime.messages import Message
from evalp.schemas.compare import (
    CompareCreate,
    CompareGroupOut,
    ComparePairAccepted,
    CompareSideOut,
    ReplayAccepted,
)
from evalp.services.event_sink import BLOB_KEY
from evalp.services.session import SessionService, event_out
from evalp.services.version import VersionService
from evalp.store.models import Agent, CompareGroup, Event, Session, Turn, Version
from evalp.utils.time import now_iso

log = get_logger(__name__)

__all__ = ["REPLAY_CONCURRENCY", "CompareService"]

REPLAY_CONCURRENCY = 4
# 对齐只需要这几类事件
_ALIGN_EVENT_TYPES = ("llm.response", "tool.call", "span.start", "message.assistant")
TITLE_MAX = 60


def _short(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")


class CompareService:
    close_order = -5  # 先于 SessionService 关闭：取消后台重放，再让会话服务收尾

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.sessions = ctx.svc(SessionService)
        self._replays: dict[str, asyncio.Task[None]] = {}  # 新会话 id -> 后台重放任务
        self._replay_slots = asyncio.Semaphore(REPLAY_CONCURRENCY)

    # ------------------------------------------------------------ 创建 / 查询

    async def create(self, body: CompareCreate) -> CompareGroupOut:
        """创建对比组：live 自动建两个 chat 会话；history 校验两侧会话存在。"""
        if body.mode == "live":
            if not body.left.version_id or not body.right.version_id:
                raise ValidationFailed(
                    "live 模式需要 left.version_id 与 right.version_id",
                    {"errors": [{"path": "left/right.version_id", "message": "必填"}]},
                )
            versions = self.ctx.svc(VersionService)
            ids = [await versions.resolve_ref(r) for r in (body.left.version_id, body.right.version_id)]
            sides = [await self.sessions.create(version_id=vid, mode="chat") for vid in ids]
            left_id, right_id = sides[0].id, sides[1].id
        else:
            if not body.left.session_id or not body.right.session_id:
                raise ValidationFailed(
                    "history 模式需要 left.session_id 与 right.session_id",
                    {"errors": [{"path": "left/right.session_id", "message": "必填"}]},
                )
            left_id, right_id = body.left.session_id, body.right.session_id
            for sid in (left_id, right_id):
                await self.sessions.get_session(sid)  # 不存在时抛 NotFound
        return await self._insert(body.mode, left_id, right_id, body.title)

    async def _insert(
        self, mode: str, left_id: str, right_id: str, title: str | None, notes: dict | None = None
    ) -> CompareGroupOut:
        async with self.ctx.db.write() as s:
            row = CompareGroup(
                id=new_id("cmp"),
                kind="session",
                mode=mode,
                left_ref=left_id,
                right_ref=right_id,
                title=title,
                notes=json.dumps(notes, ensure_ascii=False) if notes else None,
                created_at=now_iso(),
            )
            s.add(row)
            await s.flush()
        return await self.get(row.id)

    async def _group(self, compare_id: str) -> CompareGroup:
        async with self.ctx.db.read() as s:
            row = await s.get(CompareGroup, compare_id)
        if row is None or row.kind != "session":
            raise NotFound(f"对比不存在: {compare_id}", {"id": compare_id})
        return row

    async def _side(self, s: Any, session_id: str) -> CompareSideOut:
        sess = await s.get(Session, session_id)
        if sess is None:
            raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
        agent = await s.get(Agent, sess.agent_id)
        version = await s.get(Version, sess.version_id)
        return CompareSideOut(
            session_id=sess.id,
            version_id=sess.version_id,
            version_seq=version.seq if version else None,
            agent_name=agent.name if agent else None,
        )

    async def _out(self, s: Any, row: CompareGroup) -> CompareGroupOut:
        return CompareGroupOut(
            id=row.id,
            mode=row.mode,  # type: ignore[arg-type]
            left=await self._side(s, row.left_ref),
            right=await self._side(s, row.right_ref),
            title=row.title,
            created_at=row.created_at,
        )

    async def get(self, compare_id: str) -> CompareGroupOut:
        async with self.ctx.db.read() as s:
            row = await s.get(CompareGroup, compare_id)
            if row is None or row.kind != "session":
                raise NotFound(f"对比不存在: {compare_id}", {"id": compare_id})
            return await self._out(s, row)

    async def list(self, *, limit: int = 50, cursor: str | None = None) -> Page[CompareGroupOut]:
        """对比列表（新的在前，只含会话对比）。"""
        async with self.ctx.db.read() as s:
            page = await paginate(
                s, select(CompareGroup).where(CompareGroup.kind == "session"), CompareGroup.id,
                limit=limit, cursor=cursor,
            )  # fmt: skip
            items = [await self._out(s, r) for r in page.items]
        return Page(items=items, next_cursor=page.next_cursor)

    # ------------------------------------------------------------ 双栏发送

    async def run_pair(
        self,
        compare_id: str,
        input: Message | str,
        variables: dict | None = None,  # noqa: A002
    ) -> ComparePairAccepted:
        """两侧并发执行同一输入（不等待结束）。任意一侧忙则抛 ``SessionBusy``。"""
        g = await self._group(compare_id)
        if g.mode != "live":
            raise InvalidState("只有 live 模式的对比可以发送消息", {"mode": g.mode})
        for sid in (g.left_ref, g.right_ref):
            if self.sessions.is_running(sid):
                raise SessionBusy(
                    f"会话 {sid} 正在执行一轮",
                    {"session_id": sid, "turn_id": self.sessions.running_turn_id(sid)},
                )
        async with self.ctx.db.read() as s:
            pair = (
                await s.execute(
                    select(func.max(Turn.pair_index)).where(Turn.session_id.in_([g.left_ref, g.right_ref]))
                )
            ).scalar_one_or_none()
        pair_index = 0 if pair is None else pair + 1
        results = await asyncio.gather(
            *(
                self.sessions.run_turn(sid, input, variables=variables, pair_index=pair_index, wait=False)
                for sid in (g.left_ref, g.right_ref)
            ),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            for r in results:  # 一侧没能启动：取消另一侧，避免只有单侧在跑
                if not isinstance(r, BaseException):
                    with contextlib.suppress(Exception):
                        await self.sessions.cancel_turn(r.id)
            raise errors[0]
        left, right = results
        return ComparePairAccepted(pair_index=pair_index, left_turn_id=left.id, right_turn_id=right.id)

    # ------------------------------------------------------------ 对齐

    async def _load_turns(self, session_id: str) -> list[AlignTurn]:
        async with self.ctx.db.read() as s:
            rows = (
                (await s.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.idx)))
                .scalars()
                .all()
            )
            ev_rows = (
                (
                    await s.execute(
                        select(Event)
                        .where(Event.session_id == session_id, Event.type.in_(_ALIGN_EVENT_TYPES))
                        .order_by(Event.seq)
                    )
                )
                .scalars()
                .all()
            )
        by_turn: dict[str, list] = {}
        for r in ev_rows:
            e = event_out(r)
            if BLOB_KEY in e.payload:
                e = await self.sessions.resolve_blob(e)
            by_turn.setdefault(r.turn_id, []).append(e)
        out: list[AlignTurn] = []
        for t in rows:
            final = (t.output or {}).get("final") if isinstance(t.output, dict) else None
            out.append(
                AlignTurn(
                    id=t.id,
                    idx=t.idx,
                    pair_index=t.pair_index,
                    status=t.status,
                    input_text=Message.model_validate(t.input).text(),
                    final_text=Message.model_validate(final).text() if final else None,
                    latency_ms=t.latency_ms,
                    ttft_ms=t.ttft_ms,
                    usage=t.usage or {},
                    cost=t.cost or 0.0,
                    events=by_turn.get(t.id, []),
                )
            )
        return out

    async def alignment(self, compare_id: str) -> dict[str, Any]:
        """会话对齐结果（契约 ``GET /compare/{id}/alignment``）。"""
        g = await self._group(compare_id)
        left, right = await self._load_turns(g.left_ref), await self._load_turns(g.right_ref)
        if g.mode == "history":
            pairs = pair_turns(left, right, "idx")
        elif g.mode == "live":
            pairs = pair_turns(left, right, "pair_index")
        else:
            # 重放：原会话用 idx，新会话的 pair_index 就是原轮次的 idx；只看选中的轮次
            pairs = pair_turns(left, right, left_by="idx", right_by="pair_index")
            selected = (json.loads(g.notes) if g.notes else {}).get("replay_turns")
            if selected is not None:
                pairs = [p for p in pairs if p[0] in set(selected)]
        return {"turns": [align_pair(k, lt, rt) for k, lt, rt in pairs]}

    # ------------------------------------------------------------ 重放（锚定模式）

    async def replay(
        self, src_session_id: str, target_ref: str, turns: list[int] | None = None, mode: str = "anchored"
    ) -> ReplayAccepted:
        """创建重放会话与对比组，并在后台开始执行；立即返回。"""
        if mode != "anchored":
            raise ValidationFailed(
                "一期只支持锚定模式（anchored）", {"errors": [{"path": "mode", "message": mode}]}
            )
        src = await self.sessions.get_session(src_session_id)
        async with self.ctx.db.read() as s:
            src_turns = (
                (
                    await s.execute(
                        select(Turn)
                        .where(Turn.session_id == src_session_id, Turn.status != "running")
                        .order_by(Turn.idx)
                    )
                )
                .scalars()
                .all()
            )
        by_idx = {t.idx: t for t in src_turns}
        if turns is not None:
            missing = sorted(set(turns) - set(by_idx))
            if missing:
                raise ValidationFailed(
                    f"原会话没有这些轮次（或仍在执行）: {missing}",
                    {"errors": [{"path": "turns", "message": f"不存在: {missing}"}]},
                )
            selected = sorted(set(turns))
        else:
            selected = sorted(by_idx)
        if not selected:
            raise ValidationFailed(
                "原会话没有可重放的轮次", {"errors": [{"path": "turns", "message": "为空"}]}
            )

        version_id = await self.ctx.svc(VersionService).resolve_ref(target_ref)
        new = await self.sessions.create(
            version_id=version_id,
            mode="replay",
            title=f"重放：{src.title or src.id}",
            variables=src.variables,
            source_session_id=src.id,
            replay_mode="anchored",
        )
        group = await self._insert(
            "replay", src.id, new.id, f"重放：{_short(src.title) or src.id}", {"replay_turns": selected}
        )
        task = asyncio.create_task(
            self._run_replay(src.id, new.id, [(by_idx[i].id, i, by_idx[i].input) for i in selected]),
            name=f"replay-{new.id}",
        )
        self._replays[new.id] = task
        task.add_done_callback(lambda _t, sid=new.id: self._replays.pop(sid, None))
        return ReplayAccepted(session_id=new.id, compare_id=group.id)

    async def _run_replay(self, src_id: str, new_id_: str, plan: list[tuple[str, int, Any]]) -> None:
        """后台逐轮执行：第 k 轮的上下文 = 原会话前 k 轮的历史；某一轮失败不影响后面的轮次。"""
        try:
            async with self._replay_slots:
                for turn_id, idx, raw_input in plan:
                    try:
                        history = await self.sessions.get_history(src_id, upto_idx=idx)
                        await self.sessions.run_turn(
                            new_id_,
                            Message.model_validate(raw_input),
                            history_override=history,
                            anchored_from_turn_id=turn_id,
                            pair_index=idx,
                            wait=True,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - 单轮失败只记日志，继续下一轮
                        log.warning("compare.replay_turn_failed", session_id=new_id_, idx=idx, error=repr(e))
        except asyncio.CancelledError:
            log.info("compare.replay_cancelled", session_id=new_id_)
            raise

    async def wait_replay(self, session_id: str, timeout: float | None = None) -> None:
        """等待某个重放会话的后台任务结束（测试 / CLI 用）；已结束或不存在时立即返回。"""
        task = self._replays.get(session_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)

    def replay_running(self, session_id: str) -> bool:
        """该重放会话的后台任务是否还在执行。"""
        return session_id in self._replays

    async def aclose(self) -> None:
        """应用关闭：取消还在执行的重放。"""
        tasks = list(self._replays.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
