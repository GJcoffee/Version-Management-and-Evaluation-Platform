# MCP 接入指南

通过 MCP，Claude Code、Cursor 等编码助手可以直接操作 EvalP：推送草稿、发布版本、试聊、跑评测，并读取面向 LLM 整理好的评测报告，
形成"改 prompt → 发布 → 评测 → 看退化 → 再改"的闭环。

## 1. 前提

1. 安装 EvalP（`evalp` 命令在 PATH 中）：`pip install -e "backend[dev]"`，或把虚拟环境的 `bin` 加入 PATH；
2. 启动服务（可选）：`evalp mcp` 发现服务没有运行时会**自动在后台启动 `evalp serve`**（见下文"自动启动服务"）。
   想自己管理服务（或服务在别的机器上）时，另开一个终端运行并保持：

   ```bash
   evalp serve            # 默认 http://127.0.0.1:7860，token 在 ~/.evalp/token
   ```

3. 在 agent 项目目录里初始化（已有 `.evalp.toml` 的项目可跳过）：

   ```bash
   cd my-agent
   evalp init             # 生成 .evalp.toml、agent.yaml、prompts/system.md、datasets/regression.jsonl、evals.yaml
   ```

   然后把 `agent.yaml` 里的 `model.provider` 改成你在 Web UI「设置」里登记的 Provider。

MCP Server 是 `evalp mcp`，用 stdio 传输。**工作目录就是 agent 项目目录**：它从当前目录（向上）查找 `.evalp.toml`，
所以所有工具里的 `agent` 参数都可以省略。

### 自动启动服务

`evalp mcp` 启动时检测 `.evalp.toml` 里 `[server].url`（默认 `http://127.0.0.1:7860`）上有没有服务；没有，并且地址是本机时，
会以子进程启动 `evalp serve`（日志写在数据目录的 `mcp-autostart.log`），第一次工具调用时等待它就绪（最多 30 秒）。要点：

- **自己拉起的服务会随 MCP 进程一起退出**（先正常终止，超时再强制，不留僵尸进程）；已经在运行的服务只复用，不会被停止；
- 服务中途退出（比如被另一个 MCP 进程带走）时，下次工具调用会自动重新拉起；
- 远程地址不会自动启动，工具会提示先在服务所在机器运行 `evalp serve`；
- 关闭自动启动：`evalp mcp --no-autostart`，或在 `.evalp.toml` 里写：

  ```toml
  [mcp]
  autostart = false
  ```


## 2. 在 Claude Code 中配置

在 agent 项目根目录创建 `.mcp.json`（可以提交到 git，团队共用）：

```json
{
  "mcpServers": {
    "evalp": {
      "command": "evalp",
      "args": ["mcp"]
    }
  }
}
```

或者用命令添加：

```bash
claude mcp add evalp -- evalp mcp
```

首次使用时 Claude Code 会询问是否信任该项目的 MCP Server，选择允许。在会话里用 `/mcp` 可以看到 `evalp` 及其 19 个工具。

如果 `evalp` 不在 PATH 中，写绝对路径（例如 `"/home/me/evalp/.venv/bin/evalp"`）。
如果服务使用了非默认数据目录，加上环境变量：

```json
{
  "mcpServers": {
    "evalp": {
      "command": "evalp",
      "args": ["mcp"],
      "env": {"EVALP_DATA_DIR": "/data/evalp"}
    }
  }
}
```

## 3. 在 Cursor 中配置

Cursor 不一定在项目目录启动 MCP 进程，所以用 `--project-dir` 显式指定。在项目根目录创建 `.cursor/mcp.json`
（全局配置则放在 `~/.cursor/mcp.json`，并把路径写成具体的项目目录）：

```json
{
  "mcpServers": {
    "evalp": {
      "command": "evalp",
      "args": ["mcp", "--project-dir", "${workspaceFolder}"]
    }
  }
}
```

然后在 Cursor Settings → MCP 中确认 `evalp` 显示为已连接（绿点），Agent 模式下即可调用。

## 4. 工具一览

所有工具返回 Markdown：摘要 + 关键 id + Web UI 链接；长内容会截断并说明如何获取完整内容。出错时返回 `isError`，并给出中文原因。

