"""评测命令 / 工具对评测后端（进程内真实实现）+ fake OpenAI 的联调。"""

from __future__ import annotations

import re

import pytest
from evalp.client import EvalpClient
from evalp.mcp.server import build_server
from mcp import Client

from tests.integration.p2a.conftest import API, Api

DATASET = (
    '{"case_key": "refund", "input": "我要退货", "expected": "退货", "tags": ["p0"]}\n'
    '{"case_key": "hours", "input": "几点上班", "expected": "9:00"}\n'
)
EVALUATORS = [{"key": "kw", "type": "contains"}]


@pytest.fixture
async def versions(client, fake_openai):
    api = Api(client)
    out = await api.agent(
        "support",
        spec={
            "kind": "config",
            "model": {"provider": "fake", "name": "fake-model"},
            "prompts": {"system": "你是客服"},
        },
    )  # noqa: E501
    v1 = (await api.publish(out["draft"]))["version"]
    draft = await api.save(out["draft"], {**out["draft"]["spec"], "prompts": {"system": "你是资深客服"}})
    await api.publish(draft["draft"])
    r = await client.post(
        f"{API}/datasets/import", json={"name": "reg", "format": "jsonl", "content": DATASET}
    )
    assert r.status_code == 201, r.text
    return v1


async def test_run_eval_and_report_tools_against_real_backend(client, versions, fake_openai, project_dir):
    # 第一版：回显输入（含"退货"），第二版：答非所问
    fake_openai.add_rule(match={"user": "几点上班"}, reply="每天 9:00 上班")
    fake_openai.add_rule(
        match={"user": "我要退货"},
        reply=lambda req: "好的，可以退货" if "资深" not in str(req.messages) else "不清楚",
    )
    server = build_server(
        project_dir=project_dir, client_factory=lambda p: EvalpClient(http=client), poll_interval=0.05
    )
    async with Client(server) as mc:
        r = await mc.call_tool(
            "run_eval",
            {"dataset": "reg", "versions": ["v2"], "baseline": "v1", "evaluators": EVALUATORS, "repeats": 1},
        )
        out = r.content[0].text
        assert not r.is_error, out
        assert "评测已发起" in out and "通过率" in out and "评测对比" in out
        assert "退化 1" in out and "refund" in out
        run_ids = re.findall(r"`(evr_\w+)`", out)
        right = run_ids[1]
        rep = (await mc.call_tool("get_eval_report", {"run_id": right})).content[0].text
        assert "未通过的用例" in rep and "refund" in rep and "不清楚" in rep
        cmp_ = (await mc.call_tool("get_eval_compare", {"left": run_ids[0], "right": right})).content[0].text
        assert "退化的用例" in cmp_ and "refund" in cmp_
