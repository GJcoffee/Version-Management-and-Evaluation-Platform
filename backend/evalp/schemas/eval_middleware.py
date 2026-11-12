"""可复用的评测逐轮中间件计划；轮号从 1 开始，来源由本次会话固定。"""

from typing import Any

from pydantic import TypeAdapter, ValidationError

from evalp.errors import ValidationFailed
from evalp.runtime.middleware.registry import phase_of, validate_selection
from evalp.schemas.middleware import JsonArtifactConfig, MiddlewareSelection, OutputContractConfig

TurnMiddlewares = dict[str, list[MiddlewareSelection]]
_PLAN = TypeAdapter(TurnMiddlewares)


def validate_plan(value: Any, turn_count: int | None = None) -> TurnMiddlewares:
    """创建 / 导入时完整校验，禁止绑定其他尝试的轮次或候选 id。"""
    try:
        plan = _PLAN.validate_python(value)
    except ValidationError as exc:
        raise ValidationFailed("逐轮中间件须为轮号到配置数组的对象", {
            "errors": [{"path": ".".join(map(str, e["loc"])), "message": e["msg"]}
                       for e in exc.errors()],
        }) from None
    for stage, steps in plan.items():
        if not stage.isascii() or not stage.isdigit() or str(int(stage)) != stage or int(stage) < 1:
            raise ValidationFailed("逐轮中间件轮号须为从 1 开始的整数，例如 \"2\"")
        if turn_count is not None and int(stage) > turn_count:
            raise ValidationFailed(f"第 {stage} 轮中间件超出用例轮数（{turn_count}）")
        phases = set()
        for step in steps:
            validate_selection(step)
            phase = phase_of(step)
            if phase in phases:
                raise ValidationFailed(f"第 {stage} 轮最多选择一个输入中间件和一个输出中间件")
            phases.add(phase)
            if step.source_turn_id or step.expected_previous_turn_id:
                raise ValidationFailed("评测中间件来源由本次会话的上一轮固定，不能指定轮次 id")
            if isinstance(step.config, JsonArtifactConfig) and step.config.selected_candidate_id:
                raise ValidationFailed("评测不能复用会话候选 id；多个 JSON 链接请配置 url_pointer")
            if phase == "input" and stage == "1":
                raise ValidationFailed("第 1 轮没有上一轮 JSON，不能配置输入中间件")
            if isinstance(step.config, OutputContractConfig) and stage not in step.config.stages:
                raise ValidationFailed(f"第 {stage} 轮未配置对应的输出契约")
    return plan


def dump_plan(plan: TurnMiddlewares) -> dict[str, list[dict[str, Any]]]:
    return {stage: [s.model_dump(mode="json") for s in steps] for stage, steps in plan.items()}


def effective_plan(run_plan: Any, case_input: dict[str, Any]) -> TurnMiddlewares:
    """用例按轮覆盖 Run 配置；显式 [] 可关闭某轮的中间件。"""
    plan = {**(run_plan or {}), **case_input.get("turn_middlewares", {})}
    return validate_plan(plan, len(case_input.get("turns", [])))