| 工具 | 主要参数 | 作用 |
|---|---|---|
| `push_draft` | `force?` | 把本地 `agent.yaml` 和有变化的 prompt / 工具 / 数据集推送到草稿；返回上传的文件、草稿 revision、校验结果。服务端草稿被改过时报冲突（附差异），`force=true` 覆盖 |
| `pull_draft` | `force?` | 拉取服务端草稿（比如有人在 Web UI 里改过），写回本地文件。本地有未推送的修改时拒绝覆盖，除非 `force` |
| `publish_version` | `notes?`、`tag?` | 先 push 再发布；返回版本号、是否已存在（内容相同不会新建）、相对父版本的变更摘要 |
| `list_versions` | `agent?`、`limit?` | 版本列表：版本号、标签、时间、来源、备注 |
| `diff_versions` | `left`、`right`、`agent?`、`max_lines?` | 两个版本的变更列表 + 文本 diff（默认截断到 200 行）。版本可写 `v3`、标签或版本 id |
| `chat` | `message`、`version?`、`session_id?`、`agent?` | 和某个已发布版本对话一轮；返回回复、工具调用摘要、`session_id`（继续对话时传回） |
| `run_eval` | `dataset`、`versions?`、`evaluators?`、`repeats?`、`baseline?`、`wait?` | 发起评测。`wait` 默认 true，最多等 10 分钟，返回报告摘要和与基线的对比；`false` 时只返回 run id |
| `get_eval_report` | `run_id`、`failed_only?`、`limit?` | 指标摘要 + 未通过 / 低分 / 执行失败的用例（输入、期望、输出、评审理由） |
| `get_eval_compare` | `left`、`right`、`case_class?`、`limit?` | 整体结论（Δ、置信区间、改善 / 退化计数）+ 退化用例明细（附两侧输出和评审理由） |
| `get_eval_status` | `run_id` | 评测的状态、进度和预计剩余时间（不含用例明细） |
| `list_agents` | `limit?` | agent 列表：类型、最新版本、标签 |
| `get_draft` | `agent?` | 服务端草稿的 YAML、revision 和相对基础版本的变更摘要 |
| `update_draft` | `patch`、`expected_revision`、`agent?` | 对**服务端草稿**应用 JSON Merge Patch（`null` 删除键，数组整体替换）；返回新 revision 和校验结果。不改本地文件，之后用 `pull_draft` 同步，否则下次 `push_draft` 会报冲突 |
| `list_assets` | `kind?`、`q?`、`limit?` | 资产列表：最新修订、大小、说明 |
| `upsert_asset` | `kind`、`name`、`content` 或 `path`、`message?` | 创建资产或新增修订（内容相同则不新增）；`path` 是项目目录内的文件，大文件 / 二进制自动走上传 |
| `import_dataset` | `name`、`path`、`format?`、`column_map?` | 把项目目录内的 JSONL / CSV 导入为数据集（同名则新增修订）；返回用例数、引用和解析警告 |
| `get_session` | `session_id`、`turn?`、`detail?` | 会话记录：每轮的输入、回复、工具调用、耗时与 tokens（摘要最多 30 轮）；指定 `turn` 展开该轮，`detail="full"` 再列出 trace 步骤 |
| `replay_session` | `session_id`、`target_version`、`mode?`、`turns?`、`wait?` | 用另一个版本重放历史会话；`mode` 为 `anchored`（锚定，默认：每轮的上下文用原会话的历史，各轮并发）或 `free`（自由：新版本用自己的回复续写上下文，逐轮串行，更慢）；`wait` 默认 true，等重放结束后给出逐轮对比摘要 |
| `compare_sessions` | `left`、`right`、`max_turns?` | 逐轮对比两个会话：步骤增减、指标差值、工具参数差异、回复 diff（只展开有差异的轮次） |

约定：

- `dataset` 可以写数据集名称（`support-regression`）、`dataset/support-regression@3` 或修订 id；
- `evaluators` 省略时读取 `[eval].evaluators`，再省略则读项目根目录的 `evals.yaml`，也可以传评估器对象列表；
- `versions` 省略时评测最新版本；给了 `baseline` 时每个版本都与基线对比，基线在对比的左侧；
- 评测等待超时（10 分钟）不会取消评测，稍后用 `get_eval_report` / `get_eval_status` 查看；
- `upsert_asset` / `import_dataset` 的 `path` 必须在项目目录内；`replay_session` 的 `mode=anchored` 精确比较单轮差异（排除前面轮次差异的累积影响），`mode=free` 观察整段对话的走向；自由模式只重放 `turns` 里选中的轮次时，上下文只含选中的前几轮。重放中途有轮次没能执行时，摘要里会注明「重放未能完整执行」。

### 默认评测配置（`.evalp.toml` 的 `[eval]`）

`run_eval`（以及命令行的 `evalp eval run`）省略的参数从 `[eval]` 读取，显式传入的参数优先。`evalp init` 生成的模板已经带了前两项：

