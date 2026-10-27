# HiAgent 接入指南（给实现桥接的 agent 看）

本文说明如何把火山引擎 HiAgent 上的对话型 agent 接入 EvalP，用于版本记录、技术指标对比和评测。
桥接服务与 HiAgent 凭据都在使用者本机，EvalP 只通过本机 HTTP 调用桥接服务。

## 背景与约束

- agent 在 HiAgent 上维护（提示词 + 多个 skill，可生图 / 生视频）；`hiagentctl`（逆向控制台接口的 CLI）能把配置导出到本地目录。
- HiAgent 的对话 API **只能调用最新发布的版本**，不能指定版本。因此用法是：**在 HiAgent 发布 → 导出 → `evalp publish` 记录版本 →
  评测当前版本 → 与历史版本的评测结果对比**。历史版本不能随时重跑。
- 建议用**专门的测试应用**评测（复制生产应用），确认后再发布到生产，避免评测期间的待测版本被真实用户用到。
- 图片 / 视频质量由人工在 EvalP 的标注页审核；自动评测只看技术指标（耗时、首 token、token、成本、错误率、每个 skill 的调用次数 / 耗时 / 失败率）。

## 要实现的两件事

### 1. 桥接服务（`integrations/hiagent/bridge.py`）

用 `sdk/`（evalp-sdk，零依赖）实现一个 HTTP agent：EvalP 每发起一轮，桥接调用 HiAgent 的 `chat_query_v2`（**必须流式**，
阻塞模式会屏蔽中间信息），把 SSE 事件转成 EvalP 事件。SDK 用法见 `sdk/README.md`，协议见 `docs/02-详细设计.md` 6.2 节。

```python
from evalp_sdk import agent, TurnContext

@agent
async def handle(ctx: TurnContext):
    ...  # 见下方事件映射

if __name__ == "__main__":
    handle.serve_http(port=8001)
```

**会话映射**：EvalP 每个会话（`ctx.session_id`）对应一个 HiAgent 会话。首轮创建 HiAgent 会话并记住映射（进程内 dict 即可），
后续轮次沿用。HiAgent 自己维护上下文，`ctx.history` 不需要再发给它。评测时每个用例是一个新会话。

**事件映射**：

| HiAgent SSE 事件 | EvalP 中的写法 | 说明 |
|---|---|---|
| `message`（回复文本增量） | 在 `ctx.llm_call(model="hiagent")` 内调用 `call.delta(text)`，结束时 `call.record(全文)` | 前端流式显示；首 token 时间从第一个 delta 算 |
| `agent_thought` / `agent_thought_end` | 一对对应一次 `ctx.tool_call(tool, tool_input 解析成的 dict)`，`agent_thought_end` 时 `t.result(输出)`；失败时 `t.error(信息)` | skill 名用 `tool` 字段；这些就是评测报告里"按工具"的指标来源 |
| `knowledge_retrieve` / `qa_retrieve` / `terminology_retrieve` / `knowledge_graph_retrieve`（及 `_end`） | `ctx.span("knowledge_retrieve", ...)`，结束事件里记录命中数等 | Trace 里显示为一步 |
| `think_message` / `agent_thought` 里的思考文本 | `ctx.log(text, level="debug")` | 可选 |
| token 用量（若 SSE 里有） | `ctx.report_usage(prompt_tokens=..., completion_tokens=..., cost=...)` | 没有就不报，成本与 token 指标为空 |
| 流中的错误事件 / HTTP 错误 | 直接抛异常 | SDK 会生成 `error` 与 `turn.end(status=error)`，计入错误率 |

`agent_thought` 与 `agent_thought_end` 在流里是成对、顺序出现的；用 `t = ctx.tool_call(...)` 后手动 `t.__enter__()` / `t.__exit__(None, None, None)`
跨事件开闭即可。

**图片 / 视频**：HiAgent 回复里的图片、视频就是文本中的链接（裸 URL 或 markdown / `<img>` / `<video>` 标签），**原样放进回复文本**，
EvalP 前端会识别并渲染（标注页同样渲染，供人工审核）。链接会过期，需要长期留档的话另行处理，本期不做。

**凭据**：HiAgent 的 API Key 只放在桥接进程的环境变量里（如 `HIAGENT_API_KEY`），**不要写进 agent.yaml**——agent.yaml 的内容会存进版本、
参与哈希，也会出现在版本 diff 里。

### 2. 版本一致性检查

因为 API 永远调用最新发布的版本，评测前必须确认 HiAgent 当前线上版本就是 EvalP 里选的那个版本，否则会把结果记到错误的版本上。

做法：`evalp publish` 时，在 agent.yaml 的 `params` 里写入 HiAgent 的版本号（如 `hiagent_version: "0.5.3"`），它会随版本固化，
并在每轮通过 `ctx.params` 传给桥接。桥接在**每个会话的首轮**用 `hiagentctl` 查询测试应用当前已发布的版本号，与 `ctx.params["hiagent_version"]`
比较，不一致就抛异常（信息写明"HiAgent 线上版本是 X，所选 EvalP 版本对应 Y"）。这样整次评测会立刻以错误结束，不会产生错误的数据。

## agent 项目配置

在一个本地目录（建议 git 管理）里：

```bash
evalp init --kind code
```

把 `agent.yaml` 改成：

