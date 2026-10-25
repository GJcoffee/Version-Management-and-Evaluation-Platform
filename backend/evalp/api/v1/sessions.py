"""会话接口（详设 7、11.3）：会话 / 轮次 / SSE 事件流 / 大 payload 的 blob 读取。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from evalp.api.deps import get_ctx
from evalp.api.pagination import Page, PageParams, page_params
from evalp.api.sse import format_sse, sse_response
from evalp.context import AppContext
from evalp.errors import NotFound, ValidationFailed
from evalp.schemas.session import (
    SessionCreate,
    SessionDetail,
    SessionOut,
    TurnAccepted,
    TurnCreate,
    TurnOut,
)
from evalp.services.session import Idle, SessionService
from evalp.services.version import VersionService

router = APIRouter(tags=["sessions"])

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
API_PREFIX = "/api/v1"


class SessionWrapper(BaseModel):
    session: SessionOut


@router.post("/sessions", response_model=SessionWrapper, status_code=201)
async def create_session(body: SessionCreate, ctx: AppContext = Depends(get_ctx)):
    """创建对话会话。版本用 ``version_id``，或 ``agent`` + ``tag``（标签、``v12`` 或 ``latest``）指定。"""
    ref = body.version_id or f"{body.agent}:{body.tag or 'latest'}"
    version_id = await ctx.svc(VersionService).resolve_ref(ref)
    session = await ctx.svc(SessionService).create(
        version_id=version_id, mode="chat", title=body.title, variables=body.variables
    )
    return SessionWrapper(session=session)


@router.get("/sessions", response_model=Page[SessionOut])
async def list_sessions(
    agent_id: str | None = Query(None, description="agent 的 id 或名称"),
    version_id: str | None = None,
    mode: str | None = Query(
        None, description="chat | replay | eval | judge，可用逗号分隔多个；缺省排除 eval 和 judge"
    ),
    q: str | None = Query(None, description="标题关键字"),
    p: PageParams = Depends(page_params),
    ctx: AppContext = Depends(get_ctx),
):
    """会话列表（新的在前）。"""
    modes = [m.strip() for m in mode.split(",") if m.strip()] if mode else None
    return await ctx.svc(SessionService).list_sessions(
        agent_id=agent_id, version_id=version_id, mode=modes, q=q, limit=p.limit, cursor=p.cursor
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail, response_model_exclude_none=False)
async def get_session(
    session_id: str,
    include: str = Query("turns", description="逗号分隔：turns、events；传空字符串表示都不要"),
    ctx: AppContext = Depends(get_ctx),
):
    """会话详情，可带轮次与事件（大 payload 以 ``{"$blob": hash}`` 返回，用 ``GET /blobs/{hash}`` 读取）。"""
    parts = {x.strip() for x in include.split(",") if x.strip()}
    unknown = parts - {"turns", "events"}
    if unknown:
        raise ValidationFailed(
            f"include 只支持 turns、events: {', '.join(sorted(unknown))}",
            {"errors": [{"path": "include", "message": "不支持的取值"}]},
        )
    detail = await ctx.svc(SessionService).get(
        session_id, include_turns="turns" in parts, include_events="events" in parts
    )
    if "events" not in parts:
        detail.events = None
    return detail


@router.post("/sessions/{session_id}/turns", response_model=TurnAccepted, status_code=202)
async def create_turn(
    session_id: str,
    body: TurnCreate,
    response: Response,
    wait: bool | None = Query(None, description="覆盖请求体里的 wait"),
    ctx: AppContext = Depends(get_ctx),
):
    """执行一轮。默认 202 立即返回（用 ``stream_url`` 订阅过程）；``wait=true`` 时阻塞到结束并返回 200。

    会话正在执行时返回 409 ``SESSION_BUSY``。执行失败不是 HTTP 错误：看 ``turn.status`` / ``turn.error``。
    """
    svc = ctx.svc(SessionService)
    do_wait = body.wait if wait is None else wait
    after_seq = (await svc.get_session(session_id)).last_seq
    turn = await svc.run_turn(
        session_id,
        body.input,
        variables=body.variables,
        tool_mocks=body.tool_mocks,
        pair_index=body.pair_index,
        wait=do_wait,
    )
    response.status_code = 200 if do_wait else 202
    return TurnAccepted(
        turn_id=turn.id,
        stream_url=f"{API_PREFIX}/sessions/{session_id}/stream?after_seq={after_seq}",
        turn=turn,
    )


@router.post("/turns/{turn_id}/cancel", response_model=TurnOut)
async def cancel_turn(turn_id: str, ctx: AppContext = Depends(get_ctx)):
    """取消正在执行的轮次，等它收尾后返回轮次（状态为 cancelled）。轮次已结束时原样返回，不报错。"""
    svc = ctx.svc(SessionService)
    await svc.cancel_turn(turn_id)
    return await svc.get_turn(turn_id)


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0, description="只推送 seq 大于该值的事件"),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    ctx: AppContext = Depends(get_ctx),
):
    """SSE：先回放库里 ``after_seq`` 之后的事件，再订阅实时事件。

    帧：``id: {seq}``（llm.delta 没有）/ ``event: {type}`` / ``data: {事件 JSON}``；会话空闲
    （回放完没有轮次在运行，或正在运行的这一轮结束）后发送 ``event: idle`` 并关闭。
    请求头 ``Last-Event-ID`` 与 ``after_seq`` 同时给出时取较大者。
    """
    svc = ctx.svc(SessionService)
    await svc.get_session(session_id)  # 不存在时返回 404，而不是一个空的流
    start = after_seq
    if last_event_id and last_event_id.strip().isdigit():
        start = max(start, int(last_event_id.strip()))

    async def frames() -> AsyncIterator[str]:
        async for item in svc.stream(session_id, start):
            if isinstance(item, Idle):
                yield format_sse({"status": "idle", "last_seq": item.last_seq}, event="idle")
            else:
                yield format_sse(item.to_wire(), event=item.type, id=item.seq)

    return sse_response(frames())


@router.get("/blobs/{blob_hash}")
async def get_blob(
    blob_hash: str,
    session_id: str | None = Query(None, description="限定在该会话的事件里查找引用，更快"),
    ctx: AppContext = Depends(get_ctx),
):
    """读取被转存为 blob 的大 payload（事件里的 ``{"$blob": hash}``）；也可读取资产内容的 blob。

    只允许读取被事件引用的 blob 或资产修订的 blob，其他 blob 一律 404。
    """
    if not _HASH_RE.match(blob_hash):
        raise ValidationFailed(
            "blob 哈希必须是 64 位小写十六进制", {"errors": [{"path": "blob_hash", "message": "格式不对"}]}
        )
    kind = await ctx.svc(SessionService).blob_kind(blob_hash, session_id)
    if kind is None or not ctx.blobs.exists(blob_hash):
        raise NotFound(f"blob 不存在或不可访问: {blob_hash}")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=31536000, immutable"}
    if kind == "event":
        return FileResponse(ctx.blobs.path(blob_hash), media_type="application/json", headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="{blob_hash[:16]}"'
    return FileResponse(ctx.blobs.path(blob_hash), media_type="application/octet-stream", headers=headers)
