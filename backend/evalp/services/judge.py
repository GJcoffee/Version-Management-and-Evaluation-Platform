"""LLM 评审的基础设施：隐藏的评审 agent、评审版本、内置默认评审 prompt（详设 8.5）。

``sessions.version_id`` 不能为空，而每次评审调用都要记录为 ``Session(mode=judge)``，所以平台维护一个内部
隐藏 agent（:data:`JUDGE_AGENT`，agent 列表默认不显示），把评审配置（provider、model、评审 prompt、参数）
发布为它的配置型版本。相同配置的 ``spec_hash`` 相同，会自动复用同一个版本；之后用普通的
``SessionService.run_turn`` 执行评审，得到的 trace 可以像普通会话一样回看。

评审 prompt 是普通的 prompt 资产（Jinja2 模板），变量：``input``、``output``、``expected``、``rubric``、
``transcript``（工具调用摘要）以及 ``scale_min`` / ``scale_max``（评分范围）。首次启动时自动创建内置的
``prompt/judge-default``。
"""

from __future__ import annotations

import asyncio
from typing import Any

from jinja2 import TemplateSyntaxError
from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import NotFound
from evalp.logging import get_logger
from evalp.schemas.asset import AssetCreate
from evalp.services.agent import AgentService
from evalp.services.asset import AssetService
from evalp.services.asset_content import check_template
from evalp.services.draft import DraftService
from evalp.store.models import Agent
from evalp.utils.canonical import canonical_json, sha256_hex

log = get_logger(__name__)

JUDGE_AGENT = "evalp-judge"
HIDDEN_AGENTS = frozenset({JUDGE_AGENT})
DEFAULT_PROMPT_NAME = "judge-default"
DEFAULT_PROMPT_REF = f"asset://prompt/{DEFAULT_PROMPT_NAME}@latest"
JUDGE_VARIABLES = ("input", "output", "expected", "rubric", "transcript", "scale_min", "scale_max")
TEMPLATE_BUILTINS = frozenset({"variables", "knowledge", "now"})
JUDGE_USER_MESSAGE = "请按评审要求给出评分，只输出 JSON。"

DEFAULT_JUDGE_PROMPT = """\
你是一名严格、公正的评审专家，负责评估一个 AI 助手的回答质量。请只依据下面的材料独立打分，不要被回答的语气、
长度或格式左右。

## 用户输入
{{ input }}

## 助手的最终回答
{{ output }}
{% if expected %}
## 参考答案
（供对照：回答不必与之逐字一致，关键事实与结论一致即可）
{{ expected }}
{% endif %}{% if rubric %}
## 评分标准
{{ rubric }}
{% endif %}{% if transcript %}
## 工具调用摘要
{{ transcript }}
{% endif %}
## 评分要求
- 评分范围是整数 {{ scale_min }} 到 {{ scale_max }}：{{ scale_max }} 表示完全满足要求，{{ scale_min }}
  表示完全不满足。
- 依次检查：
  1. 是否回答了用户真正的问题，有无答非所问或遗漏关键步骤；
  2. 事实与关键信息是否正确，与参考答案 / 评分标准是否一致；
  3. 有无编造信息，或与工具返回的结果相矛盾；
  4. 表达是否清晰、语气是否得体。
- 有评分标准时以评分标准为准；没有时以参考答案为准；两者都没有时，按回答的正确性、完整性与相关性判断。
- 出现事实错误、编造信息或与工具结果矛盾时，必须明显扣分，不能因为文字流畅而给高分。

## 输出格式
只输出一个 JSON 对象，不要输出任何其他文字，也不要使用 Markdown 代码块：
{"score": <整数>, "reason": "<不超过 100 字的评分理由，说明得分依据或主要扣分点>"}
"""