```yaml
schema: evalp/v1
kind: code
name: <agent 名>
transport: http
http:
  base_url: http://127.0.0.1:8001
  timeout_s: 900          # 生视频耗时长，按实际调整
runtime:
  turn_timeout_s: 900
source:
  capture_git: false
params:
  hiagent_app: <测试应用 id>
  hiagent_version: "0.5.3"   # 每次发布前更新
snapshot:                      # hiagentctl 导出目录；文件随版本存档，按 map 规则入库为各类型的资产
  dir: exports/current
  map:                         # 按顺序取第一条匹配的规则；glob 按实际导出目录调整
    - {glob: "system-prompt.md", kind: prompt, name: "{agent}-system"}
    - {glob: "skills/*.json", kind: skill, name: "{stem_nover}"}
    - {glob: "resources/database*.json", kind: database, name: "{agent}-{stem}"}
    - {glob: "resources/tables/*.json", kind: database, name: "{agent}-table-{stem}"}
    - {glob: "resources/knowledge-*.json", kind: kb_config, name: "{agent}-{stem}"}
    - {glob: "platform/*.json", kind: app_config, name: "{agent}-{stem}"}
```

## 快照与资产

`snapshot.dir` 下的文件只做记录，不参与运行。`push` / `publish` 时每个文件都会上传为资产并固化到版本里：

- `map` 里每条规则 `{glob, kind, name}`：`glob` 匹配**快照目录内的相对路径**（`*` 不跨目录，`**` 跨目录），命中的文件存为 `kind` 类型的资产，
  资产名由 `name` 模板生成；按顺序取**第一条**匹配的规则，所以更具体的规则写在前面（上例 `resources/database*.json` 在 `resources/tables/*.json` 之前）。
  没有命中任何规则的文件仍存为 `file` 资产（名字形如 `snap-<agent>-<路径>-<哈希>`，看不出是什么，建议补规则）。
- 可用资产类型：`prompt`、`skill`（Skill）、`mcp`（MCP 插件）、`knowledge`（知识库）、`kb_config`（知识库配置）、`database`（数据库）、
  `app_config`（应用配置）、`tool`、`dataset`、`file`。HiAgent 导出的知识库配置是 JSON 配置而不是可检索的知识文本，所以用 `kb_config`，
  不要用 `knowledge`（`knowledge` 会出现在配置型 agent 的知识库选择器里，并可被 `knowledge_search` 检索 / 注入提示词）。
- `name` 模板的变量：`{stem}` 文件名去扩展名；`{stem_nover}` 在此基础上再去掉末尾的版本号（`-0.5.3`、`-v1.2`、`-v2` 这类）；
  `{agent}` agent 名（`.evalp.toml` 的 `[agent].name`）；`{path}` 目录内相对路径去扩展名。变量展开后统一为合法资产名（小写字母、数字、连字符，最长 64）。
- **skill 文件名带版本号**（如 `skills/crawl-engine-0.5.3.json`）：用 `{stem_nover}` 命名，skill 升级到 0.5.4 后仍是同一个资产
  `skill/crawl-engine` 的新修订，而不是新资产；版本 diff 里显示为 “Skill crawl-engine r3 → r4”。
- 建议导出时把 `platform/harness-agent.json` 里的 `SystemPrompt` 另存为 `system-prompt.md`（上例第一条规则）：它成为独立的 `prompt` 资产，
  版本 diff 里能逐行看提示词的变化（JSON 里的长字符串只能整段对比）。
- 展开后的资产名不合法（比如文件名全是中文，`{stem}` 展开为空），或两个文件得到同一个资产名，push 会报错并指出文件与规则。

通过快照入库的资产在修订元数据里带 `origin: snapshot`，在 EvalP 里**只读**：Web UI 隐藏编辑 / 新建修订 / 删除入口，并提示
“来自 agent 快照（只读），请在源平台修改后重新发布”（只是界面约定，API 不拦截）。修改在 HiAgent 上做，重新导出、发布即可记录新修订。

在 Web UI 里：Agent 详情页的「资产」标签按类型（提示词 / Skill / MCP 插件 / 知识库 / 数据库 / 应用配置 / 其他文件）列出所选版本绑定的资产，
每项显示所用修订和最近一次变化发生在哪个版本，点击进入资产详情（内容、修订历史与对比、被哪些版本引用）；
也可以在「资产库」按类型总览所有资产。

## 日常流程

```bash
# 1. 在 HiAgent 测试应用上修改并发布（hiagentctl）
# 2. 导出到 exports/current，并更新 agent.yaml 的 params.hiagent_version
# 3. 记录版本
evalp publish -m "拆分生视频提示词" --tag v0.5.4
# 4. 评测当前版本（会打印本次 run id），再与上一版的评测结果对比
evalp eval run --versions v0.5.4 --wait
evalp eval compare <上一版的 run id> <本次 run id>
```

数据集、评估器、重复次数可以写进 `.evalp.toml` 的 `[eval]` 段，省得每次在命令行上带。

评测报告里看整体指标与"按工具"的表格；评测对比页看两次运行之间每个用例与每个 skill 指标的变化；图片 / 视频在人工标注页审核。
对话层面的对比用「重放」：把旧版本时期的会话在当前版本上重放，并排查看。

## 验证

1. 桥接启动后执行 `evalp adapter check --version <版本>`，协议检查全部通过。注意自检会真实调用 HiAgent，产生少量费用。
2. 在 EvalP 网页上对话一轮，Trace 里能看到每次 skill 调用（名称、入参、耗时），回复中的图片 / 视频能直接显示。
3. 故意让 `params.hiagent_version` 与线上不一致，对话应立刻报错。
4. 为桥接写测试：用录制下来的一段 SSE 回放（脱敏）验证事件映射，不要在测试里调真实 HiAgent。
