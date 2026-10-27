"""agent 发布 / 推送规范（conventions）的服务端校验。

规范存在 agents 表的 ``conventions`` 列，由服务端对所有入口（CLI、MCP、网页、HTTP API）一致地强制，
所以放在发布 / 推送 / 资产写入的服务里调用，而不是各个客户端自己检查。违反时抛 ``ConventionViolation``
（code ``CONVENTION_VIOLATION``）：``message`` 用中文写明违反了哪条、怎么改，``details`` 带结构化信息::

    {"agent": "…", "action": "publish", "violations": [{"convention": "tag_from_param", "message": "…", …}]}
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evalp.errors import ConventionViolation
from evalp.schemas.agent import Conventions, check_tag
from evalp.spec.models import CodeSpec, ConfigSpec
from evalp.store.models import Agent, Version, VersionTag

ACTION_TEXT = {"publish": "发布", "push": "推送"}


def conventions_of(agent: Agent) -> Conventions:
    """agent 记录上的规范（缺省字段取"没有约束"的默认值）。"""
    return Conventions.model_validate(agent.conventions)


def expected_tag(conv: Conventions, spec: ConfigSpec | CodeSpec) -> str | None:
    """``tag_from_param`` 开启时规范要求的标签 ``v{params.<参数>}``；参数没有值时返回 None。"""
    if not conv.tag_from_param:
        return None
    value = spec.params.get(conv.tag_from_param) if isinstance(spec, CodeSpec) else None
    return None if value in (None, "") else f"v{value}"


def _problem(convention: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"convention": convention, "message": message, **extra}


def _violation(
    agent: Agent, action: str, problems: list[dict[str, Any]], *, status: int = 400
) -> ConventionViolation:
    body = (
        problems[0]["message"]
        if len(problems) == 1
        else "\n".join(f"{i}. {p['message']}" for i, p in enumerate(problems, 1))
    )
    return ConventionViolation(
        f"agent「{agent.name}」的发布规范不允许这次{ACTION_TEXT.get(action, action)}：\n{body}",
        {"agent": agent.name, "action": action, "violations": problems},
        status=status,
    )


def _tag_problems(conv: Conventions, spec: ConfigSpec | CodeSpec, tag: str | None) -> list[dict[str, Any]]:
    """``tag_from_param``：发布必须带标签，且等于 ``v{params.<参数>}``。"""
    param = conv.tag_from_param
    want = expected_tag(conv, spec)
    head = f"规范 tag_from_param 要求标签等于 v{{params.{param}}}"
    if want is None:
        return [
            _problem(
                "tag_from_param",
                f"{head}，但草稿的 params 里没有 {param}：请在 agent.yaml 的 params 中设置 "
                f'{param}（如 {param}: "0.5.3"），重新推送后再发布。',
                param=param,
            )
        ]
    try:
        check_tag(want)
    except ValueError as e:
        return [
            _problem(
                "tag_from_param",
                f"{head}，但 {want} 不是合法的标签（{e}）："
                f"请把 params.{param} 改成带点的版本号（如 0.5.3）。",
                param=param,
                expected_tag=want,
            )
        ]
    if not tag:
        return [
            _problem(
                "tag_from_param",
                f"{head} = {want}，发布时必须带这个标签：CLI 用 evalp publish --tag {want}，"
                f"MCP 用 publish_version 的 tag 参数，HTTP 在发布请求里带 tag。",
                param=param,
                expected_tag=want,
            )
        ]
    if tag != want:
        return [
            _problem(
                "tag_from_param",
                f"标签 {tag} 不符合规范 tag_from_param：{head} = {want}。请改用 {want}；"
                f"如果版本号不对，先更新 agent.yaml 的 params.{param} 并重新推送。",
                param=param,
                expected_tag=want,
                tag=tag,
            )
        ]
    return []


def _map_problems(conv: Conventions, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """``require_source_id``：快照 map 里指定类型的规则必须配置 id。"""
    if not conv.require_source_id:
        return []
    snap = spec.get("snapshot")
    rules = snap.get("map") if isinstance(snap, dict) else None
    kinds = ", ".join(conv.require_source_id)
    problems = []
    for i, rule in enumerate(rules or []):
        if isinstance(rule, dict) and rule.get("kind") in conv.require_source_id and not rule.get("id"):
            path = f"snapshot.map[{i}]"
            problems.append(
                _problem(
                    "require_source_id",
                    f"规范 require_source_id 要求 {kinds} 类型的快照规则必须配置 id，{path}"
                    f"（glob: {rule.get('glob')}，kind: {rule['kind']}）缺少：请加上 id，取自导出 JSON 的"
                    '稳定字段，如 id: "$.id"（字段名按实际导出文件调整）。',
                    path=path,
                    kind=rule["kind"],
                    glob=rule.get("glob"),
                )
            )
    return problems


def check_push(agent: Agent, spec: dict[str, Any]) -> None:
    """工作区推送：快照 map 的规则必须满足 ``require_source_id``。"""
    problems = _map_problems(conventions_of(agent), spec)
    if problems:
        raise _violation(agent, "push", problems)


def check_publish(
    agent: Agent,
    spec: ConfigSpec | CodeSpec,
    draft_spec: dict[str, Any],
    notes: str | None,
    tag: str | None,
) -> None:
    """发布：说明必填、标签取自参数、快照 map 配了 id（对应 conventions 的三条）。"""
    conv = conventions_of(agent)
    problems: list[dict[str, Any]] = []
    if conv.require_notes and not (notes or "").strip():
        problems.append(
            _problem(
                "require_notes",
                "规范 require_notes 要求发布说明必填：请写明这次改了什么"
                '（CLI: evalp publish -m "…"；MCP: publish_version 的 notes 参数）。',
            )
        )
    if conv.tag_from_param:
        problems += _tag_problems(conv, spec, tag)
    problems += _map_problems(conv, draft_spec)
    if problems:
        raise _violation(agent, "publish", problems)


async def check_tag_unused(s: AsyncSession, agent: Agent, tag: str, version_id: str | None) -> None:
    """``tag_from_param``：标签已存在于该 agent 时拒绝（已经指向本次要返回的同一个版本除外）。"""
    row = await s.get(VersionTag, (agent.id, tag))
    if row is None or row.version_id == version_id:
        return
    holder = await s.get(Version, row.version_id)
    assert holder is not None
    param = conventions_of(agent).tag_from_param
    raise _violation(
        agent,
        "publish",
        [
            _problem(
                "tag_from_param",
                f"标签 {tag} 已存在于 agent「{agent.name}」的 v{holder.seq}，"
                "规范 tag_from_param 不允许重复使用，"
                f"说明这个版本号已经发布过。请先把 agent.yaml 的 params.{param} 改成新的版本号，"
                "再重新推送和发布。",
                tag=tag,
                existing_version=f"v{holder.seq}",
            )
        ],
        status=409,
    )
