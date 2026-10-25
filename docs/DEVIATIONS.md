# 与详设的偏离记录

格式：`## <模块> — <简述>`，写明原因与影响。

## 工程结构 — Alembic 迁移目录放在包内

详设 1 节把迁移放在 `backend/alembic/`。实际放在 `backend/evalp/store/migrations/`（含 `env.py`、`script.py.mako`、`versions/`），
通过 `evalp.store.migrate.run_migrations(db_path)` 程序化执行，无需 `alembic.ini`。原因：迁移脚本需要随 wheel 发布，
并在服务启动时自动执行。影响：新增迁移时，用 `evalp.store.migrate.make_alembic_config(db_path)` 构造配置后调用
`alembic.command.revision(cfg, autogenerate=True, ...)`；`env.py` 已启用 `render_as_batch`（SQLite 改表需要）。

## 存储/models — 手工维护的两个表达式索引

`ix_sessions_agent_mode`（含 `created_at DESC`）与 `ix_jobs_claim`（含 `priority DESC`）是带排序方向的表达式索引，
SQLite 方言无法反射比较。它们在初始迁移中用原生 SQL 创建，并在 `models.MANUAL_INDEXES` 中登记，autogenerate 与
一致性测试会忽略这两个索引（排序方向由 `test_indexes_and_without_rowid` 单独核对）。

## 存储/models — 小的实现细节

- `eval_cases.metadata` 列在 ORM 中的属性名为 `metadata_`（`metadata` 是 SQLAlchemy 声明式基类的保留名）；列名仍为 `metadata`。
- `turns.error`、`eval_runs.error`、`eval_items.error` 按 DDL 为普通 TEXT（不做 JSON 序列化），需要结构化错误时调用方自行 `json.dumps`。
- 迁移与 DDL 的约束名按命名规范自动生成（`uq_/ck_/fk_/pk_` 前缀），DDL 中的匿名约束因此有了名字。
- 读连接额外设置 `PRAGMA query_only=ON`；写连接使用 `BEGIN IMMEDIATE`。

## 错误码 — 新增 11.2 之外的通用错误码

11.2 没有覆盖以下场景，新增：`INTERNAL_ERROR`（500，未捕获异常，响应不含内部细节）、`FORBIDDEN`（403，非回环地址访问
`/auth/bootstrap`）、`CONFLICT`（409，通用冲突，如 Provider 名称重复；`Conflict(msg, code="...")` 可指定其他 code）、
`METHOD_NOT_ALLOWED`（405）、`HTTP_ERROR`（其他框架级 HTTP 错误）。

## API — 分页越界与文档路径

- `limit` 小于 1 或大于 200 时返回 400 `VALIDATION_ERROR`（不静默截断）。
- OpenAPI 文档放在 `/api/docs` 与 `/api/openapi.json`（位于 `/api` 下，因此同样需要 token / Cookie）。

## 前端脚手架 — 依赖版本与 UI 实现（总控代记）

- UI 未使用 shadcn/Radix，改为 Tailwind v3.4 + 手写组件（风格参考 shadcn）。
- 选用稳定版本而非最新大版本：react-router-dom 6.30、@tanstack/react-table 8.21、echarts 5.6（配合 echarts-for-react 3）、tailwind-merge 2.6（Tailwind v3 配套）、vite 7 / vitest 3 / TypeScript 5.9 / ESLint 9。
- 新增列表路由 `/agents`、`/evals`（14.1 节未列，侧边栏需要）。
- 健康检查路径为 `/api/v1/health`（16.3 节简写为 `/health`）。

## 运行时/LLM 客户端 — stream_chat 返回流对象，合并结果从 `stream.response` 读取

6.4 节伪代码用 `llm.last_response` 取合并结果，但 LLMClient 是被并发共享的单例，该写法有竞态。实际：`llm.stream_chat(...)`
是普通函数（不是协程），返回 `LLMStream`：`async for chunk in stream` 取增量，迭代结束后用 `stream.response`（或
`await stream.result()`）取 `LLMResponse`。`LLMResponse.event_fields()` 直接给出 `llm.response` 事件的 payload 字段。
提前终止流时请 `await stream.aclose()`（归还限流许可）。

