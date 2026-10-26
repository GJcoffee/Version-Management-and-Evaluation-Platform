"""对比与重放服务（详设 9，契约 contract-eval 第 3 节）。

- 对比组 ``CompareGroup(kind=session)``：``left_ref`` / ``right_ref`` 保存两侧会话 id；
  ``live`` 自动创建两个 chat 会话，``history`` 对比已有会话，``replay`` 由重放自动生成；
- 双栏发送 :meth:`run_pair`：两侧并发 ``run_turn(wait=False)``，任意一侧忙则 409；也可只发给一侧（分叉）；
- 对齐 :meth:`alignment`：把数据库行转成 :class:`AlignTurn` 后交给 ``compare.align`` 的纯函数；
  结果按两侧会话的 ``last_seq`` 与轮次状态缓存，有轮次在执行时不缓存；
- 重放 :meth:`replay`：创建 ``mode=replay`` 的新会话和对比组后在后台执行，立即返回。
  锚定模式各轮互不依赖，轮内并发（全局最多 ``REPLAY_CONCURRENCY`` 个轮次同时执行）；
  自由模式（新版本用自己的回复续写上下文）逐轮串行。重放期间新会话的 SSE 不发 ``idle``；
  重放状态（running / completed / failed）与所选轮次是对比组的正式字段。见 docs/DEVIATIONS.md。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import func, select

from evalp.api.pagination import Page, paginate
from evalp.compare.align import AlignTurn, align_pair, fork_turns, pair_turns
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
    CompareUpdate,
    ReplayAccepted,
)
from evalp.services.event_sink import BLOB_KEY
from evalp.services.session import SessionService, event_out
from evalp.services.version import VersionService
from evalp.store.models import Agent, CompareGroup, Event, Session, Turn, Version
from evalp.utils.time import now_iso

log = get_logger(__name__)

__all__ = ["REPLAY_CONCURRENCY", "CompareService"]

REPLAY_CONCURRENCY = 4  # 全部重放合计最多同时执行的轮次数
ALIGN_CACHE_SIZE = 64
# 对齐只需要这几类事件
_ALIGN_EVENT_TYPES = ("llm.response", "tool.call", "span.start", "message.assistant")
TITLE_MAX = 60
INTERRUPTED_MSG = "服务重启或异常退出，重放被中断"


def _short(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")


class CompareService:
    close_order = -5  # 先于 SessionService 关闭：取消后台重放，再让会话服务收尾

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.sessions = ctx.svc(SessionService)
        self._replays: dict[str, asyncio.Task[None]] = {}  # 新会话 id -> 后台重放任务
        self._replay_live: set[str] = set()  # 重放进行中的新会话 id（含任务创建前的瞬间）
        self._replay_slots = asyncio.Semaphore(REPLAY_CONCURRENCY)
        self._align_cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self.align_cache_hits = 0  # 测试 / 诊断用
        self.align_cache_misses = 0

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
        self,
        mode: str,
        left_id: str,
        right_id: str,
        title: str | None,
        *,
        replay_status: str | None = None,
        replay_turns: list[int] | None = None,
    ) -> CompareGroupOut:
        async with self.ctx.db.write() as s:
            row = CompareGroup(
                id=new_id("cmp"),
                kind="session",
                mode=mode,
                left_ref=left_id,
                right_ref=right_id,
                title=title,
                replay_status=replay_status,
                replay_turns=replay_turns,
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

    @staticmethod
    def _legacy_replay_turns(notes: str | None) -> list[int] | None:
        """迁移 0003 之前 replay 组把所选轮次写在 notes（JSON）里；兼容读取。"""
        if not notes:
            return None
        try:
            data = json.loads(notes)
        except ValueError:
            return None
        if isinstance(data, dict) and isinstance(data.get("replay_turns"), list):
            return [int(x) for x in data["replay_turns"] if isinstance(x, int)]
        return None

    def _replay_turns(self, row: CompareGroup) -> list[int] | None:
        if row.mode != "replay":
            return None
        return row.replay_turns if row.replay_turns is not None else self._legacy_replay_turns(row.notes)

    def _replay_state(self, row: CompareGroup) -> tuple[str | None, str | None]:
        """(状态, 错误)。库里是 running 但后台任务已不在（服务重启 / 异常退出）时按 failed 返回。"""
        if row.mode != "replay":
            return None, None
        status = row.replay_status
        if status is None:  # 迁移之前创建、迁移也没能推断的极端情况：按已有数据看不出来，保守地当作失败
            return "failed", INTERRUPTED_MSG
        if status == "running" and row.right_ref not in self._replay_live:
            return "failed", INTERRUPTED_MSG
        return status, row.replay_error

    @staticmethod
    def _user_notes(row: CompareGroup) -> str | None:
        """用户备注；旧版本写在 notes 里的重放元数据不是备注，不返回。"""
        if row.mode == "replay" and CompareService._legacy_replay_turns(row.notes) is not None:
            return None
        return row.notes

    async def _out(self, s: Any, row: CompareGroup) -> CompareGroupOut:
        status, error = self._replay_state(row)
        right = await s.get(Session, row.right_ref)
        return CompareGroupOut(
            id=row.id,
            mode=row.mode,  # type: ignore[arg-type]
            left=await self._side(s, row.left_ref),
            right=await self._side(s, row.right_ref),
            title=row.title,
            notes=self._user_notes(row),
            created_at=row.created_at,
            replay_mode=right.replay_mode if row.mode == "replay" and right is not None else None,  # type: ignore[arg-type]
            replay_status=status,  # type: ignore[arg-type]
            replay_turns=self._replay_turns(row),
            replay_error=error,
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

    async def update(self, compare_id: str, body: CompareUpdate) -> CompareGroupOut:
        """修改标题 / 备注（只改请求里出现的字段）。"""
        fields = body.model_fields_set & {"title", "notes"}
        if not fields:
            raise ValidationFailed(
                "至少需要 title 或 notes 之一", {"errors": [{"path": "title|notes", "message": "必填其一"}]}
            )
        async with self.ctx.db.write() as s:
            row = await s.get(CompareGroup, compare_id)
            if row is None or row.kind != "session":
                raise NotFound(f"对比不存在: {compare_id}", {"id": compare_id})
            if "title" in fields:
                row.title = body.title
            if "notes" in fields:
                row.notes = body.notes
            await s.flush()
        return await self.get(compare_id)

    # ------------------------------------------------------------ 双栏发送

    async def run_pair(
        self,
        compare_id: str,
        input: Message | str,  # noqa: A002
        variables: dict | None = None,
        side: Literal["both", "left", "right"] = "both",
    ) -> ComparePairAccepted:
        """执行同一输入（不等待结束），默认两侧并发。目标会话忙则抛 ``SessionBusy``。

        ``side=left|right`` 为单侧消息（分叉）：只发给一侧，``pair_index`` 为空，不参与轮次对齐；
        之后两侧的上下文不再相同（这一侧多了这条消息）。
        """
        g = await self._group(compare_id)
        if g.mode != "live":
            raise InvalidState("只有 live 模式的对比可以发送消息", {"mode": g.mode})
        targets = {"both": (g.left_ref, g.right_ref), "left": (g.left_ref,), "right": (g.right_ref,)}[side]
        for sid in targets:
            if self.sessions.is_running(sid):
                raise SessionBusy(
                    f"会话 {sid} 正在执行一轮",
                    {"session_id": sid, "turn_id": self.sessions.running_turn_id(sid)},
                )
        pair_index: int | None = None
        if side == "both":
            async with self.ctx.db.read() as s:
                pair = (
                    await s.execute(
                        select(func.max(Turn.pair_index)).where(
                            Turn.session_id.in_([g.left_ref, g.right_ref])
                        )
                    )
                ).scalar_one_or_none()
            pair_index = 0 if pair is None else pair + 1
        results = await asyncio.gather(
            *(
                self.sessions.run_turn(sid, input, variables=variables, pair_index=pair_index, wait=False)
                for sid in targets
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
        by_session = {sid: r for sid, r in zip(targets, results, strict=True)}
        left, right = by_session.get(g.left_ref), by_session.get(g.right_ref)
        return ComparePairAccepted(
            pair_index=pair_index,
            left_turn_id=left.id if left else None,
            right_turn_id=right.id if right else None,
            side=side,
        )

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

    async def _side_signature(self, session_id: str) -> tuple[tuple, bool]:
        """(缓存签名, 是否稳定)。签名 = 会话 last_seq + 各轮 (id, status, finished_at)；
        有轮次仍在执行（库里 running，或内存里还占着槽位）时不稳定，结果不能缓存。"""
        async with self.ctx.db.read() as s:
            sess = await s.get(Session, session_id)
            if sess is None:
                raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
            rows = (
                await s.execute(
                    select(Turn.id, Turn.status, Turn.finished_at).where(Turn.session_id == session_id)
                    .order_by(Turn.idx)
                )
            ).all()  # fmt: skip
        stable = not any(r[1] == "running" for r in rows) and not self.sessions.is_running(session_id)
        return (session_id, sess.last_seq, tuple(tuple(r) for r in rows)), stable

    async def alignment(self, compare_id: str) -> dict[str, Any]:
        """会话对齐结果（契约 ``GET /compare/{id}/alignment``）。

        两侧都没有在执行的轮次时按（两侧 last_seq、轮次状态、对比模式与所选轮次）缓存；执行中不缓存。
        """
        g = await self._group(compare_id)
        selected = self._replay_turns(g) if g.mode == "replay" else None
        sig_l, ok_l = await self._side_signature(g.left_ref)
        sig_r, ok_r = await self._side_signature(g.right_ref)
        replaying = g.mode == "replay" and g.right_ref in self._replay_live
        cacheable = ok_l and ok_r and not replaying
        key = (g.mode, tuple(selected) if selected is not None else None, sig_l, sig_r)
        if cacheable and key in self._align_cache:
            self._align_cache.move_to_end(key)
            self.align_cache_hits += 1
            return self._align_cache[key]
        self.align_cache_misses += 1
        left, right = await self._load_turns(g.left_ref), await self._load_turns(g.right_ref)
        forks: list[dict[str, Any]] = []
        if g.mode == "history":
            pairs = pair_turns(left, right, "idx")
        elif g.mode == "live":
            pairs = pair_turns(left, right, "pair_index")
            forks = fork_turns(left, right)
        else:
            # 重放：原会话用 idx，新会话的 pair_index 就是原轮次的 idx；只看选中的轮次
            pairs = pair_turns(left, right, left_by="idx", right_by="pair_index")
            if selected is not None:
                pairs = [p for p in pairs if p[0] in set(selected)]
        result = {"turns": [align_pair(k, lt, rt) for k, lt, rt in pairs], "forks": forks}
        if cacheable:
            # 计算期间状态变了（又开始了一轮）就不存：此时的结果可能含半截数据
            sig_l2, ok_l2 = await self._side_signature(g.left_ref)
            sig_r2, ok_r2 = await self._side_signature(g.right_ref)
            if ok_l2 and ok_r2 and sig_l2 == sig_l and sig_r2 == sig_r:
                self._align_cache[key] = result
                while len(self._align_cache) > ALIGN_CACHE_SIZE:
                    self._align_cache.popitem(last=False)
        return result

    # ------------------------------------------------------------ 重放

    async def replay(
        self, src_session_id: str, target_ref: str, turns: list[int] | None = None, mode: str = "anchored"
    ) -> ReplayAccepted:
        """创建重放会话与对比组，并在后台开始执行；立即返回。

        ``anchored``：第 k 轮的上下文取自原会话前 k 轮，各轮互不依赖，轮内并发；
        ``free``：新会话用自己前面各轮的回复作为上下文，逐轮串行（只重放所选轮次时，上下文只含所选的前几轮）。
        """
        if mode not in ("anchored", "free"):
            raise ValidationFailed(
                "mode 只能是 anchored 或 free", {"errors": [{"path": "mode", "message": mode}]}
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
            replay_mode=mode,
        )
        # 先登记"进行中"并持有 SSE，再建对比组：返回之后随时连上来的 SSE / 查询都看得到进行中
        self._replay_live.add(new.id)
        release = self.sessions.hold_stream(new.id)
        try:
            group = await self._insert(
                "replay", src.id, new.id, f"重放：{_short(src.title) or src.id}",
                replay_status="running", replay_turns=selected,
            )  # fmt: skip
        except BaseException:
            release()
            self._replay_live.discard(new.id)
            raise
        plan = [(by_idx[i].id, i, by_idx[i].input) for i in selected]
        task = asyncio.create_task(
            self._run_replay(group.id, src.id, new.id, plan, mode, release), name=f"replay-{new.id}"
        )
        self._replays[new.id] = task
        task.add_done_callback(lambda _t, sid=new.id: self._replays.pop(sid, None))
        return ReplayAccepted(session_id=new.id, compare_id=group.id)

    async def _run_replay(
        self,
        group_id: str,
        src_id: str,
        new_id_: str,
        plan: list[tuple[str, int, Any]],
        mode: str,
        release: Callable[[], None],
    ) -> None:
        """后台执行重放，结束时写入重放状态。

        单轮的 agent 错误（status=error / timeout）是重放的结果，不算重放失败；轮次没能启动 / 被取消，
        或整个任务出错 / 被取消才记为 failed。
        """
        status, error = "completed", None
        try:
            problems = await (
                self._replay_anchored(src_id, new_id_, plan)
                if mode == "anchored"
                else self._replay_free(src_id, new_id_, plan)
            )
            if problems:
                status, error = "failed", "；".join(problems[:5]) + ("…" if len(problems) > 5 else "")
        except asyncio.CancelledError:
            log.info("compare.replay_cancelled", session_id=new_id_)
            status, error = "failed", "重放被取消（服务关闭）"
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("compare.replay_crashed", session_id=new_id_, error=repr(e))
            status, error = "failed", f"重放出错: {type(e).__name__}: {e}"
        finally:
            # 先写状态、再放开 SSE：连接收到 idle 时，对比组里已经是终态
            with contextlib.suppress(Exception):
                await asyncio.shield(self._finish_replay(group_id, status, error))
            self._replay_live.discard(new_id_)
            release()

    async def _finish_replay(self, group_id: str, status: str, error: str | None) -> None:
        async with self.ctx.db.write() as s:
            row = await s.get(CompareGroup, group_id)
            if row is not None:
                row.replay_status, row.replay_error = status, error

    async def _replay_anchored(
        self, src_id: str, new_id_: str, plan: list[tuple[str, int, Any]]
    ) -> list[str]:
        """锚定模式：每轮的上下文只取决于原会话，各轮并发执行；返回没能完成的轮次的说明。

        Turn 行按计划顺序创建（``start_lock`` 保证 idx 与原轮次顺序一致），之后各轮并发运行；
        同时执行的轮次数受全局信号量限制。
        """
        problems: list[str] = []
        start_lock = asyncio.Lock()

        async def one(turn_id: str, idx: int, raw_input: Any) -> None:
            async with self._replay_slots:
                try:
                    async with start_lock:
                        history = await self.sessions.get_history(src_id, upto_idx=idx)
                        started = await self.sessions.run_turn(
                            new_id_,
                            Message.model_validate(raw_input),
                            history_override=history,
                            anchored_from_turn_id=turn_id,
                            pair_index=idx,
                            concurrent=True,
                            wait=False,
                        )
                    done = await self.sessions.wait_turn(started.id)
                    if done.status == "cancelled":
                        problems.append(f"第 {idx + 1} 轮被取消")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - 单轮没能启动只记下，不影响其他轮
                    log.warning("compare.replay_turn_failed", session_id=new_id_, idx=idx, error=repr(e))
                    problems.append(f"第 {idx + 1} 轮未能执行: {e}")

        tasks = [asyncio.create_task(one(*p), name=f"replay-{new_id_}-{p[1]}") for p in plan]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return sorted(problems)

    async def _replay_free(self, src_id: str, new_id_: str, plan: list[tuple[str, int, Any]]) -> list[str]:
        """自由模式：不覆盖历史，新会话用自己已有的轮次作为上下文，所以必须逐轮串行。

        某一轮失败（agent 出错）后继续下一轮：失败的轮次不进入历史（历史只取 status=ok 的轮次）。
        """
        problems: list[str] = []
        for _turn_id, idx, raw_input in plan:
            async with self._replay_slots:
                try:
                    done = await self.sessions.run_turn(
                        new_id_, Message.model_validate(raw_input), pair_index=idx, wait=True
                    )
                    if done.status == "cancelled":
                        problems.append(f"第 {idx + 1} 轮被取消")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("compare.replay_turn_failed", session_id=new_id_, idx=idx, error=repr(e))
                    problems.append(f"第 {idx + 1} 轮未能执行: {e}")
        return problems

    async def wait_replay(self, session_id: str, timeout: float | None = None) -> None:
        """等待某个重放会话的后台任务结束（测试 / CLI 用）；已结束或不存在时立即返回。"""
        task = self._replays.get(session_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)

    def replay_running(self, session_id: str) -> bool:
        """该重放会话的后台任务是否还在执行。"""
        return session_id in self._replay_live

    async def aclose(self) -> None:
        """应用关闭：取消还在执行的重放。"""
        tasks = list(self._replays.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
