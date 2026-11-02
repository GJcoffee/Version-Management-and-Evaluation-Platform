"""轮间中间件来源固定、执行 Trace 与产物持久化。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import Conflict, ValidationFailed
from evalp.runtime.adapters.base import RunContext
from evalp.runtime.events import ev
from evalp.runtime.messages import Message, TurnOutput
from evalp.runtime.middleware.json_artifact import (
    artifact_error,
    candidates,
    compose_input,
    decode_artifact,
    fetch_artifact,
    select_candidate,
)
from evalp.runtime.middleware.registry import validate_selection
from evalp.runtime.runner import EmitFn, error_from_exception
from evalp.schemas.middleware import ArtifactCandidates, JsonArtifactConfig, MiddlewareSelection
from evalp.store.models import Session, Turn


class MiddlewareService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def source(self, session_id: str, source_id: str) -> Turn:
        """只允许读取当前会话的成功最终回复。"""
        async with self.ctx.db.read() as s:
            source = await s.get(Turn, source_id)
            if (
                source is None or source.session_id != session_id or source.status != "ok"
                or not source.output or not source.output.get("final")
            ):
                raise artifact_error("ARTIFACT_SOURCE_INVALID", "来源须为当前会话已成功完成的轮次")
            return source

    async def artifact_candidates(
        self, session_id: str, source_id: str, url_pointer: str | None = None,
    ) -> ArtifactCandidates:
        """返回本地候选，不执行下载。"""
        config = JsonArtifactConfig(url_pointer=url_pointer)
        source = await self.source(session_id, source_id)
        final = TurnOutput.model_validate(source.output).final
        assert final is not None
        return ArtifactCandidates(source_turn_id=source_id, candidates=candidates(source_id, final, config))

    async def freeze(
        self, session: Session, original: Message, selections: list[MiddlewareSelection],
        *, adapter_snapshot: dict | None = None,
    ) -> dict | None:
        """在会话执行槽内校验配置，并固定依赖来源。"""
        if not selections:
            return None
        if len(selections) > 1:
            raise ValidationFailed("首期每轮最多选择一个中间件")
        if session.mode != "chat":
            raise ValidationFailed("轮间中间件当前仅支持普通对话会话")
        binding = (adapter_snapshot or session.adapter_snapshot or {}).get("binding") or {}
        if (binding.get("config") or {}).get("business_bridge"):
            raise ValidationFailed("该业务桥接尚不支持平台组合输入，暂不可使用轮间中间件")
        if original.content is None or (
            isinstance(original.content, list) and any(
                b.get("type") != "text" or not isinstance(b.get("text"), str) for b in original.content
            )
        ):
            raise ValidationFailed("轮间中间件仅支持文本输入")
        selection = selections[0]
        validate_selection(selection)
        async with self.ctx.db.read() as s:
            last = (await s.execute(
                select(Turn).where(Turn.session_id == session.id).order_by(Turn.idx.desc()).limit(1)
            )).scalar_one_or_none()
        if selection.expected_previous_turn_id and (
            last is None or last.id != selection.expected_previous_turn_id
        ):
            raise Conflict("上一轮已变化，请刷新产物来源", code="ARTIFACT_SOURCE_CHANGED")
        source_id = selection.source_turn_id or (last.id if last else "")
        source = await self.source(session.id, source_id)
        step = selection.model_dump(mode="json")
        step.update(source_turn_id=source.id, status="pending", duration_ms=0)
        return {"schema_version": 1, "steps": [step]}

    async def prepare(
        self, ctx: RunContext, original: Message, snapshot: dict, emit: EmitFn,
    ) -> Message:
        """保存完整输入和独立 Blob 引用成功后才返回给 Runner。"""
        step = snapshot["steps"][0]
        config = JsonArtifactConfig.model_validate(step["config"])
        root = f"middleware:{uuid4().hex}"
        saved = False
        started = time.monotonic()
        step["status"] = "running"
        await self.save_snapshot(ctx.turn_id, snapshot)
        await emit(ev("span.start", step_id=root, name="读取上一轮 JSON", attrs={
            "category": "middleware", "id": step["id"], "version": step["version"],
            "source_turn_id": step["source_turn_id"],
        }))

        async def phase(name: str, action: Callable[[], Any]) -> Any:
            child = f"middleware:{uuid4().hex}"
            t0 = time.monotonic()
            await emit(ev("span.start", step_id=child, parent_step_id=root, name=name,
                          attrs={"category": "middleware"}))
            try:
                value = action()
                if asyncio.iscoroutine(value):
                    value = await value
            except BaseException:
                await emit(ev("span.end", step_id=child, parent_step_id=root, name=name, status="error",
                              latency_ms=int((time.monotonic() - t0) * 1000)))
                raise
            await emit(ev("span.end", step_id=child, parent_step_id=root, name=name, status="ok",
                          latency_ms=int((time.monotonic() - t0) * 1000)))
            return value

        try:
            source = await self.source(ctx.session_id, step["source_turn_id"])
            final = TurnOutput.model_validate(source.output).final
            assert final is not None
            candidate = await phase(
                "提取 JSON 链接",
                lambda: select_candidate(candidates(source.id, final, config), config),
            )
            raw = await phase("读取 JSON", lambda: fetch_artifact(candidate.url, self.ctx.config.artifacts))
            result = await phase("解析与选择字段", lambda: decode_artifact(raw, candidate.url, config))
            effective = compose_input(original, result, self.ctx.config.artifacts)
            step.update(status="ok", duration_ms=int((time.monotonic() - started) * 1000),
                        source_url=result.url, raw_sha256=result.raw_sha256)
            # 同一事务登记 Blob 引用与输入；事务失败不会调用 Adapter。
            async with self.ctx.db.write() as s:
                turn = await s.get(Turn, ctx.turn_id)
                if turn is None:
                    raise artifact_error("ARTIFACT_SOURCE_INVALID", "执行轮次已不存在")
                blob = await self.ctx.blobs.put_bytes(
                    json.dumps(asdict(result), ensure_ascii=False, allow_nan=False).encode(), session=s,
                )
                await self.ctx.blobs.incref(blob, session=s)
                step["result_blob_hash"] = blob
                step["duration_ms"] = int((time.monotonic() - started) * 1000)
                turn.effective_input = effective.model_dump(mode="json", exclude_none=True)
                turn.middleware_snapshot = snapshot
            saved = True
            await emit(ev("span.end", step_id=root, name="读取上一轮 JSON",
                          status="ok", latency_ms=step["duration_ms"],
                          output={"effective_input": effective, "result": {"$blob": blob},
                                  "source_turn_id": source.id}))
            return effective
        except BaseException as exc:
            cancelled = isinstance(exc, asyncio.CancelledError)
            status = "error"
            if cancelled:
                status = "timeout" if time.monotonic() >= ctx.deadline else "cancelled"
            if not saved:
                step.pop("result_blob_hash", None)
            step.update(status=status, duration_ms=int((time.monotonic() - started) * 1000),
                        error=None if cancelled else error_from_exception(exc))
            # _finalize 会在取消/超时预算以外保存该快照并补齐终态。
            await emit(ev("span.end", step_id=root, name="读取上一轮 JSON",
                          status=status, latency_ms=step["duration_ms"],
                          output={"error": step.get("error")}))
            raise

    async def save_snapshot(self, turn_id: str, snapshot: dict) -> None:
        async with self.ctx.db.write() as s:
            turn = await s.get(Turn, turn_id)
            if turn is not None:
                turn.middleware_snapshot = snapshot
