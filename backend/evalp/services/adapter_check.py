"""Adapter 自检：对一个版本跑一组"协议合规"检查并给出可读的报告。

检查项（顺序执行，前一项失败时依赖它的后续项会被跳过）：

- ``health``：健康检查（HTTP agent 的 ``/evalp/health``、子进程的命令可执行、配置型的 Provider 可用）；
- ``single``：单轮对话成功，且产出了非空的最终回复；
- ``multi_turn``：带着上一轮的历史再来一轮，历史被正确传给 agent（能验证时验证，不能验证给 ``warn``）；
- ``timeout``：极短的超时到期后本轮以 ``timeout`` 结束，agent 被及时终止（子进程 pid 已不存在）；
- ``cancel``：轮次执行中取消，以 ``cancelled`` 结束，agent 被及时终止；
- ``events``：汇总前面所有轮次里 agent 实际产出的事件，检查顺序、配对、协议违例，逐条列出问题。

自检不写数据库、不产生会话（使用临时 id），但会真实调用 agent（配置型会真实调用 LLM）。
agent 响应得比探测超时还快时，``timeout`` / ``cancel`` 无法验证，给出 ``skip``。
报告里的任何异常都不会向外抛出：agent 的任何失败都体现为某个检查项的 ``fail``。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field

from evalp.context import AppContext
from evalp.errors import EvalpError
from evalp.ids import new_id
from evalp.logging import get_logger
from evalp.runtime.adapters.base import AgentAdapter, RunContext, build_adapter
from evalp.runtime.adapters.protocol import PROTOCOL_ERROR
from evalp.runtime.events import AgentEvent
from evalp.runtime.messages import Message
from evalp.runtime.runner import Runner, TurnResult
from evalp.services.version import ResolvedVersion, VersionService

log = get_logger(__name__)

Status = Literal["pass", "fail", "warn", "skip"]

TIMEOUT_PROBE_S = 0.05  # 超时探测：给本轮的预算
CANCEL_AFTER_S = 0.05  # 取消探测：开始多久后取消
STOP_WITHIN_S = 5.0  # 超时 / 取消之后，应在这么久内结束
MAX_TURN_S = 60.0  # 单轮的最长等待（取 Spec 的 turn_timeout_s 与它的较小者）
SINGLE_INPUT = "你好，这是一次适配器自检，请简短回复。"
MULTI_INPUT = "请继续，并简短回复。"


class CheckItem(BaseModel):
    name: str
    title: str
    status: Status
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class CheckReport(BaseModel):
    version_id: str
    agent_name: str | None = None
    kind: str | None = None
    transport: str | None = None
    ok: bool = True  # 没有 fail
    summary: dict[str, int] = Field(default_factory=dict)  # 各状态的数量
    items: list[CheckItem] = Field(default_factory=list)
    duration_ms: int = 0


class _Recorder(AgentAdapter):
    """包一层真正的 Adapter，记录它产出的原始事件（Runner 修改之前）和抛出的异常。"""

    def __init__(self, inner: AgentAdapter) -> None:
        self.inner = inner
        self.runs: list[list[AgentEvent]] = []
        self.raised: list[BaseException | None] = []

    async def run_turn(  # type: ignore[override]
        self, ctx: RunContext, history: list[Message], user_input: Message
    ) -> AsyncIterator[AgentEvent]:
        events: list[AgentEvent] = []
        self.runs.append(events)
        self.raised.append(None)
        idx = len(self.raised) - 1
        agen = self.inner.run_turn(ctx, history, user_input)
        try:
            async for e in agen:
                events.append(e)
                yield e
        except Exception as e:  # noqa: BLE001 - 记录后原样抛给 Runner
            self.raised[idx] = e
            raise
        finally:
            with contextlib.suppress(Exception):
                await agen.aclose()  # type: ignore[attr-defined]

    async def healthcheck(self):  # type: ignore[no-untyped-def,override]
        return await self.inner.healthcheck()

    async def aclose(self) -> None:
        await self.inner.aclose()


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def _wait_pid_gone(pid: int | None, seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not _pid_alive(pid)


class _Checker:
    def __init__(self, app: AppContext, version: ResolvedVersion) -> None:
        self.app = app
        self.version = version
        self.rec: _Recorder | None = None
        self.items: list[CheckItem] = []
        self.history: list[Message] = []
        self.results: dict[str, TurnResult] = {}
        self.aborted: set[int] = set()  # 被探测主动中断的执行序号：不检查配对 / turn.end
        try:
            spec = version.spec_model()
            self.turn_budget = min(float(spec.runtime.turn_timeout_s), MAX_TURN_S)
        except Exception:  # noqa: BLE001
            self.turn_budget = MAX_TURN_S

    # -- 工具

    def _rctx(self, *, budget: float | None = None, cancel: asyncio.Event | None = None) -> RunContext:
        return RunContext(
            session_id=new_id("ses"),
            turn_id=new_id("trn"),
            version=self.version,
            app=self.app,
            deadline=time.monotonic() + (self.turn_budget if budget is None else budget),
            cancel=cancel or asyncio.Event(),
        )

    async def _run(
        self,
        history: list[Message],
        text: str,
        *,
        budget: float | None = None,
        cancel: asyncio.Event | None = None,
    ) -> TurnResult:
        assert self.rec is not None

        async def sink(_: AgentEvent) -> None:
            return None

        return await Runner().run(
            self.rec,
            self._rctx(budget=budget, cancel=cancel),
            history,
            Message(role="user", content=text),
            sink,
        )

    def _add(
        self, name: str, title: str, status: Status, message: str, t0: float, /, **details: Any
    ) -> CheckItem:
        item = CheckItem(
            name=name,
            title=title,
            status=status,
            message=message,
            details=details,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        self.items.append(item)
        return item

    @staticmethod
    def _err_text(result: TurnResult) -> str:
        e = result.error or {}
        return f"{result.status}：{e.get('code', '')} {_short(e.get('message', ''), 300)}".strip()

    def _last_raw(self) -> list[AgentEvent]:
        assert self.rec is not None
        return self.rec.runs[-1] if self.rec.runs else []

    def _pid(self) -> int | None:
        return getattr(self.rec.inner, "last_pid", None) if self.rec else None

    # -- 检查项

    async def health(self) -> None:
        t0 = time.monotonic()
        assert self.rec is not None
        try:
            res = await asyncio.wait_for(self.rec.healthcheck(), 15)
        except Exception as e:  # noqa: BLE001
            self._add(
                "health", "健康检查", "fail", f"健康检查抛出异常: {type(e).__name__}: {_short(str(e))}", t0
            )
            return
        status: Status = "pass" if res.ok else "fail"
        self._add(
            "health", "健康检查", status, res.message or ("健康" if res.ok else "不健康"), t0, **res.details
        )

    async def single(self) -> bool:
        t0 = time.monotonic()
        result = await self._run([], SINGLE_INPUT)
        self.results["single"] = result
        if result.status != "ok":
            self._add(
                "single", "单轮对话", "fail", self._err_text(result), t0,
                error=result.error, turn_status=result.status,
            )  # fmt: skip
            return False
        final = result.output.final if result.output else None
        text = final.text() if final else ""
        if not text.strip():
            self._add("single", "单轮对话", "fail", "本轮成功结束，但最终回复为空", t0)
            return False
        self.history = [Message(role="user", content=SINGLE_INPUT), *(result.output.messages or [final])]
        self._add(
            "single", "单轮对话", "pass", f"回复: {_short(text, 60)}", t0,
            usage=result.usage, latency_ms=result.latency_ms, ttft_ms=result.ttft_ms,
            event_types=sorted({e.type for e in self._last_raw()}),
        )  # fmt: skip
        return True

    async def multi_turn(self) -> None:
        t0 = time.monotonic()
        result = await self._run(self.history, MULTI_INPUT)
        self.results["multi"] = result
        if result.status != "ok":
            self._add("multi_turn", "多轮对话", "fail", self._err_text(result), t0, error=result.error)
            return
        raw = self._last_raw()
        expected = len(self.history)
        start = next((e for e in raw if e.type == "turn.start"), None)
        reported = start.payload.get("history_len") if start else None
        if isinstance(reported, int):
            if reported != expected:
                self._add(
                    "multi_turn", "多轮对话", "fail",
                    f"传给 agent 的历史有 {expected} 条消息，agent 报告收到 {reported} 条", t0,
                    expected=expected, reported=reported,
                )  # fmt: skip
            else:
                self._add("multi_turn", "多轮对话", "pass", f"agent 收到了 {expected} 条历史消息", t0)
            return
        requests = [e for e in raw if e.type == "llm.request"]
        if requests:
            seen = any(
                any(
                    isinstance(m, dict) and m.get("content") == SINGLE_INPUT
                    for m in r.payload.get("messages") or []
                )
                for r in requests
            )
            if seen:
                self._add("multi_turn", "多轮对话", "pass", "LLM 请求里带上了上一轮的内容", t0)
            else:
                self._add(
                    "multi_turn", "多轮对话", "fail", "LLM 请求里没有上一轮的消息，历史可能没有被使用", t0
                )  # fmt: skip
            return
        self._add(
            "multi_turn", "多轮对话", "warn",
            "第二轮成功，但 agent 没有报告 history_len 也没有 llm.request，无法确认历史是否被使用",
            t0, history_len=expected,
        )  # fmt: skip

    async def timeout(self) -> None:
        t0 = time.monotonic()
        result = await self._run([], SINGLE_INPUT, budget=TIMEOUT_PROBE_S)
        elapsed = time.monotonic() - t0
        pid = self._pid()
        self.aborted.add(len(self.rec.runs) - 1)  # type: ignore[union-attr]
        if result.status == "ok":
            self._add(
                "timeout", "超时终止", "skip",
                f"agent 在 {int(TIMEOUT_PROBE_S * 1000)}ms 内就完成了，无法验证超时终止", t0,
            )  # fmt: skip
            return
        if result.status != "timeout":
            self._add("timeout", "超时终止", "fail", f"期望 timeout，实际 {self._err_text(result)}", t0)
            return
        problems = []
        if elapsed > STOP_WITHIN_S:
            problems.append(f"到期后 {elapsed:.1f}s 才结束")
        if pid is not None and not await _wait_pid_gone(pid):
            problems.append(f"子进程 {pid} 超时后仍在运行")
        if problems:
            self._add("timeout", "超时终止", "fail", "；".join(problems), t0, pid=pid)
        else:
            self._add("timeout", "超时终止", "pass", f"超时后 {int(elapsed * 1000)}ms 内终止", t0, pid=pid)

    async def cancel(self) -> None:
        t0 = time.monotonic()
        cancel = asyncio.Event()
        task = asyncio.create_task(self._run([], SINGLE_INPUT, cancel=cancel))
        done, _ = await asyncio.wait({task}, timeout=CANCEL_AFTER_S)
        if done:
            result = task.result()
            self._add(
                "cancel", "取消", "skip",
                f"agent 在 {int(CANCEL_AFTER_S * 1000)}ms 内就结束了（{result.status}），无法验证取消", t0,
            )  # fmt: skip
            return
        t_cancel = time.monotonic()
        cancel.set()
        try:
            result = await asyncio.wait_for(task, STOP_WITHIN_S + 2)
        except TimeoutError:
            self._add("cancel", "取消", "fail", f"取消后 {STOP_WITHIN_S:.0f}s 仍未结束", t0)
            return
        elapsed = time.monotonic() - t_cancel
        pid = self._pid()
        self.aborted.add(len(self.rec.runs) - 1)  # type: ignore[union-attr]
        problems = []
        if result.status != "cancelled":
            problems.append(f"期望 cancelled，实际 {self._err_text(result)}")
        if elapsed > STOP_WITHIN_S:
            problems.append(f"取消后 {elapsed:.1f}s 才结束")
        if pid is not None and not await _wait_pid_gone(pid):
            problems.append(f"子进程 {pid} 取消后仍在运行")
        if problems:
            self._add("cancel", "取消", "fail", "；".join(problems), t0, pid=pid)
        else:
            self._add("cancel", "取消", "pass", f"取消后 {int(elapsed * 1000)}ms 内终止", t0, pid=pid)

    def events(self) -> None:
        """检查前面所有轮次里 agent 产出的原始事件。"""
        t0 = time.monotonic()
        assert self.rec is not None
        problems: list[str] = []
        warnings: list[str] = []
        total = 0
        for n, (events, raised) in enumerate(zip(self.rec.runs, self.rec.raised, strict=True), start=1):
            total += len(events)
            where = f"第 {n} 次执行"
            if raised is not None:
                problems.append(f"{where}: 适配器异常 {type(raised).__name__}: {_short(str(raised))}")
            types = [e.type for e in events]
            if "turn.start" in types and types[0] != "turn.start":
                problems.append(f"{where}: turn.start 不是第一个事件（实际第一个是 {types[0]}）")
            if "turn.end" in types and types.index("turn.end") != len(types) - 1:
                problems.append(f"{where}: turn.end 之后还有事件")
            for e in events:
                if e.type == "error" and e.payload.get("code") == PROTOCOL_ERROR:
                    problems.append(f"{where}: {e.payload.get('message')}")
            if n - 1 in self.aborted:
                continue  # 被探测主动中断的执行，事件流天然不完整
            problems += [f"{where}: {p}" for p in _pairing_problems(events)]
            if (
                not any(e.type == "message.assistant" for e in events)
                and "turn.end" not in types
                and not any(e.type == "error" for e in events)
            ):
                warnings.append(f"{where}: agent 没有产出 message.assistant")
        ok_calls = sum(
            1 for r in self.results.values() if r.status == "ok" and (r.usage.get("llm_calls") or 0) == 0
        )
        if ok_calls:
            warnings.append(
                "有轮次没有任何 llm.response，用量 / 成本统计将为 0（可用 evalp-sdk 的 llm_call 上报）"
            )
        if problems:
            self._add(
                "events",
                "事件合规",
                "fail",
                f"{len(problems)} 个问题: {problems[0]}",
                t0,
                problems=problems,
                warnings=warnings,
                events=total,
            )
        elif warnings:
            self._add("events", "事件合规", "warn", warnings[0], t0, warnings=warnings, events=total)
        else:
            self._add("events", "事件合规", "pass", f"共检查 {total} 个事件，没有发现问题", t0, events=total)


def _pairing_problems(events: list[AgentEvent]) -> list[str]:
    """llm.request / llm.response、tool.call / tool.result、span.start / span.end 必须成对。"""
    out: list[str] = []
    open_steps: dict[tuple[str, str], str] = {}
    pairs = {
        "llm.response": "llm.request",
        "tool.result": "tool.call",
        "span.end": "span.start",
    }
    starts = set(pairs.values())
    for e in events:
        if e.type in starts and e.step_id:
            open_steps[(e.type, e.step_id)] = e.type
        elif e.type in pairs:
            key = (pairs[e.type], e.step_id or "")
            if key not in open_steps:
                out.append(f"{e.type}(step_id={e.step_id}) 没有对应的 {pairs[e.type]}")
            else:
                del open_steps[key]
    for (typ, step), _ in open_steps.items():
        out.append(f"{typ}(step_id={step}) 没有对应的结束事件")
    return out


async def run_adapter_check(ctx: AppContext, version_id: str) -> CheckReport:
    """对一个版本执行 Adapter 自检，返回报告。版本不存在抛 NotFound，其余失败都写进报告。"""
    t_all = time.monotonic()
    version = await ctx.svc(VersionService).get_resolved(version_id)
    report = CheckReport(version_id=version.id, agent_name=version.agent_name)
    spec_dict = version.spec if isinstance(getattr(version, "spec", None), dict) else {}
    report.kind = spec_dict.get("kind")
    report.transport = spec_dict.get("transport")
    checker = _Checker(ctx, version)
    try:
        try:
            checker.rec = _Recorder(build_adapter(version, ctx))
        except EvalpError as e:
            checker._add(
                "health", "构造 Adapter", "fail", f"{e.code}: {_short(e.message, 300)}", time.monotonic()
            )
            return _finish(report, checker, t_all)
        await checker.health()
        single_ok = await checker.single()
        if single_ok:
            await checker.multi_turn()
            await checker.timeout()
            await checker.cancel()
        else:
            for name, title in (("multi_turn", "多轮对话"), ("timeout", "超时终止"), ("cancel", "取消")):
                checker._add(name, title, "skip", "单轮对话失败，跳过", time.monotonic())
        checker.events()
    except Exception as e:  # noqa: BLE001 - 自检本身不能崩
        log.exception("adapter_check.crashed", version_id=version_id)
        checker._add(
            "internal", "自检内部错误", "fail", f"{type(e).__name__}: {_short(str(e), 300)}", time.monotonic()
        )
    finally:
        if checker.rec is not None:
            with contextlib.suppress(Exception):
                await checker.rec.aclose()
    return _finish(report, checker, t_all)


def _finish(report: CheckReport, checker: _Checker, t_all: float) -> CheckReport:
    report.items = checker.items
    summary: dict[str, int] = {}
    for it in report.items:
        summary[it.status] = summary.get(it.status, 0) + 1
    report.summary = summary
    report.ok = summary.get("fail", 0) == 0
    report.duration_ms = int((time.monotonic() - t_all) * 1000)
    return report