## 运行时/LLM 客户端 — 重试与限流的细节取舍

- 6.8 节的"最多 4 次"按 4 次**重试**理解（共最多 5 次请求）；`Retry-After`（含 `retry-after-ms`）优先，上限 120s，
  不再叠加抖动；`RetryPolicy` 可在 `LLMClient.retry` 上调整（测试里把基础间隔调小）。
- 首个 token 之前的传输中断（含只收到角色块的流）视为未开始输出，可重试；之后出错抛 `ProviderError(details.retriable=True)`。
- 服务端拒绝 `stream_options`（400/422 且消息含 stream_options / include_usage）时自动去掉重发，不计入重试次数，
  并在该 Provider 句柄上记住（Provider 配置变更导致客户端重建后重新探测）。
- RPM / TPM 令牌桶容量等于每分钟额度（启动时是满的）；单个请求的预扣值超过桶容量时按容量计，避免永远等不到；
  响应后实际用量超过预扣值时桶可以变成负数（"欠账"），之后的请求会等到补回。
- 缺失的 Provider 抛 `SpecInvalid`（400），而不是 `NotFound`。
- 限额热更新：`LLMRegistry.get()` 每次（带 2s 缓存，`registry.cache_ttl` 可调）从 ProviderService 读取配置，
  变更后推送给限流器；`RateLimiter` 本身也支持 `limits_source + ttl`。`ProviderService.update` 不会主动让缓存失效
  （该文件不在本模块范围内），因此变更最多延迟 2s 生效。
- openai SDK 3.x 内部使用 httpx2，但同时接受传统 `httpx.AsyncClient`；注入的传输层统一包成 `httpx.AsyncClient` 传入，
  与 openai 1.x/2.x 行为一致。

## 运行时/工具 — http 工具默认 body、超时取值、Python 解释器

- http 工具未配置 `body_template` 时：POST/PUT/PATCH 把参数作为 JSON body（同 5.2 节）；**GET/DELETE 改为查询参数**
  （非字符串值序列化为 JSON），因为带 body 的 GET 会被多数服务忽略。
- 工具有效超时 = `min(调用方 timeout, 工具 impl.timeout_s)`（`impl.timeout_s` 有默认值 30），`ToolSet.execute` 外层再留 5s 兜底。
- Python 工具指定了与平台不同的 `python` 解释器时，该解释器不一定装了 evalp：在 `PYTHONPATH` 前面加一个只含
  `evalp/toolhost.py` 的临时 shim 目录（`runtime/procutil.toolhost_shim_dir`），保证 `-m evalp.toolhost` 可用，
  同时不会把平台的 site-packages 泄漏进用户环境。默认解释器（`sys.executable`）不做处理。
- 新增 `runtime/procutil.py`（`popen_kwargs` / `kill_process_tree` / `terminate_process` / `toolhost_command`），
  SubprocessAdapter 与 Python 评估器应直接复用；`runtime/tools/python_tool.run_toolhost` 是通用的 toolhost 调用函数。
- 工具结果 `ToolResult` 增加 `detail` 字段（Python 工具的 traceback / stderr、HTTP 状态码），不进入给模型的文本。
- 工具集 `ToolSet.build` 遇到重复工具名抛 `ValidationFailed`；`mocks` 中出现但工具集里没有的名字被忽略。
- `ToolSet` 持有共享的 httpx 客户端，用完请 `await tools.aclose()`（或 `async with`）。

## 运行时/knowledge_search — BM25Plus 与命中过滤

rank_bm25 的 `BM25Okapi` 在小语料上 IDF 可能为 0 或负数（如 2 个块里某词只出现 1 次），中文 FAQ 场景下会漏检，
改用 `BM25Plus`（IDF 恒正），并额外要求块至少命中一个（去停用词后的）查询词才返回。仅含 Markdown 标题的段落并入下一段。
JSONL 知识（`{id,text,meta}`）按记录分块，chunk_id 为 `{资产名}#{id}`。

## 运行时/事件 — turn_id 可空