def validate_judge_template(text: str) -> list[str]:
    """校验评审 prompt 模板：语法正确，且只使用允许的变量。返回问题列表（空表示通过）。"""
    try:
        used = check_template(text)
    except TemplateSyntaxError as e:
        return [f"模板语法错误（第 {e.lineno} 行）: {e.message}"]
    unknown = [v for v in used if v not in JUDGE_VARIABLES and v not in TEMPLATE_BUILTINS]
    if unknown:
        return [f"评审 prompt 使用了未知变量: {', '.join(unknown)}（可用: {', '.join(JUDGE_VARIABLES)}）"]
    return []


class JudgeService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = asyncio.Lock()
        self._versions: dict[str, str] = {}

    async def ensure_default_prompt(self) -> None:
        """首次启动时创建内置的 ``prompt/judge-default``；资产已存在（含被软删除）时不改动。幂等。"""
        assets = self.ctx.svc(AssetService)
        if await assets.find("prompt", DEFAULT_PROMPT_NAME, include_deleted=True) is not None:
            return
        await assets.create(
            AssetCreate(
                kind="prompt",
                name=DEFAULT_PROMPT_NAME,
                description="平台内置的默认 LLM 评审 prompt（输出 {score, reason} 的 JSON）",
                content=DEFAULT_JUDGE_PROMPT,
                content_type="text/markdown",
                message="内置默认评审 prompt",
            )
        )
        log.info("judge.default_prompt_created")

    async def prompt_text(self, ref: str) -> tuple[str, str]:
        """解析评审 prompt 引用，返回 ``(固化后的引用 asset://prompt/x@rev, 模板文本)``。"""
        assets = self.ctx.svc(AssetService)
        resolved = await assets.resolve_ref(ref)
        if resolved.kind != "prompt":
            raise NotFound(f"评审 prompt 必须是 prompt 资产: {ref}")
        text = (await self.ctx.blobs.get_bytes(resolved.hash)).decode("utf-8")
        return f"asset://prompt/{resolved.name}@{resolved.rev}", text

    async def version_for(
        self, *, provider: str, model: str, prompt_ref: str, params: dict[str, Any] | None = None
    ) -> str:
        """取得（必要时发布）评审配置对应的隐藏 agent 版本，返回版本 id。

        ``prompt_ref`` 应当是固化后的引用（``asset://prompt/x@3``）。配置相同则 spec_hash 相同，复用同一版本。
        provider / 模型 / 资产不存在时抛 SpecInvalid。
        """
        merged = {"temperature": 0, "max_tokens": 1024, **(params or {})}
        merged["response_format"] = {"type": "json_object"}
        spec: dict[str, Any] = {
            "schema": "evalp/v1",
            "kind": "config",
            "model": {"provider": provider, "name": model, "params": merged},
            "prompts": {"system": prompt_ref, "variables": dict.fromkeys(JUDGE_VARIABLES, "")},
            "runtime": {"max_steps": 1, "turn_timeout_s": 180},
        }
        key = sha256_hex(canonical_json(spec))
        cached = self._versions.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            if key in self._versions:
                return self._versions[key]
            draft = await self._judge_draft()
            drafts = self.ctx.svc(DraftService)
            saved = await drafts.save(draft.id, spec, draft.revision)
            out = await drafts.publish(saved.id, saved.revision, notes="LLM 评审配置", via="api")
            self._versions[key] = out.version.id
            return out.version.id

    async def _judge_draft(self):
        """取得隐藏 agent 的 main 草稿（不存在时创建 agent 与草稿）。"""
        drafts = self.ctx.svc(DraftService)
        async with self.ctx.db.write() as s:
            agent = (await s.execute(select(Agent).where(Agent.name == JUDGE_AGENT))).scalar_one_or_none()
            if agent is None:
                agent = await AgentService.insert_agent(
                    s, JUDGE_AGENT, "config", "平台内部使用：LLM 评审会话的承载 agent（不要删除或修改）"
                )
                await AgentService.insert_draft(s, agent, "main", {"kind": "config"})
        return next(d for d in await drafts.list(agent.id) if d.name == "main")
