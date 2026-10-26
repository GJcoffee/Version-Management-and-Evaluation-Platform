"""evalp init：在当前目录生成 .evalp.toml、agent.yaml、示例 prompt / 数据集 / 评估器，并更新 .gitignore。"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from evalp.cli.common import console, err_console
from evalp.workspace_client import CONFIG_NAME, STATE_DIR, normalize_stem


class Kind(StrEnum):
    config = "config"
    code = "code"


TOML = """\
# EvalP 工作区配置，建议提交到 git
[server]
url = "http://127.0.0.1:7860"

[agent]
name = "{name}"         # 首次 push 时自动创建
spec = "agent.yaml"
draft = "main"

# 本地文件 -> 资产（kind/name）。支持 glob；{{stem}} 表示不带扩展名的文件名（会转成小写和连字符）
[assets]
{assets}

# 默认评测配置：evalp eval run 和 MCP 的 run_eval 省略参数时读取；命令行 / 工具参数优先
[eval]
dataset = "dataset/{prefix}-regression"
evaluators = "evals.yaml"            # 评估器配置文件（相对本目录）
# repeats = 3                        # 每个用例重复次数
# baseline = "baseline"              # 基线版本（标签 / vN），设置后新版本与它对比
# concurrency = 8                    # 每个评测的并发上限
# versions = ["latest"]              # 默认评测哪些版本

# [mcp]
# autostart = false                  # 关闭 MCP 启动时自动拉起 evalp serve
"""

CONFIG_ASSETS = """\
"prompts/system.md" = "prompt/{p}-system"
# "tools/*.yaml" = "tool/{{stem}}"
# "knowledge/*.md" = "knowledge/{{stem}}"
"datasets/regression.jsonl" = "dataset/{p}-regression"
"""

CODE_ASSETS = """\
"datasets/regression.jsonl" = "dataset/{p}-regression"
"""

CONFIG_YAML = """\
schema: evalp/v1
kind: config
model:
  provider: deepseek          # 改成你在 Web UI「设置」里登记的 Provider 名称
  name: deepseek-chat
  params:
    temperature: 0.3
    max_tokens: 2048
prompts:
  system: file:prompts/system.md     # push 时上传为 prompt 资产，并改写为 asset:// 引用
  variables:
    company: 示例公司
# tools:
#   - file:tools/query-order.yaml    # 需要在 [assets] 中添加 "tools/*.yaml" = "tool/{stem}"
runtime:
  max_steps: 8
  turn_timeout_s: 120
"""

CODE_YAML = """\
schema: evalp/v1
kind: code
transport: http                # http | subprocess
http:
  base_url: http://127.0.0.1:8001     # 你的 agent 服务地址（协议见 docs：evalp-agent/1）
  timeout_s: 300
# transport: subprocess 时改用：
# subprocess:
#   command: ["python", "-m", "my_agent.evalp_entry"]
#   cwd: "."                  # 相对本目录，push 时转为绝对路径
source:
  repo: "."                    # 相对本目录，push 时转为绝对路径
  capture_git: true            # 发布时记录 commit；有未提交改动会被拒绝（allow_dirty: true 可放开）
  allow_dirty: false
params: {}                     # 原样透传给 agent，也是版本差异的一部分
runtime:
  turn_timeout_s: 300
"""

PROMPT_MD = """\
你是{{ variables.company }}的智能客服助手。请遵守以下规则：

## 角色与语气
- 使用简体中文，礼貌、简洁，先回答问题，再给出下一步操作建议；
- 不确定的信息不要编造，如实告知并建议联系人工客服。

## 业务规则（示例，请按实际业务修改）
- 商品签收后 7 天内支持无理由退货，商品需保持完好；
- 退货流程：在「我的订单」中申请退货 -> 等待审核 -> 寄回商品 -> 退款原路返回；
- 人工客服工作时间：每天 9:00-18:00。