`AgentEvent.turn_id` 为可选（`ev()` 构造时没有 turn_id），由 Runner 通过 `event.with_turn(turn_id)` 补上；
payload 模型允许额外字段（外部 agent 的自定义信息不会被丢弃）。脱敏 `redact()` 的键名规则：`authorization`、`api_key`、
`password`、`secret`、`cookie`，以及以 `_token` / `_secret` / `_api_key` 结尾的键（`max_tokens`、`prompt_tokens` 不受影响），
并抹掉字符串里的 `Bearer xxx` 与 `sk-…` 密钥。

## 测试 — Fake OpenAI 的内存传输层

httpx 自带的 `ASGITransport` 会等整个响应写完才返回，看不到首 token 延迟、也无法模拟流中途断开；
`tests/fixtures/fake_openai.py` 提供 `StreamingASGITransport`（真正的流式 + 读超时），`fake_openai` fixture 使用它。
tests/conftest.py 仅追加了 `pytest_plugins = ["tests.fixtures.fake_openai"]`。

## P2-A spec — AgentSpec 的默认值与附加字段

- 4.1/4.2 只给出了示例值，没有单列默认值。取示例值作为默认值：`model.params` 为 `temperature=0.3, top_p=1.0, max_tokens=4096, seed=null`；
  配置型 `runtime` 为 `max_steps=12, turn_timeout_s=180, parallel_tool_calls=true, tool_timeout_s=30`；代码型 `http.timeout_s=300`、
  `runtime.turn_timeout_s=300`、`source={repo:".", capture_git:true, allow_dirty:false}`。`model.params` 允许额外字段（原样透传）。
- 新增可选的顶层字段 `name` / `description` / `notes`（规则 6 提到的"显示用字段"需要有落脚点），不参与哈希，也不存入版本。
- 新增 `source.git: {commit, patch_hash}`（只读，发布时由服务端写入），使 commit 与补丁哈希参与 spec_hash；分支名和 dirty 标记只存入 `versions.source`，不参与哈希。
- 版本里的引用位置在固化后是 `{"$asset": {kind,name,rev,hash}}`，`parse_spec()` 同样接受这种形式，运行时可直接用 `parse_spec(version.spec)`。
- `transport=http` 时 `subprocess` 段（反之亦然）被丢弃，避免遗留配置影响哈希。
- 规则 5（行尾空白）只作用于 `prompts` 子树（system 与 variables 中的全部字符串）；保留行首缩进和末尾换行。
- 配置型 agent 的最小模板：已登记 Provider 时填入第一个 Provider 及其第一个模型，否则 `provider`/`name` 为空（校验时报错，提示补全）。代码型模板用
  `capture_git=false`（避免 UI 发布时误采集服务进程所在目录的仓库）。

## P2-A 资产 — latest 指针、upsert 与删除语义

- `UNIQUE(asset_id, blob_hash)` 下，把内容改回旧版本会命中旧修订。此时返回旧修订（`existed=true`），并把 `assets.latest_rev` 指回它（否则 `@latest`
  不会跟随最新内容）；修订号只增不减，新修订取 `max(rev)+1`。所以 `latest_rev` 的含义是"当前生效的修订"，不一定等于最大修订号。
- `POST /assets` 是 upsert：同名资产已存在时追加修订（新建 201，否则 200）；同名且已软删除的资产被恢复。
- `DELETE /assets/{id}` 默认软删除；`?hard=true` 才物理删除（被版本或评测引用时 409 `ASSET_IN_USE`）。新增 `POST /assets/{id}/restore`、`PATCH /assets/{id}`。
- 概设 10.1 未列出的补充接口：`GET /assets/revisions/{revision_id}`（带内容）、`.../raw`、`GET /assets/{id}/revisions/{rev}`、`GET /agents/{ref}/tags`、`GET /agents/{ref}/drafts`、`POST /agents/{ref}/drafts`。
- `file` 类资产在 AgentSpec 中没有可引用的位置（4.1/4.2 未定义），所以不会出现在 `version_asset_refs` 里；硬删除保护对它同样生效（若将来有引用）。
- `raw` 下载一律 `Content-Disposition: attachment` + `nosniff`，避免用户上传的 HTML 在本站点下被渲染。
- 资产大小上限：knowledge 20MB、file 100MB（详设）；dataset 100MB、prompt/tool 5MB 为本模块自行设置的保护上限。

