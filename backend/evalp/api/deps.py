"""路由依赖。"""

from __future__ import annotations

from fastapi import Request

from evalp.context import AppContext


def get_ctx(request: Request) -> AppContext:
    """取出应用上下文：``ctx: AppContext = Depends(get_ctx)``。"""
    return request.app.state.ctx
