"""输出契约中间件：Envelope 提取与各阶段严格校验。"""

import hashlib
import json

import httpx
import pytest
import respx
from evalp.runtime.middleware.output_contract import extract_envelope, validate_envelope
from evalp.schemas.middleware import OutputContractConfig, OutputStageRule


def rule(**over) -> OutputStageRule:
    return OutputStageRule(**{"output_type": "confirm", **over})


def contract_config(*rules: OutputStageRule, **over) -> OutputContractConfig:
    return OutputContractConfig(
        agent_id="creative-director",
        stages={str(i): item for i, item in enumerate(rules, start=1)},
        **over,
    )


def envelope(output_type="confirm", **over) -> dict:
    value = {
        "summary": "阶段说明",
        "outputType": output_type,
        "agentInfo": {"agentId": "creative-director"},
        "confirmInfo": {"questionInfoList": [{"optionList": [{"optionCode": "approve"}]}]},
        "artifactInfo": None,
        "agentInvocationChain": [],
    }
    value.update(over)
    return value


def artifact(artifact_type: int) -> dict:
    return {"artifactType": artifact_type, "artifactId": "art-1", "cardInfoList": [{"url": "x"}]}


def review(status=2, items=None) -> dict:
    return {"examineStatus": status, "examineInfoList": items if items is not None else [{"note": "ok"}]}


def test_valid_confirm_stage():
    item = rule(approval_code="approve")
    validate_envelope(envelope(), item, contract_config(item))


def test_valid_confirm_artifact_stage():
    env = envelope("confirm_artifact", artifactInfo=artifact(1))
    item = rule(output_type="confirm_artifact", artifact_types=[1, 2, 4], approval_code="approve")
    validate_envelope(env, item, contract_config(item))


def test_valid_examine_stage_requires_examine_key():
    env = envelope("examine", artifactInfo=artifact(5), examineInfo=review())
    item = rule(output_type="examine", artifact_type=5, examine_status=[2, 3])
    validate_envelope(env, item, contract_config(item))


def test_valid_finish_terminal_stage():
    env = envelope("finish", confirmInfo={"questionInfoList": []})
    item = rule(output_type="finish", terminal=True)
    validate_envelope(env, item, contract_config(item))


@pytest.mark.parametrize("mutate,message", [
    (lambda e: e.pop("summary"), "缺少 summary"),
    (lambda e: e.update(extra=1), "多余 extra"),
    (lambda e: e.update(outputType="finish"), "本轮阶段应为 confirm"),
    (lambda e: e.update(agentInfo={"agentId": "other"}), "agentInfo.agentId"),
    (lambda e: e.update(confirmInfo={"questionInfoList": []}), "缺少真实确认题"),
    (lambda e: e["confirmInfo"]["questionInfoList"][0]["optionList"].clear(), "批准选项"),
    (lambda e: e.update(artifactInfo=artifact(1)), "artifactInfo 必须为 null"),
])
def test_invalid_confirm_variants(mutate, message):
    env = envelope()
    mutate(env)
    with pytest.raises(ValueError, match=message):
        validate_envelope(env, rule(approval_code="approve"), contract_config(rule(approval_code="approve")))


def test_terminal_still_carries_artifact_or_questions():
    item = rule(output_type="finish", terminal=True)
    for env in (
        envelope("finish", confirmInfo={"questionInfoList": [{"optionList": []}]}),
        envelope("finish", artifactInfo=artifact(1), confirmInfo={"questionInfoList": []}),
    ):
        with pytest.raises(ValueError, match="结束态"):
            validate_envelope(env, item, contract_config(item))


def test_examine_invalid_status_and_list():
    item = rule(output_type="examine", artifact_type=5)
    for env in (
        envelope("examine", artifactInfo=artifact(5), examineInfo=review(status=1)),
        envelope("examine", artifactInfo=artifact(5), examineInfo=review(items=[])),
        envelope("examine", artifactInfo=artifact(5), examineInfo=review(items=["x"])),
        envelope("examine", artifactInfo=artifact(5)),
    ):
        with pytest.raises(ValueError):
            validate_envelope(env, item, contract_config(item))


def test_examine_requires_exactly_one_extra_key():
    item = rule(output_type="examine", artifact_type=5)
    env = envelope("examine", artifactInfo=artifact(5), examineInfo=review(), extra=1)
    with pytest.raises(ValueError, match="多余 extra"):
        validate_envelope(env, item, contract_config(item))


def test_missing_stage_is_not_silently_skipped():
    cfg = contract_config(rule(approval_code="approve"))
    assert cfg.stages.get("2") is None


async def test_extract_direct_json_and_url(config):
    raw = json.dumps(envelope(), ensure_ascii=False).encode()
    direct, digest, size = await extract_envelope(f"  {raw.decode()}  ", config.artifacts)
    assert direct["outputType"] == "confirm"
    assert (digest, size) == (hashlib.sha256(raw).hexdigest(), len(raw))

    config.artifacts.allowed_hosts = "artifacts.example"
    with respx.mock() as router:
        route = router.get("https://artifacts.example/out.json").mock(
            return_value=httpx.Response(200, content=raw)
        )
        fetched, url_digest, url_size = await extract_envelope(
            "https://artifacts.example/out.json", config.artifacts
        )
    assert route.called and fetched == direct
    assert (url_digest, url_size) == (digest, size)


async def test_extract_rejects_invalid_json_and_non_object(config):
    for text in ("not-json", "[1, 2]"):
        with pytest.raises(ValueError):
            await extract_envelope(text, config.artifacts)


@pytest.mark.parametrize("reply", [
    "I'll start by reading the storyboard-video skill to follow its current stage contract.{url}",
    "已生成：[下载 JSON]({url})。",
    "{url}\n[同一产物]({url})",
])
async def test_extract_json_link_with_commentary_and_markdown(config, reply):
    url = "https://artifacts.example/out.json?Expires=1794457834&Signature=%2BX7J%3D"
    raw = json.dumps(envelope(), ensure_ascii=False).encode()
    config.artifacts.allowed_hosts = "artifacts.example"
    with respx.mock() as router:
        route = router.get(url).mock(return_value=httpx.Response(200, content=raw))
        parsed, digest, size = await extract_envelope(reply.format(url=url), config.artifacts)
    assert route.call_count == 1 and parsed == envelope()
    assert (digest, size) == (hashlib.sha256(raw).hexdigest(), len(raw))


async def test_extract_rejects_multiple_json_links_before_fetch(config):
    with respx.mock() as router:
        with pytest.raises(ValueError, match="多个 JSON"):
            await extract_envelope(
                "https://artifacts.example/a.json\nhttps://artifacts.example/b.json",
                config.artifacts,
            )
    assert not router.calls


async def test_extract_rejects_invalid_link_content(config):
    config.artifacts.allowed_hosts = "artifacts.example"
    with respx.mock() as router:
        router.get("https://artifacts.example/out.json").mock(
            return_value=httpx.Response(200, content=b"not-json")
        )
        with pytest.raises(ValueError, match="链接的内容不是有效的 JSON"):
            await extract_envelope("已生成：https://artifacts.example/out.json", config.artifacts)


async def test_extract_direct_envelope_does_not_follow_embedded_json_url(config):
    env = envelope(summary="https://artifacts.example/reference.json")
    with respx.mock() as router:
        parsed, _, _ = await extract_envelope(json.dumps(env), config.artifacts)
    assert parsed == env and not router.calls