```toml
[eval]
dataset = "dataset/support-regression"   # 数据集名称或引用
evaluators = "evals.yaml"                # 评估器配置文件（相对项目目录）；也可以写 [[eval.evaluators]] 表数组
repeats = 3                              # 每个用例重复次数
baseline = "baseline"                    # 基线版本（标签 / vN），设置后新版本与它对比
concurrency = 8                          # 每个评测的并发上限
versions = ["latest"]                    # 默认评测哪些版本
```

这样助手只需要 `run_eval()`，不用每次都带一串参数。写错键名或类型时，读取项目配置的命令会直接报错并指出位置。

## 5. 典型工作流

场景：客服 agent 的"退货"回答不够准确，让助手改 prompt 并验证没有引入退化。项目里已经有 `baseline` 标签指向的版本和数据集
`support-regression`。

1. **对你说**："用户反馈退货流程说得不清楚，改一下 `prompts/system.md`，然后跑回归评测，和 baseline 对比。"
2. 助手修改 `prompts/system.md`（编辑本地文件，不需要 MCP）。
3. 助手调用 `push_draft`：

   ```
   ## 已推送草稿
   - agent: `support`
   - 草稿 revision: 4（draft_id `drf_01…`）
   - 上传了 1 个文件:
     - `prompts/system.md` -> prompt/support-system rev 3
   - 草稿校验: 通过
   ```

4. 助手调用 `publish_version(notes="明确退货流程和时限")`，得到 `v5` 以及相对 `v4` 的变更摘要。
5. 助手调用 `chat(message="买了三天的东西还能退吗？", version="v5")` 试聊一轮，确认回复符合预期；
   继续追问时带上返回的 `session_id`。
6. 助手调用 `run_eval(dataset="support-regression", versions=["v5"], baseline="baseline")`，等待结束后拿到：

   ```
   ### 未通过的用例（共 2 条，按得分从低到高显示前 2 条）
   #### `refund-003` 得分 0
   - 输入: 订单签收十天了还能退吗
   - 期望: 超过 7 天
   - 输出: 可以的，请在订单页申请退货。
   - 评估器 `kw`: 0（未通过）：缺少: '超过 7 天'
   ...
   ## 评测对比
   ### 整体结论
   **退化 1**，改善 3，不变 46 ...
   - 平均 Δ（右 - 左）: +0.04，95% 置信区间 [-0.01, +0.09]
   ### 退化的用例（显示 1/1）
   - `refund-003` [p0]: 1 -> 0（Δ -1）
     - 基线输出: …  对比输出: …
   Web UI: http://127.0.0.1:7860/evals/compare?left=evr_…&right=evr_…
   ```

7. 助手根据退化用例继续修改 prompt，重复 3–6 步；满意后 `publish_version(tag="candidate")`，
   把 Web UI 的评测对比链接发给你做最后确认。

其他常用用法：

- "看一下 v4 和 baseline 差在哪" → `diff_versions(left="baseline", right="v4")`；
- "上次那个评测为什么有失败" → `get_eval_report(run_id="evr_…")`；
- 在 Web UI 里改了草稿后，助手 `push_draft` 会报冲突，先 `pull_draft` 合并，或确认后 `push_draft(force=true)`。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 工具返回"无法连接到 EvalP 服务…请先运行 evalp serve" | 自动启动被关闭或地址不是本机：手动启动 `evalp serve`；服务不在默认端口时，在 `.evalp.toml` 的 `[server].url` 写明地址 |
| 工具返回"自动启动 EvalP 服务失败" | 按提示查看数据目录下的 `mcp-autostart.log`（常见原因：端口被其他程序占用），或手动运行 `evalp serve` 排查 |
| "找不到 API token" | MCP 进程读不到服务的数据目录：在 `env` 里设置 `EVALP_DATA_DIR`，或给 `evalp mcp` 加 `--data-dir` |
| "当前工作目录不是 agent 项目" | 在项目根目录启动，或加 `--project-dir`（Cursor 用 `${workspaceFolder}`） |
| 草稿校验未通过（Provider 不存在等） | 按提示在 Web UI「设置」登记 Provider，或修改 `agent.yaml`；校验失败时 `publish_version` 会被拒绝 |
| 客户端里看不到工具 | 在终端运行 `evalp mcp --help` 确认命令可用；Claude Code 用 `/mcp` 查看连接状态和错误 |

调试时可以直接在终端运行同等功能的命令：`evalp push`、`evalp publish -m …`、`evalp eval run --wait`、`evalp eval report <run_id>`、
`evalp eval status <run_id>`、`evalp asset ls`、`evalp dataset import …`、`evalp replay <session> --to v3 --wait`。
CI 里可以用 `evalp eval gate <run_id> --baseline <基线 run_id> --max-regressions 0`：不满足条件时退出码为 1。
