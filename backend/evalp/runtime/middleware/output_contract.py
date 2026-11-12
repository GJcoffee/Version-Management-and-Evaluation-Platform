"""轮次结束后的输出契约：读取最终 JSON，并按阶段校验业务 Envelope。"""

from __future__ import annotations

import hashlib
import json

from evalp.config import ArtifactsConfig
from evalp.runtime.messages import Message
from evalp.runtime.middleware.json_artifact import candidates, fetch_artifact
from evalp.schemas.middleware import JsonArtifactConfig, OutputContractConfig, OutputStageRule


def _fail(message: str) -> None:
    raise ValueError(message)


async def extract_envelope(
    text: str, limits: ArtifactsConfig
) -> tuple[dict, str, int]:
    """优先解析直接 JSON，否则读取回复中的唯一 JSON 链接。返回 (Envelope, sha256, size)。"""
    stripped = text.strip()
    raw = stripped.encode("utf-8")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        items = candidates("output_contract", Message(role="assistant", content=text), JsonArtifactConfig())
        if not items:
            _fail("最终输出不是有效的 JSON，也未找到 JSON 产物链接")
        if len(items) != 1:
            _fail("最终输出有多个 JSON 产物链接，无法确定阶段产物")
        raw = await fetch_artifact(items[0].url, limits)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            _fail("最终输出链接的内容不是有效的 JSON")
    if not isinstance(envelope, dict):
        _fail("最终输出必须是 JSON 对象")
    return envelope, hashlib.sha256(raw).hexdigest(), len(raw)


def _option_codes(questions: list) -> list:
    return [
        option.get("optionCode")
        for question in questions
        if isinstance(question, dict)
        for option in (question.get("optionList") or [])
        if isinstance(option, dict)
    ]


def validate_envelope(
    envelope: dict, rule: OutputStageRule, config: OutputContractConfig
) -> None:
    """严格校验 Envelope 键集合、阶段类型、确认题与阶段产物。"""
    required = set(config.required_keys)
    if rule.output_type == "examine":
        required.add(config.examine_key)
    actual = set(envelope)
    missing = sorted(required - actual)
    extra = sorted(actual - required)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"缺少 {', '.join(missing)}")
        if extra:
            parts.append(f"多余 {', '.join(extra)}")
        _fail(f"Envelope 键不符合契约（{'；'.join(parts)}）")

    if envelope.get("outputType") != rule.output_type:
        _fail(f"本轮阶段应为 {rule.output_type}，实际为 {envelope.get('outputType')}")
    agent = envelope.get("agentInfo")
    if not isinstance(agent, dict):
        _fail("agentInfo 必须是对象")
    if config.agent_id is not None and agent.get("agentId") != config.agent_id:
        _fail(f"agentInfo.agentId 应为 {config.agent_id}")

    confirm = envelope.get("confirmInfo")
    if not isinstance(confirm, dict) or not isinstance(confirm.get("questionInfoList"), list):
        _fail("confirmInfo.questionInfoList 必须是数组")
    questions = confirm["questionInfoList"]
    artifact = envelope.get("artifactInfo")

    if rule.terminal:
        if questions or artifact is not None or envelope.get(config.examine_key) is not None:
            _fail("结束态仍带确认题、产物或审核")
        return

    if not questions:
        _fail("当前阶段缺少真实确认题")
    if rule.approval_code is not None and rule.approval_code not in _option_codes(questions):
        _fail(f"当前阶段缺少批准选项 {rule.approval_code}")

    if rule.output_type in ("confirm_artifact", "examine"):
        allowed = (
            rule.artifact_types if rule.artifact_types is not None
            else ([rule.artifact_type] if rule.artifact_type is not None else [])
        )
        if (
            not isinstance(artifact, dict)
            or type(artifact.get("artifactType")) is not int
            or artifact["artifactType"] not in allowed
            or not artifact.get("artifactId")
            or not isinstance(artifact.get("cardInfoList"), list)
            or not artifact["cardInfoList"]
        ):
            _fail(
                "artifactInfo 不合法：需要允许的 artifactType、非空 artifactId 与非空 cardInfoList"
            )
    elif artifact is not None:
        _fail("当前阶段 artifactInfo 必须为 null")

    if rule.output_type == "examine":
        review = envelope.get(config.examine_key)
        if (
            not isinstance(review, dict)
            or type(review.get("examineStatus")) is not int
            or review["examineStatus"] not in rule.examine_status
            or not isinstance(review.get("examineInfoList"), list)
            or not review["examineInfoList"]
            or not all(isinstance(item, dict) for item in review["examineInfoList"])
        ):
            _fail("examineInfo 不合法：审核状态或审核列表不符合契约")