## P2-A 数据集 — 解析细节

- 未知字段放入 `metadata` 并警告 `UNKNOWN_FIELD`；顶层 `variables` / `tool_mocks` 等价于写在 `metadata` 里。
- `case_hash` 中缺省的 `variables` / `tool_mocks` 按 `{}` 处理（`metadata.variables = {}` 与不写等价）。
- 自动生成 `case_key`：单轮取 `sha256(input)[:12]`，多轮取 `sha256(canonical(turns))[:12]`；警告按类型合并为一条（`count`、前 20 个 `lines`）。
- CSV：`column_map` 的 `input` 可以是列名数组（多轮）；`tags` 列按 `;` 分隔；`metadata / variables / tool_mocks` 列为 JSON 文本；`column_map` 保存在 `revision.meta`。
- `DATASET_PARSE_ERROR` 的 `details` 在 `lines` 之外还有 `errors: [{line, message}]` 和 `total_errors`（最多列出 100 条）。

## P2-A 版本与发布

- 新增错误码：`CONTENT_MISSING`（400，工作区 push 缺少内容）、`ASSET_DELETED`（409，往已软删除的资产追加修订）、`AGENT_IN_USE`（409，有版本的 agent 不能删除，请归档）。
- 发布时若 `spec_hash` 已存在，返回已有版本（`existed=true`），并把草稿的 `base_version_id` 指向它（4.4 伪代码没有这一步；草稿内容此时等价于该版本）。
- 结构错误（Pydantic）返回 `VALIDATION_ERROR`，业务错误返回 `SPEC_INVALID`，`details` 都有 `errors` / `warnings`。
- 草稿保存只做轻量检查（对象、`kind`/`schema` 一致，缺省时补上），允许保存未完成的 Spec；完整校验在 validate / publish。
- 代码型的 git 补丁用临时索引（`GIT_INDEX_FILE`）生成，等价于 `git diff HEAD` 并包含未跟踪文件（遵守 .gitignore），不修改用户仓库的暂存区；未跟踪文件也算 dirty。
- 标签名不能是 `latest` 或形如 `v12`（会与 `agent:v12` / `agent:latest` 引用冲突）。
- 有版本的 agent 不能删除，只能归档（`PATCH archived=true`）。

## P2-A 工作区同步

- 草稿已存在时，`POST /workspace/push` 必须带 `expected_revision`（除非 `force=true`）；缺省视为冲突，避免静默覆盖 UI 中的修改。冲突响应的 `details` 带 `current_revision`、`server_spec` 和 `diff`（服务端 vs 本地）。
- `files[]` 增加可选字段 `encoding`（`utf-8` | `base64`，二进制文件用）、`content_type`、`column_map`；响应增加 `agent_created`。
- `POST /workspace/pull` 的响应除 `draft` 与 `assets` 外增加 `local_spec`（引用还原为 `file:` 后的 Spec）和 `missing`；`asset_map` 方向为 `{"kind/name": "本地路径"}`。
- 新建 agent 且 `draft_name` 不是 `main` 时，同时创建一个 `main` 草稿，保证每个 agent 都有 main 草稿。

## P2-A 依赖（需总控确认）

本模块直接使用 `python-multipart`（`POST /assets/upload` 的 multipart 解析）和 `jsonschema`（校验工具的 `parameters`）。二者当前由 `mcp` 间接依赖带入，
未在 `backend/pyproject.toml` 显式声明。建议总控把它们加入 dependencies（我无权修改 pyproject）。

## P3-A 存储 — 迁移 0002 与 turns.error 的存储格式

- 迁移 `0002`：`sessions.variables`（JSON，默认 `{}`）、`turns.anchored_from_turn_id`（外键 → `turns.id`）。详设 3.3 未列出这两列：会话级变量（9.4 的创建接口要求）与锚定重放需要记录"对应原会话的哪一轮"。
- `turns.error` 列仍是 TEXT，内容约定为 JSON 字符串 `{"code","message","retriable"}`（服务层 `error_to_text` / `error_from_text` 负责编解码，读取旧的纯文本值时降级为 `{"code":"ERROR","message":原文}`）。API / `TurnOut.error` 一律是对象。
- SQLite 的 batch 重建 `sessions` 表会丢失 `ix_sessions_agent_mode` 的 `created_at DESC`（反射不出表达式索引的方向），迁移里在 batch 之后（升级和降级都）按 0001 的定义重建索引，并有测试守住。