## 输出要求
- 回复控制在 200 字以内；
- 涉及流程时用有序列表分步说明。
"""

DATASET_JSONL = """\
{"case_key": "refund-001", "input": "我想退货，请问怎么操作？", "expected": "7 天", "tags": ["refund", "p0"]}
{"case_key": "hours-001", "input": "你们客服几点上班？", "expected": "9:00", "tags": ["faq"]}
{"case_key": "refund-002", "input": "买了三天的东西还能退吗？", "expected": "退货", "tags": ["refund"]}
"""

EVALS_YAML = """\
# 评估器配置，用法：evalp eval run --dataset <数据集> --evaluators evals.yaml
- key: keywords
  type: contains          # 回复需包含用例 expected 中的关键词
  gate: true              # 参与"是否通过"的判定
# - key: judge
#   type: llm_judge
#   provider: deepseek
#   model: deepseek-chat
#   prompt: asset://prompt/judge-default@latest
#   scale: [1, 5]
#   pass_threshold: 4
"""


def _write(path: Path, content: str, created: list[str], skipped: list[str]) -> None:
    if path.exists():
        skipped.append(path.as_posix())
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(path.as_posix())


def init(
    kind: Annotated[
        Kind, typer.Option("--kind", help="agent 类型：config（配置型）或 code（代码型）")
    ] = Kind.config,
    name: Annotated[str | None, typer.Option(help="agent 名称（默认取当前目录名）")] = None,
    force: Annotated[bool, typer.Option("--force", help="覆盖已有的 .evalp.toml")] = False,
) -> None:
    """在当前目录初始化 agent 项目：生成 .evalp.toml、agent.yaml 和示例文件，并把 .evalp/ 加入 .gitignore。"""
    cwd = Path.cwd()
    if (cwd / CONFIG_NAME).exists() and not force:
        err_console.print(f"[bold red]错误[/bold red] 当前目录已有 {CONFIG_NAME}（如需覆盖请加 --force）。")
        raise typer.Exit(1)
    agent = name or re.sub(r"[^\w.\-]+", "-", cwd.name).strip("-") or "my-agent"
    prefix = normalize_stem(agent) or "agent"
    assets = (CONFIG_ASSETS if kind is Kind.config else CODE_ASSETS).format(p=prefix)
    created: list[str] = []
    skipped: list[str] = []
    (cwd / CONFIG_NAME).write_text(
        TOML.format(name=agent, assets=assets.rstrip(), prefix=prefix), encoding="utf-8"
    )
    created.append(CONFIG_NAME)
    _write(cwd / "agent.yaml", CONFIG_YAML if kind is Kind.config else CODE_YAML, created, skipped)
    if kind is Kind.config:
        _write(cwd / "prompts" / "system.md", PROMPT_MD, created, skipped)
    _write(cwd / "datasets" / "regression.jsonl", DATASET_JSONL, created, skipped)
    _write(cwd / "evals.yaml", EVALS_YAML, created, skipped)

    ignore = cwd / ".gitignore"
    entry = f"{STATE_DIR}/"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if entry not in (x.strip() for x in lines) and STATE_DIR not in (x.strip() for x in lines):
        text = "\n".join(lines) + ("\n" if lines else "")
        ignore.write_text(text + entry + "\n", encoding="utf-8")
        created.append(".gitignore（追加 .evalp/）")

    for p in created:
        console.print(f"[green]创建[/green] {p}")
    for p in skipped:
        console.print(f"[yellow]已存在，跳过[/yellow] {p}")
    console.print(f"\nagent [bold]{agent}[/bold]（{kind.value}）已初始化。下一步：")
    console.print("  1. 编辑 agent.yaml（配置型需要把 model.provider 改成已登记的 Provider）")
    console.print("  2. [bold]evalp serve[/bold]（另开终端启动服务）")
    console.print('  3. [bold]evalp push[/bold] -> [bold]evalp publish -m "初版" --tag baseline[/bold]')
    console.print("  4. [bold]evalp eval run --wait[/bold]（数据集和评估器取自 .evalp.toml 的 [eval]）")
