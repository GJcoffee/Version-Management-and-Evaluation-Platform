"""Spec 接口：/api/v1/spec/schema。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from evalp.spec.models import spec_json_schema

router = APIRouter(prefix="/spec", tags=["spec"])


@router.get("/schema")
async def get_spec_schema() -> dict[str, Any]:
    """AgentSpec 的 JSON Schema（供前端生成表单）。"""
    return spec_json_schema()
