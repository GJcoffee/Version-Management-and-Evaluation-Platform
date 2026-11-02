"""会话服务：创建会话、执行一轮、取消、历史拼接、事件查询、SSE 订阅（详设 6、7、9.4）。

给评测 / 对比 / 重放模块的要点：

- ``run_turn(..., wait=True)`` 在本轮结束后返回 ``TurnOut``；执行失败**不抛异常**，看 ``status`` / ``error``。
  调用方任务被取消时，本轮会被取消（置位 cancel，等 Runner 收尾后再向上抛 CancelledError），不会留下孤儿任务；
- 同一会话同一时间只能执行一轮（``SessionBusy``）；唯一的例外是 ``concurrent=True``（锚定重放）：
  同一会话的多个并发轮次共用一个 :class:`EventSink`（seq 单调），会话直到最后一个轮次结束才回到空闲；
  ``history_override`` 非空时代替会话自身的历史（锚定重放）；
- ``hold_stream`` 让该会话的 SSE 在没有轮次运行的间隙也不发 ``idle``（重放轮次之间）；
- 历史拼接（6.1）：只取 ``status=ok`` 的轮次，按 idx 顺序拼接 ``input`` 与 ``output.messages``；
- 每个会话的事件由 :class:`EventSink` 写入 ``events`` 表（先发布到总线，后批量落库），``turn.end`` 到达前
  ``turns`` 与 ``sessions.stats / status`` 已更新完毕；
- 远端为主的轮次（Adapter 设置了 ``ctx.late_observe``，如 HiAgent）在 ``turn.end`` 后立即释放会话，
  规范指标（用时等）由后台任务补齐：补齐事件以该轮的 turn_id 追加到事件流，完成后改写轮次的 usage / 用时
  并更新会话 stats；补齐期间持有 SSE（``hold_stream``），完成后才发 ``idle``。``wait_trace`` /
  ``wait_traces`` 等待补齐结束；
- 进程启动后第一次使用本服务时（或应用启动时显式调用 :meth:`recover`），把遗留的 running 轮次标记为
  ``error``（code=INTERRUPTED），会话状态改为 idle；仍在补齐（``usage.trace_status=pending``）的轮次标记为
  ``unavailable``。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Text, exists, func, select, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.api.pagination import Page, paginate
from evalp.context import AppContext
from evalp.errors import NotFound, SessionBusy, ValidationFailed
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.runtime.adapters.base import AgentAdapter, RunContext, build_adapter
from evalp.runtime.eventbus import SubscriptionClosed
from evalp.runtime.events import AgentEvent, ev
from evalp.runtime.llm.ratelimit import PRIORITY_INTERACTIVE
from evalp.runtime.messages import Message, TurnOutput
from evalp.runtime.runner import Runner, TurnResult
from evalp.schemas.middleware import MiddlewareSelection
from evalp.schemas.session import SessionDetail, SessionOut, TurnError, TurnOut
from evalp.services.agent import AgentService
from evalp.services.event_sink import BLOB_KEY, EventSink, session_topic
from evalp.services.middleware import MiddlewareService
from evalp.services.version import ResolvedVersion, VersionService
from evalp.spec.models import CodeSpec
from evalp.store.models import Agent, AssetRevision, Event, Session, Turn, Version
from evalp.utils.time import now_iso

log = get_logger(__name__)

__all__ = ["EventSink", "Idle", "SessionService", "turn_out"]

MODES = ("chat", "eval", "replay", "judge")
DEFAULT_LIST_MODES = ("chat", "replay")  # 默认不列出评测与评审会话
REPLAY_MODES = ("anchored", "free")
CANCEL_WAIT_S = 10.0
SHUTDOWN_WAIT_S = 15.0
REPLAY_PAGE = 500
RELEASE_WAIT_S = 2.0
TITLE_MAX = 40
EMPTY_STATS: dict[str, Any] = {
    "turns": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "llm_calls": 0,
    "tool_calls": 0,
    "cost": 0.0,
}


@dataclass
class Idle:
    """SSE 流的结束标记：会话已空闲（没有正在执行的轮次）。"""

    last_seq: int


@dataclass(eq=False)
class _Running:
    """一个正在执行的轮次。"""

    session_id: str
    turn_id: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    result: TurnResult | None = None
    sink: EventSink | None = None
    finalized: bool = False  # 轮次结果已写库（对外可见为已结束），只差 turn.end 落库后释放槽位
    shared: bool = False  # 允许与同会话的其他 shared 轮次并发（锚定重放）
    released: asyncio.Event = field(default_factory=asyncio.Event)  # turn.end 已落库、会话已可接受下一轮
    middleware_snapshot: dict | None = None
    release_hold: Callable[[], None] | None = None  # 补齐期间对 SSE 的持有，交给补齐任务后置空


@dataclass(eq=False)
class _Observing:
    """一个正在后台补齐远端指标的轮次。"""

    session_id: str
    task: asyncio.Task[None]


class _FailingAdapter(AgentAdapter):
    """构造 Adapter 就失败时的占位：执行时抛出原始异常，让 Runner 走统一的 error 流程。"""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def run_turn(self, ctx: RunContext, history: list[Message], user_input: Message):  # type: ignore[override]
        raise self.exc
        yield  # pragma: no cover - 使本函数成为异步生成器


# ---------------------------------------------------------------- ORM -> DTO


def error_to_text(error: dict[str, Any] | None) -> str | None:
    """轮次错误 -> 存入 turns.error 的 JSON 字符串。"""
    if error is None:
        return None
    return json.dumps(
        {
            "code": error.get("code", "ERROR"),
            "message": error.get("message", ""),
            "retriable": bool(error.get("retriable", False)),
        },
        ensure_ascii=False,
    )


def error_from_text(text: str | None) -> TurnError | None:
    """turns.error 的 JSON 字符串 -> TurnError（兼容纯文本的旧数据）。"""
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "code" in data:
            return TurnError(
                code=str(data["code"]),
                message=str(data.get("message", "")),
                retriable=bool(data.get("retriable")),
            )
    except ValueError:
        pass
    return TurnError(code="ERROR", message=text, retriable=False)


def turn_out(t: Turn) -> TurnOut:
    """Turn ORM -> TurnOut。"""
    return TurnOut(
        id=t.id,
        session_id=t.session_id,
        idx=t.idx,
        pair_index=t.pair_index,
        status=t.status,  # type: ignore[arg-type]
        input=Message.model_validate(t.input),
        effective_input=Message.model_validate(t.effective_input) if t.effective_input else None,
        middleware_snapshot=t.middleware_snapshot,
        output=TurnOutput.model_validate(t.output) if t.output else None,
        usage=t.usage or {},
        cost=t.cost or 0.0,
        latency_ms=t.latency_ms,
        ttft_ms=t.ttft_ms,
        error=error_from_text(t.error),
        anchored_from_turn_id=t.anchored_from_turn_id,
        started_at=t.started_at,
        finished_at=t.finished_at,
    )


def event_out(r: Event) -> AgentEvent:
    """Event ORM -> AgentEvent。"""
    return AgentEvent(
        seq=r.seq,
        turn_id=r.turn_id,
        ts=r.ts,
        type=r.type,  # type: ignore[arg-type]
        step_id=r.step_id,
        parent_step_id=r.parent_step_id,
        payload=r.payload or {},
    )


def _session_out(s: Session, agent_name: str | None = None, version_seq: int | None = None) -> SessionOut:
    return SessionOut(
        id=s.id,
        agent_id=s.agent_id,
        version_id=s.version_id,
        mode=s.mode,  # type: ignore[arg-type]
        title=s.title,
        source_session_id=s.source_session_id,
        replay_mode=s.replay_mode,  # type: ignore[arg-type]
        status=s.status,  # type: ignore[arg-type]
        last_seq=s.last_seq,
        stats=s.stats or {},
        variables=s.variables or {},
        adapter_snapshot=s.adapter_snapshot,
        created_at=s.created_at,
        updated_at=s.updated_at,
        agent_name=agent_name,
        version_seq=version_seq,
    )


def _title_from(msg: Message) -> str | None:
    text = " ".join(msg.text().split())
    return (text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")) if text else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SessionService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._running: dict[str, list[_Running]] = {}  # session_id -> 正在执行的轮次（一般只有一个）
        self._by_turn: dict[str, _Running] = {}
        self._observations: dict[str, _Observing] = {}  # turn_id -> 后台补齐任务
        self._sinks: dict[str, list[Any]] = {}  # session_id -> [共享的 EventSink, 引用数]
        self._holds: dict[str, int] = {}  # session_id -> SSE 保持打开的持有数（重放期间）
        self._hold_events: dict[str, asyncio.Event] = {}  # 持有数归零时置位，唤醒等待中的 SSE
        self._recovered = False
        self._recover_lock = asyncio.Lock()

    # ------------------------------------------------------------ 启动恢复

    async def recover(self) -> int:
        """把遗留的 running 轮次标记为 error（INTERRUPTED），会话改为 idle；返回处理的轮次数。幂等。"""
        async with self._recover_lock:
            if self._recovered:
                return 0
            self._recovered = True
            return await self._recover_interrupted()

    async def _ensure_recovered(self) -> None:
        if not self._recovered:
            await self.recover()

    async def _recover_interrupted(self) -> int:
        now = now_iso()
        error = {
            "code": "INTERRUPTED",
            "message": "服务重启或异常退出，本轮被中断",
            "retriable": True,
        }
        count = 0
        async with self.ctx.db.write() as s:
            turns = (await s.execute(select(Turn).where(Turn.status == "running"))).scalars().all()
            for t in turns:
                if t.id in self._by_turn:
                    continue
                sess = await s.get(Session, t.session_id)
                seq = sess.last_seq if sess else 0
                t.status, t.error, t.finished_at = "error", error_to_text(error), now
                if t.middleware_snapshot:
                    snapshot = copy.deepcopy(t.middleware_snapshot)
                    for step in snapshot.get("steps", []):
                        if step.get("status") in ("pending", "running"):
                            step.update(status="error", error=error)
                    t.middleware_snapshot = snapshot
                if sess is not None:
                    # 补写 error + turn.end，保持"每轮以 turn.end 结束"的约束
                    end = ev("turn.end", status="error", usage=t.usage, cost=t.cost, error=error)
                    for i, e in enumerate((ev("error", **error), end), start=1):
                        s.add(
                            Event(
                                session_id=sess.id,
                                seq=seq + i,
                                turn_id=t.id,
                                ts=now,
                                type=e.type,
                                payload=e.payload,
                            )
                        )
                    sess.last_seq = seq + 2
                count += 1
            skip = list(self._running)
            await s.execute(
                update(Session)
                .where(Session.status == "running", Session.id.not_in(skip) if skip else True)
                .values(status="idle", updated_at=now)
            )
            note = "服务重启，Trace 补齐中断"
            unfinished = (
                await s.execute(
                    select(Turn).where(
                        Turn.status != "running",
                        func.json_extract(Turn.usage, "$.trace_status") == "pending",
                        Turn.id.not_in(list(self._observations)),
                    )
                )
            ).scalars().all()
            for t in unfinished:
                t.usage = {**t.usage, "trace_status": "unavailable", "trace_note": note}
                sess = await s.get(Session, t.session_id)
                if sess is not None:
                    metrics = ev("metrics", scope="turn", trace_status="unavailable", trace_note=note)
                    sess.last_seq += 1
                    s.add(Event(session_id=sess.id, seq=sess.last_seq, turn_id=t.id, ts=now,
                                type=metrics.type, payload=metrics.payload))
                count += 1
        if count:
            log.warning("session.recovered_interrupted_turns", count=count)
        return count

    # ------------------------------------------------------------ 创建 / 查询

    async def create(
        self,
        *,
        version_id: str,
        mode: Literal["chat", "eval", "replay", "judge"],
        title: str | None = None,
        variables: dict | None = None,
        source_session_id: str | None = None,
        replay_mode: str | None = None,
        adapter_snapshot: dict[str, Any] | None = None,
        environment: str = "production",
    ) -> SessionOut:
        """创建会话。``version_id`` 必须是版本 id（agent+tag 先用 ``VersionService.resolve_ref`` 解析）。

        ``transport: adapter`` 的版本按 ``environment``（production | test）固化接入快照；
        评测等已有快照的调用方用 ``adapter_snapshot`` 直接沿用。
        """
        await self._ensure_recovered()
        if mode not in MODES:
            raise ValidationFailed(
                f"mode 必须是 {'/'.join(MODES)} 之一", {"errors": [{"path": "mode", "message": mode}]}
            )
        if replay_mode is not None and replay_mode not in REPLAY_MODES:
            raise ValidationFailed(
                f"replay_mode 必须是 {'/'.join(REPLAY_MODES)} 之一",
                {"errors": [{"path": "replay_mode", "message": replay_mode}]},
            )
        async with self.ctx.db.write() as s:
            version = await s.get(Version, version_id)
            if version is None:
                raise NotFound(f"版本不存在: {version_id}", {"id": version_id})
            agent = await s.get(Agent, version.agent_id)
            assert agent is not None
            if source_session_id is not None and await s.get(Session, source_session_id) is None:
                raise NotFound(f"来源会话不存在: {source_session_id}", {"id": source_session_id})
            now = now_iso()
            from evalp.services.adapter import AdapterService

            snapshot = (
                adapter_snapshot if adapter_snapshot is not None
                else await self.ctx.svc(AdapterService).snapshot(s, version.spec, environment)
            )
            row = Session(
                id=new_id("ses"),
                agent_id=agent.id,
                version_id=version.id,
                mode=mode,
                title=title,
                source_session_id=source_session_id,
                replay_mode=replay_mode,
                status="idle",
                last_seq=0,
                stats=dict(EMPTY_STATS),
                variables=dict(variables or {}),
                adapter_snapshot=snapshot,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            await s.flush()
            return _session_out(row, agent.name, version.seq)

    async def get_session(self, session_id: str) -> SessionOut:
        """会话信息（不含轮次）；不存在抛 NotFound。"""
        async with self.ctx.db.read() as s:
            return await self._session_out(s, session_id)

    async def rename(self, session_id: str, title: str) -> SessionOut:
        """修改会话标题；不存在抛 NotFound。标题的长度校验在请求 DTO 里。"""
        async with self.ctx.db.write() as s:
            row = await s.get(Session, session_id)
            if row is None:
                raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
            row.title = title
            row.updated_at = now_iso()
            await s.flush()
            return await self._session_out(s, session_id)

    async def _session_out(self, s: AsyncSession, session_id: str) -> SessionOut:
        row = await s.get(Session, session_id)
        if row is None:
            raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
        agent = await s.get(Agent, row.agent_id)
        version = await s.get(Version, row.version_id)
        return _session_out(row, agent.name if agent else None, version.seq if version else None)

    async def get(
        self, session_id: str, *, include_turns: bool = True, include_events: bool = False
    ) -> SessionDetail:
        """会话详情，可带全部轮次与事件（事件里的大 payload 保持 ``$blob`` 形式）。"""
        async with self.ctx.db.read() as s:
            session = await self._session_out(s, session_id)
            turns: list[TurnOut] = []
            if include_turns:
                rows = (
                    (await s.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.idx)))
                    .scalars()
                    .all()
                )
                turns = [turn_out(t) for t in rows]
        events = await self.get_events(session_id) if include_events else None
        return SessionDetail(session=session, turns=turns, events=events)

    async def get_turn(self, turn_id: str) -> TurnOut:
        """按 id 取轮次；不存在抛 NotFound。"""
        async with self.ctx.db.read() as s:
            t = await s.get(Turn, turn_id)
            if t is None:
                raise NotFound(f"轮次不存在: {turn_id}", {"id": turn_id})
            return turn_out(t)

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        version_id: str | None = None,
        mode: str | list[str] | tuple[str, ...] | None = None,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[SessionOut]:
        """会话列表（新的在前）。``mode`` 缺省时排除 eval 和 judge；``agent_id`` 可为 id 或名称。"""
        modes = [mode] if isinstance(mode, str) else list(mode) if mode else list(DEFAULT_LIST_MODES)
        bad = [m for m in modes if m not in MODES]
        if bad:
            raise ValidationFailed(
                f"mode 不合法: {', '.join(bad)}", {"errors": [{"path": "mode", "message": bad[0]}]}
            )
        async with self.ctx.db.read() as s:
            stmt = select(Session).where(Session.mode.in_(modes))
            if agent_id:
                agent = await AgentService.find_orm(s, agent_id)
                stmt = stmt.where(Session.agent_id == (agent.id if agent else agent_id))
            if version_id:
                stmt = stmt.where(Session.version_id == version_id)
            if q:
                stmt = stmt.where(Session.title.like(_like(q), escape="\\"))
            page = await paginate(s, stmt, Session.id, limit=limit, cursor=cursor)
            agents = {a.id: a.name for a in (await s.execute(select(Agent))).scalars()}
            vids = {r.version_id for r in page.items}
            seqs = (
                dict((await s.execute(select(Version.id, Version.seq).where(Version.id.in_(vids)))).all())
                if vids
                else {}
            )
            return Page(
                items=[_session_out(r, agents.get(r.agent_id), seqs.get(r.version_id)) for r in page.items],
                next_cursor=page.next_cursor,
            )

    # ------------------------------------------------------------ 历史 / 事件

    async def get_history(self, session_id: str, upto_idx: int | None = None) -> list[Message]:
        """历史拼接（6.1）：``status=ok`` 且 ``idx < upto_idx`` 的各轮，依次取 input 与 output.messages。"""
        async with self.ctx.db.read() as s:
            if await s.get(Session, session_id) is None:
                raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
            stmt = select(Turn).where(Turn.session_id == session_id, Turn.status == "ok").order_by(Turn.idx)
            if upto_idx is not None:
                stmt = stmt.where(Turn.idx < upto_idx)
            turns = (await s.execute(stmt)).scalars().all()
        out: list[Message] = []
        for t in turns:
            out.append(Message.model_validate(t.effective_input or t.input))
            if t.output:
                o = TurnOutput.model_validate(t.output)
                out.extend(o.messages or ([o.final] if o.final else []))
        return out

    async def get_events(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        after_seq: int = 0,
        resolve_blobs: bool = False,
    ) -> list[AgentEvent]:
        """按 seq 升序返回事件。``resolve_blobs=True`` 时把 ``{"$blob": …}`` 展开为完整 payload。"""
        async with self.ctx.db.read() as s:
            stmt = select(Event).where(Event.session_id == session_id, Event.seq > after_seq)
            if turn_id:
                stmt = stmt.where(Event.turn_id == turn_id)
            rows = (await s.execute(stmt.order_by(Event.seq))).scalars().all()
        events = [event_out(r) for r in rows]
        if resolve_blobs:
            events = [await self.resolve_blob(e) for e in events]
        return events

    async def resolve_blob(self, event: AgentEvent) -> AgentEvent:
        """payload 被转存为 blob 时，读取完整内容替换回去。"""
        h = event.payload.get(BLOB_KEY)
        if not h:
            return event
        data = json.loads((await self.ctx.blobs.get_bytes(h)).decode("utf-8"))
        return event.model_copy(update={"payload": data})

    # ------------------------------------------------------------ 执行一轮

    @staticmethod
    def _coerce_input(value: Message | str | dict[str, Any]) -> Message:
        msg = (
            Message.user(value)
            if isinstance(value, str)
            else (value if isinstance(value, Message) else Message.model_validate(value))
        )
        if msg.role != "user":
            raise ValidationFailed(
                "输入消息的 role 必须是 user", {"errors": [{"path": "input.role", "message": msg.role}]}
            )
        return msg

    def running_turn_id(self, session_id: str) -> str | None:
        """会话当前正在执行的轮次 id（没有则 None；并发轮次时取最早开始的那个）。"""
        slots = self._running.get(session_id)
        return slots[0].turn_id if slots else None

    def running_turn_ids(self, session_id: str) -> list[str]:
        """会话当前所有正在执行的轮次 id（按开始顺序）。"""
        return [s.turn_id for s in self._running.get(session_id, ())]

    def is_running(self, session_id: str) -> bool:
        return bool(self._running.get(session_id))

    def is_observing(self, session_id: str) -> bool:
        """该会话是否有轮次的远端指标仍在后台补齐（用时等规范指标尚未定稿）。"""
        return any(o.session_id == session_id for o in self._observations.values())

    # ------------------------------------------------------------ SSE 保持打开（重放期间）

    def hold_stream(self, session_id: str) -> Callable[[], None]:
        """持有该会话的 SSE：持有期间没有轮次执行时也不发 ``idle``（重放在轮次之间会有空档）。

        返回释放函数（幂等）。持有数归零时唤醒正在等待的 SSE，由它们重新判断是否空闲。
        """
        self._holds[session_id] = self._holds.get(session_id, 0) + 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            n = self._holds.get(session_id, 1) - 1
            if n > 0:
                self._holds[session_id] = n
                return
            self._holds.pop(session_id, None)
            waiter = self._hold_events.pop(session_id, None)
            if waiter is not None:
                waiter.set()

        return release

    def is_held(self, session_id: str) -> bool:
        return self._holds.get(session_id, 0) > 0

    # ------------------------------------------------------------ 共享 EventSink

    def _acquire_sink(self, session_id: str, last_seq: int) -> EventSink:
        """取会话的 EventSink：已有（并发轮次）则复用，保证同一会话的 seq 单调且只有一份缓冲。"""
        entry = self._sinks.get(session_id)
        if entry is None:
            entry = [EventSink(self.ctx, session_id, last_seq), 0]
            self._sinks[session_id] = entry
        entry[1] += 1
        return entry[0]

    async def _release_sink(self, session_id: str, sink: EventSink) -> None:
        """归还 EventSink；最后一个使用者归还时关闭（做最后一次 flush）。"""
        entry = self._sinks.get(session_id)
        if entry is None or entry[0] is not sink:
            await sink.close()
            return
        entry[1] -= 1
        if entry[1] <= 0:
            del self._sinks[session_id]
            await sink.close()

    async def run_turn(
        self,
        session_id: str,
        input: Message | str,  # noqa: A002 - 与契约保持一致
        *,
        variables: dict | None = None,
        tool_mocks: dict | None = None,
        priority: int = PRIORITY_INTERACTIVE,
        pair_index: int | None = None,
        history_override: list[Message] | None = None,
        anchored_from_turn_id: str | None = None,
        concurrent: bool = False,
        wait: bool = True,
        middlewares: list[MiddlewareSelection] | None = None,
    ) -> TurnOut:
        """执行一轮。

        ``wait=False``：创建 running 状态的轮次后立即返回，后台执行；``wait=True``：执行完成后返回。
        执行失败不抛异常，通过 ``TurnOut.status`` / ``error`` 体现。会话正在执行时抛 ``SessionBusy``。
        ``concurrent=True``：允许与同会话中同样 ``concurrent`` 的轮次并发（仅锚定重放使用，
        每轮的上下文都由 ``history_override`` 给定，互不依赖）；与非并发轮次仍然互斥。
        """
        await self._ensure_recovered()
        msg = self._coerce_input(input)
        if middlewares and (concurrent or history_override is not None or anchored_from_turn_id):
            raise ValidationFailed("轮间中间件暂不支持重放上下文或并发轮次")
        cur = self._running.get(session_id)
        if cur and not concurrent and all(c.finalized for c in cur):
            # 轮次在库里已经是结束状态（客户端可能刚轮询到），只差 turn.end 落库：稍等槽位释放而不是报忙
            for c in list(cur):
                await self._wait_released(c.turn_id)
        cur = self._running.get(session_id)
        if cur and not (concurrent and all(c.shared for c in cur)):
            raise SessionBusy(
                f"会话 {session_id} 正在执行一轮",
                {"session_id": session_id, "turn_id": cur[0].turn_id},
            )
        slot = _Running(session_id=session_id, turn_id=new_id("trn"), shared=concurrent)
        self._running.setdefault(session_id, []).append(slot)  # 无 await：检查与占位是原子的
        self._by_turn[slot.turn_id] = slot
        try:
            async with self.ctx.db.read() as s:
                sess = await s.get(Session, session_id)
                if sess is None:
                    raise NotFound(f"会话不存在: {session_id}", {"id": session_id})
                session_vars = dict(sess.variables or {})
                version_id = sess.version_id
                adapter_snapshot = sess.adapter_snapshot
                if anchored_from_turn_id and await s.get(Turn, anchored_from_turn_id) is None:
                    raise NotFound(
                        f"锚定的轮次不存在: {anchored_from_turn_id}", {"id": anchored_from_turn_id}
                    )
            resolved = await self.ctx.svc(VersionService).get_resolved(version_id)
            resolved.adapter_snapshot = adapter_snapshot
            slot.middleware_snapshot = await self.ctx.svc(MiddlewareService).freeze(
                sess, msg, middlewares or [], adapter_snapshot=resolved.adapter_snapshot,
            )
            history = (
                list(history_override) if history_override is not None else await self.get_history(session_id)
            )
            async with self.ctx.db.write() as s:
                row = await s.get(Session, session_id)
                assert row is not None
                idx = (
                    await s.execute(select(func.max(Turn.idx)).where(Turn.session_id == session_id))
                ).scalar_one_or_none()
                turn = Turn(
                    id=slot.turn_id,
                    session_id=session_id,
                    idx=0 if idx is None else idx + 1,
                    pair_index=pair_index,
                    status="running",
                    input=msg.model_dump(mode="json", exclude_none=True),
                    middleware_snapshot=slot.middleware_snapshot,
                    usage={},
                    cost=0.0,
                    anchored_from_turn_id=anchored_from_turn_id,
                    started_at=now_iso(),
                )
                s.add(turn)
                row.status, row.updated_at = "running", now_iso()
                if row.title is None:
                    row.title = _title_from(msg)
                last_seq = row.last_seq
                await s.flush()
                out = turn_out(turn)
            slot.sink = self._acquire_sink(session_id, last_seq)
        except BaseException:
            self._release(slot)
            raise

        merged = {**session_vars, **(variables or {})}
        slot.task = asyncio.create_task(
            self._execute(slot, resolved, msg, history, merged, tool_mocks, priority),
            name=f"turn-{slot.turn_id}",
        )
        if not wait:
            return out
        return await self._await_slot(slot)

    async def _await_slot(self, slot: _Running) -> TurnOut:
        """等本轮结束并返回轮次；等待方被取消时取消本轮并等它收尾，避免留下孤儿任务。"""
        assert slot.task is not None
        try:
            await asyncio.shield(slot.task)
        except asyncio.CancelledError:
            slot.cancel.set()
            await asyncio.wait({slot.task}, timeout=CANCEL_WAIT_S)
            raise
        return await self.get_turn(slot.turn_id)

    async def wait_turn(self, turn_id: str) -> TurnOut:
        """等一个以 ``wait=False`` 启动的轮次结束（已结束则立即返回）；等待方被取消时本轮一并取消。"""
        slot = self._by_turn.get(turn_id)
        if slot is None or slot.task is None:
            return await self.get_turn(turn_id)
        return await self._await_slot(slot)

    def _release(self, slot: _Running) -> None:
        slots = self._running.get(slot.session_id)
        if slots is not None and slot in slots:
            slots.remove(slot)
            if not slots:
                del self._running[slot.session_id]
        self._by_turn.pop(slot.turn_id, None)
        slot.released.set()

    async def _wait_released(self, turn_id: str | None) -> None:
        """等该轮次的 turn.end 落库（槽位释放）。SSE 在发 Idle 前调用，保证 Idle 之后库里已是完整数据。"""
        slot = self._by_turn.get(turn_id) if turn_id else None
        if slot is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(slot.released.wait(), RELEASE_WAIT_S)

    async def _execute(
        self,
        slot: _Running,
        resolved: ResolvedVersion,
        msg: Message,
        history: list[Message],
        variables: dict[str, Any],
        tool_mocks: dict[str, Any] | None,
        priority: int,
    ) -> None:
        """后台执行一轮：Runner + EventSink，结束时更新 turns / sessions。"""
        sink = slot.sink
        assert sink is not None
        finalized = False
        adapter: AgentAdapter | None = None

        async def emit(event: AgentEvent) -> None:
            if event.turn_id is None:  # sink 可能被同会话的并发轮次共用，必须显式标注轮次
                event = event.with_turn(slot.turn_id)
            if event.type == "turn.start" and slot.middleware_snapshot:
                event = event.model_copy(update={"payload": {
                    **event.payload, "middleware_snapshot": copy.deepcopy(slot.middleware_snapshot),
                    "has_middleware": True,
                }})
            await sink.emit(event)
            if event.type == "turn.end":
                self._release(slot)  # turn.end 已发布并落库：此后该会话可以开始下一轮

        async def before_end(result: TurnResult) -> None:
            nonlocal finalized
            await self._finalize(slot, result)
            finalized = slot.finalized = True
            if rctx.late_observe is not None:  # 在 turn.end 发布之前持有 SSE，补齐完成后才发 idle
                slot.release_hold = self.hold_stream(slot.session_id)

        async def prepare_input() -> Message:
            assert slot.middleware_snapshot is not None
            return await self.ctx.svc(MiddlewareService).prepare(
                rctx, msg, slot.middleware_snapshot, emit,
            )

        try:
            timeout_s = 300.0
            seed: int | None = None
            try:
                spec = resolved.spec_model()
                timeout_s = float(spec.runtime.turn_timeout_s)
                raw_seed = spec.params.get("seed") if isinstance(spec, CodeSpec) else spec.model.params.seed
                seed = raw_seed if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) else None
                adapter = build_adapter(resolved, self.ctx)
            except Exception as e:  # noqa: BLE001 - Spec 解析 / Adapter 构造失败：本轮以 error 结束
                adapter = _FailingAdapter(e)
            rctx = RunContext(
                session_id=slot.session_id,
                turn_id=slot.turn_id,
                version=resolved,
                app=self.ctx,
                seed=seed,
                variables=variables,
                tool_mocks=tool_mocks,
                deadline=time.monotonic() + timeout_s,
                cancel=slot.cancel,
                priority=priority,
            )
            slot.result = await Runner().run(
                adapter, rctx, history, msg, emit, before_end=before_end,
                prepare_input=prepare_input if slot.middleware_snapshot else None,
            )
            if rctx.late_observe is not None and slot.release_hold is not None:
                self._start_observation(slot, rctx, slot.result, sink)
        except BaseException as e:  # noqa: BLE001 - 兜底：保证轮次与会话状态不会停留在 running
            if not isinstance(e, asyncio.CancelledError):
                log.exception("session.turn_crashed", turn_id=slot.turn_id, error=f"{type(e).__name__}: {e}")
            if not finalized:
                err = {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}", "retriable": False}
                result = TurnResult("error", None, {}, 0.0, 0, None, err)
                with contextlib.suppress(Exception):
                    await self._finalize(slot, result)
                    await sink.emit(ev("error", turn_id=slot.turn_id, **err))
                    await sink.emit(ev("turn.end", turn_id=slot.turn_id, status="error", error=err))
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            if slot.release_hold is not None:  # 没有交给补齐任务（执行被中断）：不再持有 SSE
                slot.release_hold()
            if adapter is not None:
                with contextlib.suppress(Exception):
                    await adapter.aclose()
            await self._release_sink(slot.session_id, sink)
            self._release(slot)

    # ------------------------------------------------------------ 远端指标补齐

    def _start_observation(
        self, slot: _Running, rctx: RunContext, result: TurnResult, sink: EventSink
    ) -> None:
        """启动后台补齐：额外持有一份 EventSink 与 SSE 持有，由补齐任务在结束时归还。"""
        release, slot.release_hold = slot.release_hold, None
        assert release is not None
        self._acquire_sink(slot.session_id, sink.last_seq)
        task = asyncio.create_task(
            self._observe(slot, rctx, result, sink, release), name=f"observe-{slot.turn_id}"
        )
        self._observations[slot.turn_id] = _Observing(slot.session_id, task)

    async def _observe(
        self,
        slot: _Running,
        rctx: RunContext,
        result: TurnResult,
        sink: EventSink,
        release: Callable[[], None],
    ) -> None:
        try:
            final = await Runner().observe(rctx, result, sink.emit)
            await self._finalize_observation(slot, result, final)
            await sink.flush()  # 先落库再放开 SSE，idle 之后库里已是完整数据
        except Exception as e:  # noqa: BLE001 - 后台任务的失败只记录，不能无声消失
            log.exception("session.observe_failed", turn_id=slot.turn_id, error=f"{type(e).__name__}: {e}")
        finally:
            self._observations.pop(slot.turn_id, None)
            await self._release_sink(slot.session_id, sink)
            release()

    async def _finalize_observation(self, slot: _Running, before: TurnResult, after: TurnResult) -> None:
        """补齐完成：改写轮次的 usage / 规范用时，并把补齐带来的差额累计进会话 stats。"""
        async with self.ctx.db.write() as s:
            t = await s.get(Turn, slot.turn_id)
            assert t is not None
            t.usage, t.cost = after.usage, after.cost
            t.latency_ms, t.ttft_ms = after.latency_ms, after.ttft_ms
            sess = await s.get(Session, slot.session_id)
            if sess is not None:
                stats = {**EMPTY_STATS, **(sess.stats or {})}
                for k in ("prompt_tokens", "completion_tokens", "llm_calls", "tool_calls"):
                    delta = int(after.usage.get(k, 0) or 0) - int(before.usage.get(k, 0) or 0)
                    stats[k] = stats.get(k, 0) + delta
                stats["cost"] = round(float(stats.get("cost", 0.0)) + after.cost - before.cost, 8)
                sess.stats = stats

    async def wait_trace(self, turn_id: str) -> TurnOut:
        """等该轮的远端指标补齐结束（没有补齐任务则立即返回），返回补齐后的轮次；等待方被取消不影响补齐。"""
        await self._ensure_recovered()
        observing = self._observations.get(turn_id)
        if observing is not None:
            await asyncio.wait({observing.task})
        return await self.get_turn(turn_id)

    async def wait_traces(self, session_id: str) -> None:
        """等该会话所有轮次的远端指标补齐结束（遗留的 pending 在恢复时已标为 unavailable）。"""
        await self._ensure_recovered()
        tasks = [o.task for o in self._observations.values() if o.session_id == session_id]
        if tasks:
            await asyncio.wait(tasks)

    async def _finalize(self, slot: _Running, result: TurnResult) -> None:
        """写入轮次结果，并累计会话的 stats / status。"""
        now = now_iso()
        async with self.ctx.db.write() as s:
            t = await s.get(Turn, slot.turn_id)
            if t is not None:
                if slot.middleware_snapshot:
                    for step in slot.middleware_snapshot["steps"]:
                        if step["status"] in ("pending", "running"):
                            step.update(status=result.status, error=result.error)
                    t.middleware_snapshot = slot.middleware_snapshot
                t.status = result.status
                if result.output:
                    t.output = result.output.model_dump(mode="json", exclude_none=True)
                elif result.partial_text:  # 取消 / 出错的轮次：只保存已流出的部分文本
                    t.output = TurnOutput(partial_text=result.partial_text).model_dump(
                        mode="json", exclude_none=True
                    )
                else:
                    t.output = None
                t.usage, t.cost = result.usage, result.cost
                t.latency_ms, t.ttft_ms = result.latency_ms, result.ttft_ms
                t.error = error_to_text(result.error)
                t.finished_at = now
            sess = await s.get(Session, slot.session_id)
            if sess is not None:
                stats = {**EMPTY_STATS, **(sess.stats or {})}
                stats["turns"] += 1
                for k in ("prompt_tokens", "completion_tokens", "llm_calls", "tool_calls"):
                    stats[k] = stats.get(k, 0) + int(result.usage.get(k, 0) or 0)
                stats["cost"] = round(float(stats.get("cost", 0.0)) + result.cost, 8)
                sess.stats = stats
                siblings = self._running.get(slot.session_id, ())
                others = [r for r in siblings if r is not slot and not r.finalized]
                if not others:  # 并发轮次：最后一个结束才回到空闲
                    sess.status = "error" if result.status in ("error", "timeout") else "idle"
                sess.updated_at = now
            slot.finalized = True  # 在事务内置位：同会话并发轮次的最后一个才能把会话置为空闲

    # ------------------------------------------------------------ 取消 / 关闭

    async def cancel_turn(self, turn_id: str) -> None:
        """取消正在执行的轮次，并等它收尾（最多 10s）。轮次已结束时什么也不做；轮次不存在抛 NotFound。"""
        await self._ensure_recovered()
        slot = self._by_turn.get(turn_id)
        if slot is None:
            async with self.ctx.db.read() as s:
                if await s.get(Turn, turn_id) is None:
                    raise NotFound(f"轮次不存在: {turn_id}", {"id": turn_id})
            return
        slot.cancel.set()
        if slot.task is not None:
            await asyncio.wait({slot.task}, timeout=CANCEL_WAIT_S)

    async def aclose(self) -> None:
        """应用关闭：取消所有进行中的轮次并等待收尾。"""
        slots = list(self._by_turn.values())
        for slot in slots:
            slot.cancel.set()
        observing = [o.task for o in self._observations.values()]
        for t in observing:  # 补齐中断的轮次在下次启动时标为 unavailable
            t.cancel()
        tasks = [s.task for s in slots if s.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_WAIT_S)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*observing, return_exceptions=True)

    # ------------------------------------------------------------ SSE（7.4）

    async def stream(self, session_id: str, after_seq: int = 0) -> AsyncIterator[AgentEvent | Idle]:
        """先回放、再订阅：产出 ``after_seq`` 之后的事件；本轮结束（或会话本来就空闲）后产出 :class:`Idle`。

        订阅发生在回放之前，回放期间的实时事件缓存在队列里，靠 seq 去重（没有 seq 的 ``llm.delta`` 例外：
        回放前已经落库的步骤，其残留的 delta 会被丢弃）。订阅者处理太慢被总线断开时，流直接结束且不发 Idle，
        客户端带 ``Last-Event-ID`` 重连即可从数据库补齐。
        """
        sub = self.ctx.bus.subscribe(session_topic(session_id))
        pending: asyncio.Future[AgentEvent] | None = None
        try:
            last = after_seq
            # 事件先发布后落库：订阅之前已发布但还在缓冲里的事件既不在队列也不在库里，
            # 所以回放前先让正在执行的轮次把缓冲刷到库（订阅之后发布的事件在队列里，靠 seq 去重）
            entry = self._sinks.get(session_id)
            if entry is not None:
                await entry[0].flush()
            # 回放开始时正在执行的轮次；看到它的 turn.end 才算结束。回放 / 积压 / 实时事件里的
            # turn.start 会补进来（并发轮次、重放的后一轮），turn.end 会移出
            busy_ids = {s.turn_id for s in self._running.get(session_id, ())}
            while True:
                async with self.ctx.db.read() as s:
                    rows = (
                        (
                            await s.execute(
                                select(Event)
                                .where(Event.session_id == session_id, Event.seq > last)
                                .order_by(Event.seq)
                                .limit(REPLAY_PAGE)
                            )
                        )
                        .scalars()
                        .all()
                    )
                if not rows:
                    break
                for r in rows:
                    e = event_out(r)
                    yield e
                    last = r.seq
                    if e.type == "turn.end" and e.turn_id:
                        busy_ids.discard(e.turn_id)

            # 回放结束时队列里的积压：按顺序处理，积压里已经回放过的步骤的 delta 是过期的
            backlog = sub.qsize()
            held: list[AgentEvent] = []
            try:
                for _ in range(backlog):
                    item: AgentEvent = await sub.get()
                    if item.seq is None:
                        held.append(item)
                        continue
                    if item.seq <= last:
                        held.clear()
                        continue
                    for h in held:
                        yield h
                    held.clear()
                    yield item
                    last = item.seq
                    if item.type == "turn.end" and item.turn_id:
                        busy_ids.discard(item.turn_id)
                    elif item.type == "turn.start" and item.turn_id:
                        busy_ids.add(item.turn_id)
            except SubscriptionClosed:
                return
            if busy_ids or self.is_held(session_id):
                for h in held:
                    yield h
            else:
                await self._wait_all_released(session_id)
                yield Idle(last)
                return
            while True:
                # 没有重放持有时直接等总线；持有期间还要能被"持有结束"唤醒（轮次之间不发 idle）
                if pending is None and not self.is_held(session_id):
                    if not busy_ids and sub.qsize() == 0:  # 补齐的最后一个事件与释放持有同时发生
                        await self._wait_all_released(session_id)
                        yield Idle(last)
                        return
                    try:
                        item = await sub.get()
                    except SubscriptionClosed:
                        return
                else:
                    if pending is None:
                        pending = asyncio.ensure_future(sub.get())
                    waits: set[asyncio.Future[Any]] = {pending}
                    hold_wait: asyncio.Future[Any] | None = None
                    if self.is_held(session_id):
                        hold_ev = self._hold_events.setdefault(session_id, asyncio.Event())
                        hold_wait = asyncio.ensure_future(hold_ev.wait())
                        waits.add(hold_wait)
                    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                    if hold_wait is not None and not hold_wait.done():
                        hold_wait.cancel()
                    if not pending.done():  # 持有结束：重新判断是否空闲
                        if not busy_ids and not self.is_held(session_id):
                            await self._wait_all_released(session_id)
                            yield Idle(last)
                            return
                        continue
                    done_get, pending = pending, None
                    try:
                        item = done_get.result()
                    except SubscriptionClosed:
                        return
                if item.seq is not None and item.seq <= last:
                    continue
                yield item
                if item.seq is not None:
                    last = item.seq
                if item.type == "turn.start" and item.turn_id:
                    busy_ids.add(item.turn_id)
                elif item.type == "turn.end":
                    if item.turn_id:
                        busy_ids.discard(item.turn_id)
                    await self._wait_released(item.turn_id)
                    if not busy_ids and not self.is_held(session_id):
                        yield Idle(last)
                        return
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                elif not pending.cancelled():
                    pending.exception()  # 取走异常（订阅已被关闭），避免 "exception was never retrieved"
            sub.close()

    async def _wait_all_released(self, session_id: str) -> None:
        """等该会话所有已结束（finalized）的轮次释放槽位，保证 Idle 之后库里已是完整数据。"""
        for slot in list(self._running.get(session_id, ())):
            await self._wait_released(slot.turn_id)

    # ------------------------------------------------------------ blob 访问控制

    async def blob_kind(
        self, blob_hash: str, session_id: str | None = None
    ) -> Literal["event", "asset"] | None:
        """判断 blob 是否允许读取：被 events 引用（``event``）或是资产修订的内容（``asset``）。

        传入 ``session_id`` 时只在该会话的事件里查找（走主键前缀，很快）；否则全表扫描事件。
        """
        pattern = f'%"{BLOB_KEY}":"{blob_hash}"%'
        async with self.ctx.db.read() as s:
            stmt = select(Event.seq).where(type_coerce(Event.payload, Text).like(pattern)).limit(1)
            if session_id:
                stmt = stmt.where(Event.session_id == session_id)
            if (await s.execute(stmt)).first() is not None:
                return "event"
            steps = func.json_each(Turn.middleware_snapshot, "$.steps").table_valued("value")
            stmt = select(Turn.id).where(
                exists().where(func.json_extract(steps.c.value, "$.result_blob_hash") == blob_hash)
            ).limit(1)
            if session_id:
                stmt = stmt.where(Turn.session_id == session_id)
            if (await s.execute(stmt)).first() is not None:
                return "event"
            if (
                await s.execute(select(AssetRevision.id).where(AssetRevision.blob_hash == blob_hash).limit(1))
            ).first() is not None:
                return "asset"
        return None