## P3-A 运行时 — Runner 与事件

- Adapter 产出的 `turn.end` 只是"提示"：Runner 吸收其 status / output / usage / cost，但最终的 `turn.end` 始终由 Runner 汇总后产出（用量以 `llm.response` 汇总为准），保证每一轮恰好一个 `turn.start` 和一个 `turn.end`；`turn.end` 之后的事件被丢弃。
- `turn.start` 的 `history_len` 与 `input` 由 Runner 覆盖为平台实际传入的值；Adapter 自己报告的 `history_len` 只用于 `adapter-check` 的历史校验（读取原始事件）。
- 本轮新增的消息通过 `RunContext.new_messages` 收集（ConfigAdapter 追加 assistant(tool_calls) / tool / final）；代码型 agent 缺省只记录最终回复 `[final]`，L1 响应可用可选字段 `messages` 提供完整消息。下一轮的历史（6.1）因此对代码型 agent 不含工具调用细节。
- 错误码：`AGENT_PROTOCOL_ERROR`（agent 输出不符合 evalp-agent/1，不可重试）、`MAX_STEPS_EXCEEDED`、`TIMEOUT`、`INTERRUPTED`（进程重启时遗留的 running 轮次，`retriable=true`）。HTTP 429/502/503/504 以及连接失败标记 `retriable=true`。
- `llm.delta` 只发布不落库，没有 `seq`（SSE 帧也没有 `id:`）。事件先发布到总线、再按 50ms / 100 条批量落库；`turn.end` / `error` 立即落库。为保证 SSE 回放不丢事件，`stream()` 在订阅之后、回放之前会先让正在执行的轮次把缓冲刷库，并在发 `idle` 之前等本轮 `turn.end` 落库。
- 大 payload（> `events.payload_inline_limit_kb`）转存 blob：事件里留 `{"$blob": hash}`（`incref` 一次），`get_events(resolve_blobs=True)` 才展开；落库前先 `redact()` 脱敏。

## P3-A 会话 — SessionService 的取舍

- 同一会话单槽位（内存）：`SESSION_BUSY` 只在本进程内保证（单进程部署）。槽位在 `turn.end` 落库后释放；轮次行与 `sessions.stats / status` 在 `turn.end` 发布前就已更新。
- `wait=True` 的调用方任务被取消时，本轮同步被取消（置位 cancel、等 Runner 收尾后再抛 CancelledError），避免孤儿轮次。
- `get_history` 只拼接 `status=ok` 的轮次（失败 / 取消 / 超时的轮次不进历史）；`history_override` 非空时整体代替会话自身的历史，且不落库。
- 会话 `status`：最近一轮 `error | timeout` 置 `error`，否则 `idle`；下一轮成功后恢复 `idle`。启动恢复（`recover()`，应用 lifespan 里调用，首次使用服务时也会惰性调用）把遗留 running 轮次标记为 `error/INTERRUPTED`，补写 `error` + `turn.end` 事件，会话置 `idle`。
- `GET /sessions` 缺省 `mode` 过滤为 `chat,replay`（排除 `eval`、`judge`）；`agent_id` 参数同时接受 id 和名称。`POST /sessions` 只创建 `chat` 模式，其他模式由评测 / 重放模块直接调用服务。
- 空字符串输入不报错（原样交给 agent）。
- SSE 订阅者的总线队列溢出（1000）时流直接结束且不发 `idle`，客户端用 `Last-Event-ID` 续传；`Idle.last_seq` 是已发送的最后一个 seq。
- `GET /blobs/{hash}` 只开放事件引用的 blob 与资产修订 blob；事件 blob 按 JSON 返回，资产 blob 作为附件。

## P3-A Adapter — 代码型 agent 的 `subprocess.cwd` 与其他细节

