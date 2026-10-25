"""Adapter 自检接口（详设 6.3）：对一个版本跑协议合规检查。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from evalp.api.deps import get_ctx
from evalp.context import AppContext
from evalp.services.adapter_check import CheckReport, run_adapter_check
from evalp.services.version import VersionService

router = APIRouter(tags=["adapter-check"])


@router.post("/versions/{version_ref}/adapter-check", response_model=CheckReport)
async def adapter_check(version_ref: str, ctx: AppContext = Depends(get_ctx)):
    """对版本跑自检：健康检查、单轮、多轮（历史）、超时、取消、事件合规。

    自检不写库、不产生会话，但会真实调用 agent（配置型会真实调用 LLM，产生少量费用）。
    agent 的任何失败都体现在报告的检查项里（``items[].status``），HTTP 状态码始终为 200；
    版本不存在时返回 404。``version_ref`` 可以是版本 id、``agent:v12``、``agent:标签`` 或 ``agent:latest``。
    """
    version_id = await ctx.svc(VersionService).resolve_ref(version_ref)
    return await run_adapter_check(ctx, version_id)
