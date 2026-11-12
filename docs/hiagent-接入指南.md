# HiAgent 接入指南

本文说明如何把火山引擎 HiAgent 上的对话型 agent 接入 EvalP，用于版本记录、技术指标对比和评测。
使用平台维护的 `hiagent@1` 适配器，直接调用 HiAgent 发布 API。连接配置与凭据引用在
「设置 → 适配器 → HiAgent」维护，版本只保存业务资产和运行方式。
旧 SDK 桥、本地桥发布包与运行时补丁已归档，不再作为新接入入口。

## 背景与约束

- agent 在 HiAgent 上维护（提示词 + 多个 skill，可生图 / 生视频）；`hiagentctl`（逆向控制台接口的 CLI）能把配置导出到本地目录。
- HiAgent 的对话 API **只能调用最新发布的版本**，不能指定版本。因此用法是：**在 HiAgent 发布 → 导出 → `evalp publish` 记录版本 →
  评测当前版本 → 与历史版本的评测结果对比**。历史版本不能随时重跑。
- 建议用**专门的测试应用**评测（复制生产应用），确认后再发布到生产，避免评测期间的待测版本被真实用户用到。
- 图片 / 视频质量由人工在 EvalP 的标注页审核；自动评测只看技术指标（耗时、首 token、token、成本、错误率、每个 skill 的调用次数 / 耗时 / 失败率）。

## 原生接入

原生适配器统一处理 SSE、会话身份、发布版本核对及 Trace 补齐。
`published_version` 属于平台环境配置，业务版本号与适配器实现版本分别记录。
同平台兼容升级由平台统一发布，所有绑定共用当前实现。

主提示词、Skill、MCP 与模型配置通过版本快照记录；阶段输出校验由
`output_contract` 中间件承担。API Key、Cookie／CSRF 正文只进入平台凭据存储，
不写入 `agent.yaml` 或快照文件。

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
transport: adapter
platform: hiagent
runtime:
  turn_timeout_s: 900
source:
  capture_git: false
snapshot:                      # hiagentctl 导出目录；文件随版本存档，按 map 规则入库为各类型的资产
  dir: exports/current
  map:                         # 按顺序取第一条匹配的规则；glob 与 $. 字段名按实际导出目录 / 导出 JSON 调整
    - {glob: "system-prompt.md", kind: prompt, name: "{agent}-system"}
    - {glob: "skills/*.json", kind: skill, id: "$.id", name: "{stem_nover}", display: "$.name"}   # 来源 ID、显示名取自 JSON
    # 导出的是 skill 目录（含 SKILL.md）时改用下一行：来源 ID、显示名取自 SKILL.md 的 YAML frontmatter
    # - {glob: "skills/*", kind: skill, id: "$.id", name: "{stem_nover}", display: "$.name"}
    - {glob: "plugins/*.json", kind: mcp, id: "$.plugin_id", name: "{stem}", display: "$.name"}
    - {glob: "resources/database*.json", kind: database, name: "{agent}-{stem}"}
    - {glob: "resources/tables/*.json", kind: database, name: "{agent}-table-{stem}"}
    - {glob: "resources/knowledge-*.json", kind: kb_config, name: "{agent}-{stem}"}
    - {glob: "platform/*.json", kind: app_config, name: "{agent}-{stem}"}