- P2 发布流程没有把代码型 agent 的相对 `subprocess.cwd` 固化为绝对路径（draft / validate / publish 里都没有处理 `cwd`）。SubprocessAdapter 在执行时把相对 `cwd` 按 `source.repo`（发布时记录的仓库根）解析；`source.repo` 本身是相对路径时相对平台进程的当前目录；目录不存在报 `AGENT_START_FAILED`。命令里的相对可执行路径不做处理（走 PATH）。
- SubprocessAdapter：每轮一个进程；`stdout` 按 NDJSON 逐行解析，不是 JSON 的行记为 `log` 事件（上限 500 条）；`stderr` 按行记为 warn 级别的 `log` 事件；整个进程树在超时 / 取消 / 异常时被 psutil 终止；注入 `EVALP_SESSION_ID`、`EVALP_TURN_ID`。`turn.end` 之后到管道 EOF 之前（最长 2s）的 stderr 仍会被收集，所以最终 `turn.end` 会稍晚于 agent 的 `turn.end`。
- 子进程退出码非 0 但已经输出了合法的 L1 响应时按成功处理（日志里记 warn）；没有输出合法响应则 `AGENT_ERROR` 错误（`exit_code`），附 stderr 最后 20 行。
- HttpAdapter：响应 `Content-Type` 含 `ndjson` 走 L2，否则按 L1（JSON）；`${env:NAME}` 在 headers / base_url 中于执行时解析；执行中被取消 / 超时时调用 `POST /evalp/cancel` 并断开连接（尽力而为，不等待）。
- ConfigAdapter：Jinja2 沙箱 + `StrictUndefined`，渲染变量为 `variables`（Spec < 会话 < 单轮）、`knowledge[名称]`、`now`；也可把 Spec 变量直接用作顶层名字（`{{ tone }}`）；并行工具调用时先产出全部 `tool.call`，结果按完成顺序产出 `tool.result`，但追加到 messages 的顺序与调用顺序一致。

## P3-A evalp-sdk — 独立包

- `sdk/` 是独立的 Python 包（`sdk/pyproject.toml`，Python ≥ 3.11，零运行时依赖），用 `pip install -e sdk` 安装。HTTP 服务用标准库 `ThreadingHTTPServer` + 独立事件循环线程；`stdio` 模式默认把 fd 1 重定向到 stderr，事件走复制出来的 stdout，handler 里的 `print` 不会污染协议。
- 同步 handler 在线程里执行（emit 线程安全）；`start_http(port=0)` 返回带 `.url` / `.close()` 的句柄，测试里用它起示例 agent。

## P3-A Adapter 自检 — 探测方式

- `timeout` 探测使用 50ms 的预算、`cancel` 探测在 50ms 后取消；agent 更快完成时给 `skip`（无法验证），不当作失败。子进程型额外用 `psutil` 确认 `last_pid` 已退出。被探测主动中断的执行不参与 `events` 的成对检查。
- 自检复用 Runner 与真实 Adapter，不写库；因此配置型 agent 会真实消耗 LLM 额度（约 3–4 次调用）。

## P3-A 测试 — 对共享测试夹具的小修改

- `tests/fixtures/fake_openai.py` 的 `StreamingASGITransport` 把请求头名转成小写再放入 ASGI scope（ASGI 规定小写，否则 Starlette 的 `Headers` 取不到 `Authorization`，SSE 流式测试会 401）。
- `tests/fixtures/sample_tools.py` 增加 `slow_double`。

## 前端会话页 — 若干取舍（总控代记）

- 会话标题只读（后端暂无 `PATCH /sessions/{id}`）。
- `useSessionStream` 基于通用 `useEventStream` + reducer 实现，返回值比 14.3 多 `turns/running/runningTurn/loading`。
- 草稿编辑器改为：输入防抖干跑校验；手动保存（按钮 / Ctrl+S）+ 闲置 5 秒自动保存。
- `turn.end.status=completed` 与 `Turn.status=ok` 在前端统一映射为 `ok`。
- SSE 业务事件 `event: error` 与 `EventSource.onerror` 同名，前端已区分处理（带 data 视为业务事件）。
- 超过 32KB 的事件在库中是整个 payload 被替换为 `{$blob, ...}` 存根（不是字段级），前端按整 payload 存根按需加载。