```

整理旧版本时，先在同一 Agent 下发布绑定完整分类资产的新版本，检查固定修订与内容，
再通过 MCP `set_milestone` 归档过程版本。已有同类型、同内容的资产应直接复用固定修订；
原来存为 `file` 的 Skill ZIP 应绑定到 `skill` 类型，不复制包或改写历史版本。

分类基线应按实际资产类型绑定固定修订；历史版本归档后仍可读取原 Spec 和资产引用。
平台部署、接入迁移与 Trace 账号配置见 [部署指南](platform-trace-deployment.md)。

## 快照与资产

`snapshot.dir` 下的文件只做记录，不参与运行。`push` / `publish` 时每个文件都会上传为资产并固化到版本里：

- `map` 里每条规则 `{glob, kind, name, id}`（`id` 可选）：`glob` 匹配**快照目录内的相对路径**（`*` 不跨目录，`**` 跨目录），命中的文件存为 `kind` 类型的资产，
  资产名由 `name` 模板生成；按顺序取**第一条**匹配的规则，所以更具体的规则写在前面（上例 `resources/database*.json` 在 `resources/tables/*.json` 之前）。
  没有命中任何规则的文件仍存为 `file` 资产（名字形如 `snap-<agent>-<路径>-<哈希>`，看不出是什么，建议补规则）。
- 可用资产类型：`prompt`、`skill`（Skill）、`mcp`（MCP 插件）、`knowledge`（知识库）、`kb_config`（知识库配置）、`database`（数据库）、
  `app_config`（应用配置）、`tool`、`dataset`、`file`。HiAgent 导出的知识库配置是 JSON 配置而不是可检索的知识文本，所以用 `kb_config`，
  不要用 `knowledge`（`knowledge` 会出现在配置型 agent 的知识库选择器里，并可被 `knowledge_search` 检索 / 注入提示词）。
- `name` 模板的变量：`{stem}` 文件名去扩展名；`{stem_nover}` 在此基础上再去掉末尾的版本号（`-0.5.3`、`-v1.2`、`-v2` 这类）；
  `{agent}` agent 名（`.evalp.toml` 的 `[agent].name`）；`{path}` 目录内相对路径去扩展名。变量展开后统一为合法资产名（小写字母、数字、连字符，最长 64）。
- **`$.` 取值与来源 ID（推荐给 skill、MCP 插件）**：`name` 或 `id` 的值以 `$.` 开头时，从导出的 JSON 文件内容按点路径取值
  （对象按键、数组按下标，如 `$.id`、`$.meta.skill_id`、`$.items.0.name`），否则仍是上面的模板。**字段名按实际导出的 JSON 调整**，
  取不到值（文件不是 JSON、路径不存在、不是字符串或数字）时 push 会报错并指出文件和规则。
  **目录（或 ZIP）形式的 skill 没有 JSON 文件**，`$.` 改为从包内默认文档 `SKILL.md` 的 YAML frontmatter 取值（点路径规则相同，如 `$.id`、`$.name`、`$.meta.skill_id`）；
  SKILL.md 没有 frontmatter 或没有该字段时 push 报错，并说明是从 SKILL.md 的 YAML frontmatter 取值。
  - `name` 取到的原文（可以是中文）保存为资产的**显示名**，资产库、资产详情、Agent「资产」标签里优先显示它；规范化后（小写字母、数字、连字符）成为资产名。
    原文里没有字母或数字（纯中文）时无法生成资产名，push 会报错。**推荐写法**：`name` 用文件名模板（如 `{stem_nover}`），
    另写 `display: "$.name"` 把 JSON 里的原名（可以是中文）作为显示名——资产名稳定、好认，页面上仍显示 HiAgent 里的原名。
  - `id` 是 HiAgent 上的稳定 ID，保存为资产的**来源 ID**。**同一个 `(类型, 来源 ID)` 始终是同一个资产**：skill 在 HiAgent 上改了名字、或导出文件名变了，
    push 时按来源 ID 找到原来的资产，自动改名并追加新修订，**修订历史保留**，旧名字记录在资产的“曾用名”里，不会再存成一个新资产；
    版本 diff 里显示为 “Skill 旧名 → 新名 · r3 → r4”。新名字被另一个资产占用时 push 报错（`ASSET_NAME_CONFLICT`），不会自动合并。
    升级前入库、同名且还没有来源 ID 的资产会被认领，不会重复。
  - 没有配置 `id` 的规则仍按资产名识别同一个资产（改名就会变成新资产）。
- **文件名带版本号**（如 `skills/crawl-engine-0.5.3.json`）且没有稳定 ID 时：用 `{stem_nover}` 命名，skill 升级到 0.5.4 后仍是同一个资产
  `skill/crawl-engine` 的新修订，而不是新资产；版本 diff 里显示为 “Skill crawl-engine r3 → r4”。
- **Skill 保留原始文件包**：导出为 `skills/<原始目录名>/`（含 `SKILL.md` 及所有子目录），或原始 ZIP。
  添加 `{glob: "skills/*.zip", kind: skill, name: "{stem_nover}"}` 和
  `{glob: "skills/*", kind: skill, name: "{stem_nover}"}`，目录整体成为一个 Skill 修订，不拆成零散资产。
  目录快照保留根目录、空目录、各层路径和文件字节；原始 ZIP 不重写。
  网页显示 `SKILL.md` 中的原始 `name`（缺省为其所在目录名），默认打开 `SKILL.md`，并提供文件树、逐文件只读预览和下载。
  引用标识仍由 `{stem_nover}` 生成；去版本号仅用于标识，不改变展示名或包内路径。
  **目录 skill 的推荐写法**：`{glob: "skills/*", kind: skill, name: "{stem_nover}", id: "$.<frontmatter 字段>", display: "$.name"}`，
  `id` 的字段名按实际 SKILL.md 的 frontmatter 调整（例如 frontmatter 里写了 `skill_id: sk-img-1` 就用 `id: "$.skill_id"`；只有 `name` 时没有稳定 ID 可取，不要硬配）。
  这样开启了 `require_source_id: [skill]` 规范的 agent 也能使用目录 skill，改目录名 / 版本号后仍是同一个资产。
  Skill 升级后版本 diff 与资产修订对比会列出包内文件的变化（新增 / 删除 / 修改及数量），文本文件的修改可点开看逐行 diff，二进制文件只列出；
  目录名带版本号（`image-gen-0.5.3/` → `image-gen-0.5.4/`）时按顶层目录内的相对路径匹配，不会显示成全部删除再全部新增。
  资产库也支持导入 ZIP / 文件夹；浏览器选择文件夹只提供文件路径，如需保留空目录请使用 ZIP 或目录快照。
  旧版仅有 JSON 元数据的导出不会被伪造成目录，页面明确提示缺少原始文件，需重新导出原始包。
- 建议导出时把 `platform/harness-agent.json` 里的 `SystemPrompt` 另存为 `system-prompt.md`（上例第一条规则）：它成为独立的 `prompt` 资产，
  版本 diff 里能逐行看提示词的变化（JSON 里的长字符串只能整段对比）。
- Skill 名称包含中文时，引用标识附加稳定哈希以避免重名，展示名保持原样。
  其他类型展开后的资产名不合法，或两个文件得到同一个资产名时，push 会报错并指出文件与规则。

通过快照入库的资产在修订元数据里带 `origin: snapshot`，在 EvalP 里**只读**：Web UI 隐藏编辑 / 新建修订 / 删除入口，并提示
“来自 agent 快照（只读），请在源平台修改后重新发布”。默认只是界面约定，API 不拦截；agent 开启 `snapshot_readonly` 规范（见下一节）后，
服务端对所有入口（含 MCP `upsert_asset` 和资产 HTTP 接口）拒绝快照推送之外的写入。修改在 HiAgent 上做，重新导出、发布即可记录新修订。

在 Web UI 里：Agent 详情页的「资产」标签按类型（提示词 / Skill / MCP 插件 / 知识库 / 数据库 / 应用配置 / 其他文件）列出所选版本绑定的资产，
每项显示原始名称（若有）、资产名、来源 ID、所用修订和最近一次变化发生在哪个版本，点击进入资产详情（内容、修订历史与对比、曾用名、被哪些版本引用）；
也可以在「资产库」按类型总览所有资产。

## 发布规范

AI 编码助手（通过 MCP / CLI）、网页、HTTP API 都能发布版本、存资产，容易各存各的：资产名乱起、改个名字又存成新资产、版本标签随意。
**发布规范**把约定固定在服务端：规范按 agent 配置，存在服务端的 agent 记录上（**不放 `agent.yaml`**，发布方无法自己删掉绕过），
服务端对所有入口一致地强制，违反时返回错误码 `CONVENTION_VIOLATION`（HTTP 400；与现有状态冲突如标签重复、写入只读资产为 409），
`message` 用中文写明违反了哪条、怎么改。MCP 助手在发布、推送前先调用 `get_conventions` 了解规范，被拒绝时把服务端的说明原样给助手，照着改即可。

**怎么开启**（只能用这两种方式，改 `agent.yaml` 没有用）：

- 网页：Agent 详情页 →「规范」标签，查看并修改；
- 命令行：

  ```bash
  evalp agent conventions show
  evalp agent conventions set --tag-from-param hiagent_version --require-notes \
      --require-source-id skill,mcp --snapshot-readonly
  evalp agent conventions set --no-require-notes --tag-from-param ""     # 只改给出的项；空字符串 / --no-… 表示关闭
  ```

| 规范 | 含义 |
|---|---|
| `tag_from_param`（如 `hiagent_version`） | 发布**必须**带标签，且标签必须等于 `v{params.hiagent_version}`（`params.hiagent_version: "0.5.3"` -> `v0.5.3`）；同一标签已存在于该 agent 时拒绝（说明这个版本号已经发布过）。标签与版本在同一个事务里创建，不会出现“版本发布了但标签没打上”；内容完全相同的重复发布（返回已有版本）不算重复。只约束发布，之后手动补充别名标签（如 `prod`）不受限。 |
| `require_notes` | 发布说明必填（CLI `-m`，MCP `notes`，网页“备注”）。 |
| `require_source_id`（如 `skill,mcp`） | 快照 `map` 里这些类型的规则必须配置 `id`（来源 ID），否则拒绝推送和发布。保证这些资产改名后仍是同一个资产。 |
| `snapshot_readonly` | 来自快照的资产（`origin: snapshot`）只允许快照推送写入，新建修订、`upsert`、改描述、删除一律拒绝（含 MCP `upsert_asset` 和资产 HTTP 接口）。要手工修改时先关闭它。 |

违反多条规范时一次返回全部，`details.violations` 是结构化的列表（`convention`、`message`，以及 `expected_tag`、`path` 等）。例如缺标签时：

```
agent「hiagent」的发布规范不允许这次发布：
规范 tag_from_param 要求标签等于 v{params.hiagent_version} = v0.5.3，发布时必须带这个标签：CLI 用 evalp publish --tag v0.5.3，MCP 用 publish_version 的 tag 参数，HTTP 在发布请求里带 tag。
```

## 日常流程

```bash
# 1. 在 HiAgent 测试应用上修改并发布（hiagentctl）
# 2. 导出到 exports/current，并更新 agent.yaml 的 params.hiagent_version
# 3. 记录版本（开启了 tag_from_param 时，标签必须等于 v{params.hiagent_version}）
evalp publish -m "拆分生视频提示词" --tag v0.5.4
# 4. 评测当前版本（会打印本次 run id），再与上一版的评测结果对比
evalp eval run --versions v0.5.4 --wait
evalp eval compare <上一版的 run id> <本次 run id>
```

数据集、评估器、重复次数可以写进 `.evalp.toml` 的 `[eval]` 段，省得每次在命令行上带。

评测报告里看整体指标与"按工具"的表格；评测对比页看两次运行之间每个用例与每个 skill 指标的变化；图片 / 视频在人工标注页审核。
对话层面的对比用「重放」：把旧版本时期的会话在当前版本上重放，并排查看。
平台内置的 HiAgent 适配器（直接调用发布 API，不经桥接）由上游会话维护历史、不接受外部历史，所以只能用「自由重放」；对它发起「锚定重放」会被拒绝。

## 用平台内置的 HiAgent 适配器（不经桥接）

版本 `transport: adapter` 时（新建 Agent 时勾选「由平台托管适配器接入」，或 agent.yaml 里写 `transport: adapter`，不需要也不能写 `adapter:` 配置），
平台直接调用 HiAgent 发布 API。连接配置**不在版本里**，而是在网页的 **设置 → 适配器 → HiAgent** 里配置。
每个平台独立维护两套环境；省略版本 `platform` 字段时默认使用 HiAgent：

- **生产**：所有这类版本默认使用；新发布的版本自动沿用，不需要任何绑定操作。新建对话 / 评测 / 重放 / 双栏对比默认都用生产。
- **测试**：只用来验证适配器改动（例如换了 HiAgent 的发布版本号、应用 ID、地址或凭据）。

**改动流程**：

1. 设置 → 适配器 → HiAgent，在「测试」卡片点「配置 / 编辑」：填 `base_url`、`app_id`、`user_id`、`published_version`、可选的 Trace 地址，
   选择凭据（没有就「新增凭据」，API Key 或含 `api_key` / `observe_headers` 的 JSON，只保存引用），点「检查上游认证」，写一句存档说明后保存。
2. 测试已配置且与生产不同后，新建对话 / 评测 / 重放 / 双栏对比的对话框里会出现「生产 | 测试」切换（默认生产）。选「测试」验证；
   会话页和评测报告页的标签会显示「测试 · 存档 #N」。
3. 验证通过后在「测试」卡片点「推送到生产」，生产得到一个新存档（来源「推送」）。生产有问题时点生产卡片的「回滚」，回到某个历史存档。

每个环境的每次变化都会存档（存档号、时间、说明、来源：编辑 / 推送 / 回滚 / 迁移），内容没变化时保存或推送不产生新存档；
历史在设置页的「存档历史」里查看。会话和评测会记下所用环境与存档号，之后改配置不影响已有运行。
发布 API Key 过期时新增凭据并更新测试、推送生产。在旧轮次 Trace 面板点「重新获取 Trace」，
选择平台最新配置（默认原会话环境），或直接选择新凭据。重查保留原应用、版本和消息 ID，查询信息单独记录。
缺少上游消息 ID 的轮次无法补查；`partial` 可重查，失败不会清空已取得的指标。
评测缓存按配置区分：配置不同不共用缓存，同一份配置共用。测试未配置、或与生产相同时，选「测试」也会落到生产（界面不再出现切换）。

MCP / CLI 同样：`chat`、`run_eval`、`replay_session`（MCP）与 `evalp chat|eval run|replay --env test`（CLI）可选环境，默认生产；
MCP 另有 `get_adapter_environments`、`update_test_adapter`、`push_adapter_to_production`、`rollback_production_adapter`。

### Trace 账号与自动重登

在设置 → 适配器 → HiAgent 的「Trace 账号」中添加站点根地址、租户（可选）、用户名和密码。
随后在测试配置中填写同站点的 `observe_base_url` 并选择「Trace 账号」，检查上游认证、保存、推送生产。
绑定中只保存 `observe_account_ref`；同账号的所有 Trace 查询共用最新 Cookie／CSRF。
发布 API Key 仍是独立不可变凭据，账号管理不会自动更换 Key。

平台参考 hiagentctl 的页面登录协议：检查 `/api/auth/checkLogin`，确认会话失效后获取验证码，
最多识别尝试三次，以页面兼容的 AES 格式提交密码和验证码，再检查登录结果。
没有 refresh_token 轮换，也不启动 hiagentctl 子进程。密码和完整 Cookie 属性加密保存在 SecretStore；
每次登录或 Cookie 更新追加修订，列表和修订接口均不回显密钥正文。

登录态通常每五分钟探活；站点提供 Cookie 到期时间时提前一分钟检查。
启动时恢复探活，查询遇到 HTTP 401/403 或明确的未登录业务错误时立即核验。
仅确认会话过期才重新登录，并最多重放一次原只读 Observe 请求。权限不足的 403 不反复重登，
网络错误冷却一分钟后再检查，不当作已过期。检查和查询按账号串行执行，避免并发重复登录。

密码错误、首次登录、密码到期或其他验证要求会暂停自动尝试，并在账号列表显示原因。
可以更新密码、点击「检查／重新登录」，或先在站点完成验证，再点击「补充登录态」提交
`{"Cookie":"…","X-Csrf-Token":"…"}`。平台核验成功后才保存新修订；编辑密码留空保留原密码。
站点、租户和用户名代表固定账号身份，切换身份时新增账号；停用保留历史并停止自动探活。

首次 Trace 补齐和旧轮次重查走同一账号管理器。重查选择最新配置时采用当前配置的账号引用，
历史会话和环境快照保持不变；`usage.trace_auth` 与 `usage.trace_refresh.trace_auth`
记录实际账号、配置修订和登录态修订。自动登录失败只影响 Trace 完整度，已有执行结果与已取得指标保留。
未绑定账号的配置继续使用原 `observe_headers`，可逐步迁移。

### 指标以 HiAgent 远端为准（Trace 后台补齐）

HiAgent 的 Trace 是异步生成的，所以平台**不等 Trace**：收到最终回复（`message_end`）后本轮立即结束，会话可以马上发下一轮；
Trace 由后台任务查询（先查 GetTraceInfo，拿到 TraceID 且 EndTime 存在才拉 ListTraceSpans 明细，未完整每 0.5 秒重试，最长 `observation_wait_s`，默认 15 秒），
拿到后把 `llm.request` / `llm.response` / `tool.result` / `metrics` 等补齐事件以该轮的 `turn_id` 追加到事件流，并改写该轮的指标与会话累计。

- 本轮 `usage.trace_status`：`pending`（补齐中，界面显示「同步中」）→ `complete` / `partial`（部分缺失，原因在 `trace_note`）/ `unavailable`
  （未取得，原因在 `trace_note`，界面显示「未取得」并悬停给原因）。取消 / 出错 / 超时的轮次只要拿到 `message_id` 同样补齐，失败不影响轮次状态。
- **规范指标全部用远端**：用时 `latency_ms` = Trace 的运行时长（`usage.upstream_run_ms`），token 用 SSE 整轮 / Trace，LLM 调用次数、每次调用耗时与 token、工具耗时来自 Trace。
  补齐前、未取得时 `latency_ms` 为空，**不用本地值顶替**；HiAgent 没有的指标（首 token 时间、成本）不适用：`ttft_ms` 恒为空，界面不显示。
  会话统计、双栏对比、评测报告 / 对比、导出都用这套规范指标。
- 本地测量（`usage.local_latency_ms`、`local_ttft_ms`、`transport_ms`、`observe_ms` 等）只作诊断留在 `usage` 里，界面不显示，也不参与任何统计与对比。
- 补齐期间会话的 SSE 保持打开（不发 `idle`），补齐完成并落库后才发 `idle`；服务重启时仍是 `pending` 的轮次启动恢复时标为 `unavailable`（原因「服务重启，Trace 补齐中断」）。
- 评测：用例的评分和统计依赖远端指标，所以用例各轮执行完后若还有 Trace 在补齐，先释放并发名额，补齐结束（或未取得）再排队评分、计入统计；
  未取得用时的执行不参与用时 p50 / p95（报告里标出数量），成本不适用时不显示。

没有远端数据的 agent（配置型、普通代码型、桥接）保持原样：自己的测量就是真实指标。

## 验证

1. 在平台适配器设置中检查上游认证；该探针不提交模型请求。
2. 核对版本「资产」页的分类、固定修订和来源 ID；原始包与参考版本保持一致。
3. 业务发生变化时，通过平台 MCP 在测试环境按实际对话顺序验证；Trace 重查使用已有轮次。
4. 平台协议变更使用脱敏 SSE／Trace 契约样本测试，兼容层的历史读取同时验证。
