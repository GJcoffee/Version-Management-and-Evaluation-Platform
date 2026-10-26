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

## 对比与重放 — 重放在同一次重放内逐轮串行，不同重放之间最多并发 4 个（已取代）

早期实现因 `SessionService` 同一会话同一时间只能执行一轮，锚定重放只能逐轮串行。**已被取代**：见文末「对比与重放 — 锚定重放轮内并发」。

## 对比与重放 — replay 对比组的轮次选择存在 compare_groups.notes（已取代）

早期把 `{"replay_turns": [...]}` 写进 `notes`。**已被取代**：见文末「对比与重放 — 重放状态与所选轮次成为 compare_groups 的正式列」（迁移 0003 已把旧数据迁出，`notes` 回归用户备注）。

## 对比与重放 — 对齐结果的附加字段与 step_id 约定

在契约字段之外增加：`turns[].status`（两侧轮次状态）、`turns[].input`（两侧用户输入文本）、`tool_args_diff[].changes[].op`。
`message.assistant`（final 步骤）没有 step_id，对齐输出里固定为 `"final"`；其他没有 step_id 的步骤记为 `"e{seq}"`。
步骤对齐用 `difflib.SequenceMatcher` 的最长连续匹配，不保证"前缀相同的步骤优先对齐"。

## 评测（收窄版对接）— 仓库里已有完整版实现，按契约补齐字段，未删除 BACKLOG 能力

开始收窄版开发时，`runtime/scheduler.py`、`evaluators/`、`services/eval.py`、`api/v1/evals.py` 等已是按详设 8 完成的完整版实现
（含 80 个测试）。没有推倒重写，而是对照 `docs/contract-eval.md` 补齐：

- 响应字段只增不减：Run 增加 `name`、`dataset_rev_id`、`dataset_name`、`dataset_rev`；Item 增加 `case_id`、`case_key`、`input_preview`、
  `expected`、`tags`、`output_preview`；`status` 增加契约里的 `failed`；创建响应的 `runs[]` 增加 `status`。
- 列表参数按契约命名为 `agent_id`、`batch_id`（旧名 `agent`、`batch` 保留）；Item 列表增加 `passed`，`q` 同时匹配输出。
- SSE：`eval.item_done` 增加 `item`（完整 EvalItem；原有扁平字段保留）；结束帧由 `end` 改为契约的 `idle`；Run 已结束时只发 `eval.status` + `idle`。
- 对比：响应改为契约结构（`cases.items`、`summary.metric_deltas`、字符串数组 `warnings`）；原先的顶层 `items` / `next_cursor` 移入 `cases`，
  `summary.metrics` 保留。`exact_match` 增加 `values`，`regex.flags` 同时接受字符串。
- llm_judge 的 `score.detail.samples[]` 增加 `request_preview` / `reply_preview`（评审请求与回复摘要）。
- 修复测试 `test_a4_restart_resumes_without_rerun_or_double_billing` 的竞态：快照恰好落在"Item 已标记 running、会话尚未创建"之间时，
  废弃会话数小于 running Item 数，属于测试断言过严，已改为只统计有会话的 running Item。

**与收窄范围不一致、但已存在于代码中的部分（未删除，需总控决定是否裁剪）**：

- BACKLOG 中的能力：暂停 / 继续、`retry-failed`、`rescore`、`dry_run` 与预算上限、评估器 `json_schema` / `trace` / `python` / `human`
  （含 `human-scores`）、`samples>1`、pass@k 与 flaky 标记、`by_tag`、对比的 bootstrap 置信区间和符号检验、`gate` 接口。
  它们有各自的测试，接口上都是契约的超集；前端 / CLI 可以忽略。
- llm_judge 没有"直接调用 `LLMClient.chat`"，而是把评审配置发布为隐藏 agent（`evalp-judge`）的版本并建 `Session(mode=judge)` 执行
  （BACKLOG 中的"judge 会话"）。好处是评审 trace 可回看、复用限流与重试；代价是库里多了 judge 会话和一个隐藏 agent。
  若要改为直连，只需重写 `evaluators/llm_judge.py::_sample`，对外字段不变。
- `repeats` 缺省值为 3（契约示例写的是 1）；评估器 `key` 缺省时按类型自动生成，契约要求显式给出的写法同样可用。

## CLI / MCP / 工作区客户端（收窄版）— 模块划分与对详设 10、12、13 的取舍

**文件**：`evalp/client.py`（REST 薄客户端）、`evalp/workspace_client.py`（`.evalp.toml`、state、push / pull）、
`evalp/cli/{common,init,workspace,versions,diffview,chat,eval,mcp_cmd}.py`、`evalp/mcp/server.py`。
任务之外**新增**两个 CLI 与 MCP 共用的模块：`evalp/actions.py`（发布、发起 / 等待评测、取报告与对比、单轮对话）和
`evalp/render.py`（把版本 diff / 评测报告 / 对比渲染成 Markdown）。客户端是异步的（`httpx.AsyncClient`），CLI 用 `asyncio.run` 调用。

- **mcp SDK 是 2.x**：`FastMCP` 更名为 `mcp.server.mcpserver.MCPServer`，`mcp.server.fastmcp` 导入会直接报错。`evalp/mcp/server.py` 优先导入
  `MCPServer`，失败再退回 1.x 的 `FastMCP`。工具失败抛 `ToolError`（客户端看到 `isError=true` 和中文原因）。
- **评测的默认值**：评估器配置默认读项目根目录的 `evals.yaml`（`evalp init` 会生成），也可以 `--evaluators` 指定；
  MCP 的 `evaluators` 还可以直接传对象列表。`--repeats` 缺省时不传，由服务端取默认值。（`.evalp.toml` 的 `[eval]` 已在后续补上，见文末"接入层补全"一节。）
- **基线约定**：`eval run` / `run_eval` 增加 `--baseline`。给了 baseline 时它排在第一位（对比的左侧）；没给且有多个版本时以第一个版本为基线；
  每个非基线版本各与基线做一次对比。详设示例 `--versions v13,baseline` 的顺序在这个约定下会把 v13 当基线，请用 `--baseline baseline`。
- **`eval run` 的 `--wait` 默认关闭**（与 13 节的 `[--wait]` 一致）；不带时只打印 run id 和链接。MCP 的 `run_eval` 默认 `wait=true`，最多等 10 分钟，超时不取消评测。
  等待用轮询（`GET /evals/{id}`，CLI 1 秒、MCP 2 秒）而不是 SSE，Run 进入 `paused` 时也结束等待。
- **新增的选项**（均为小增量）：`init --name/--force`（init 还会生成示例数据集 `datasets/regression.jsonl` 和 `evals.yaml`，让 push → eval 的流程能直接跑通）、
  `pull --force`、`chat -m/--message`（单条消息后退出）、`eval run --name`、`evalp mcp --project-dir`（Cursor 等不在项目目录启动 MCP 的客户端使用）。
  所有访问服务的命令都有 `--data-dir`（也读 `EVALP_DATA_DIR`），服务地址取 `.evalp.toml` 的 `[server].url`，没有时取数据目录配置的 host:port。
- **push 的细节**：① 本地 spec、文件哈希都没变，且 `GET /drafts/{id}` 的 revision 与 state 一致时不调用 `/workspace/push`（避免空推送让 revision 空涨），
  `--force` 总是推送；② 只对哈希有变化的文件带 `content`，服务端报 `CONTENT_MISSING`（如数据目录被重置）时自动补传内容重试一次；
  ③ push 成功后调用 `/drafts/{id}/validate` 并展示结果（失败不影响 push）；④ 代码型 agent 的相对 `source.repo` / `subprocess.cwd` 在 push 时转为绝对路径
  （api.md 要求客户端传绝对路径），pull 时位于项目目录内的绝对路径还原为相对路径。
- **`[assets]` 的 `{stem}`** 会规范化为合法资产名（小写，非字母数字转连字符），如 `Query_Order.yaml` → `query-order`。
  精确路径（不含通配符）的映射在本地文件被删除时仍参与 pull，用于还原文件；通配符映射只匹配本地已存在的文件。
- **pull 的保护**：本地文件或 `agent.yaml` 相对上次同步有未推送的修改、且与服务端内容不同时，pull 拒绝覆盖并列出文件，`--force` 才覆盖
  （详设没有这一步；避免 MCP / 误操作丢失本地修改）。pull 重写 `agent.yaml` 时保留注释（见文末"接入层补全"一节）；内容没变则不重写。
- **未做**：chat 只能选已发布的版本（会话基于版本），不能直接和草稿对话（BACKLOG）。其余项（自动拉起 `evalp serve`、其余 MCP 工具与 CLI 命令）已在后续补上。

## 后端改进（前端联调反馈）— SSE 的 `error` 帧改名为 `agent.error`，只在 SSE 输出层映射

`error` 同时是**落库的事件类型**（`events.type`）、`EventType` 枚举和 agent 对外协议（SDK / HTTP / 子进程 Adapter）里的事件类型，
改名会破坏已有数据和外部 agent。所以只改 `GET /sessions/{id}/stream` 帧里的 `event:` 一行（`evalp.api.sse.SSE_EVENT_NAMES`：`error` -> `agent.error`）；
`data` 里的 `type` 仍是 `error`，库、`GET /sessions/{id}?include=events`、Adapter 协议、SDK 都不变，详设 6.2 / 7.4 里的事件类型表仍然成立。
原因：名为 `error` 的 SSE 帧会同时触发浏览器 `EventSource.onerror`，客户端容易误判为断线。
影响：监听 `error` 帧的外部客户端要改为监听 `agent.error`（仓库内只有前端 `SESSION_EVENT_TYPES` 和 CLI `chat`，已同步；CLI 同时兼容旧帧名）。
评测的 SSE（`/evals/{id}/stream`）没有名为 `error` 的帧，不受影响。

## 后端改进（前端联调反馈）— 取消 / 出错的轮次保存已流出的部分文本

详设 6.2 / 7.2 规定 `llm.delta` 不落库。为了让刷新后仍能看到取消 / 出错前已生成的内容，Runner 累积每次 LLM 调用的流出文本（`llm.response` 到达后丢弃，
因为内容已在事件里），轮次以 `cancelled | error | timeout` 结束时：

- `TurnOutput` 增加可选字段 `partial_text`（`runtime/messages.py` 的跨模块契约，只增不减）；`turns.output` 存 `{"messages": [], "partial_text": "…"}`，
  此时 `output` 非 null 但没有 `final`（`TurnOut.output.final` 在 TS 类型里变为可空）。不影响历史拼接（只取 `status=ok` 的轮次）、评测（只读 `final`）和对齐。
- `turn.end` 事件的 payload 多一个额外字段 `partial_text`（与 `error` 一样放在 payload 的额外字段里，未改 `TurnEndPayload`），SSE 回放据此还原。
- 上限 10000 个字符（超出截断并加 `…`），避免 `turn.end` 因体积超限被整个转存为 blob 存根。
- 只保存"尚未被 `llm.response` 取代"的文本：取消发生在工具调用阶段时没有部分文本。外部 agent 只要发了带 `text` 的 `llm.delta` 同样适用。

## 后端改进（前端联调反馈）— 版本 diff 的草稿一侧、`spec_path` 统一为身份键

- `assets[].spec_path`（版本详情）与 `GET /assets/{id}/references` 的 `spec_path` 改为输出身份键路径（`tools[query-order]`），与版本 diff 的 `changes[].path` 一致。
  库里的 `version_asset_refs.spec_path` **仍存下标路径**（`tools[0]`）：校验报错的路径（`validate.errors[].path`）指向草稿里的位置，保持下标；
  这样不需要数据迁移，输出时按版本 Spec 换算（`spec.diff.identity_paths`，与 diff 共用身份键规则）。详设 3.3 注释里的 `tools[2]` 示例指的是库里的存储形式。
- `GET /versions/diff` 的两侧新增草稿引用：`drf_…` 或 `agent名:@草稿名`（详设 4.5 只有版本）。草稿取**已保存**的内容并按发布流程固化 / 规范化，
  涉及草稿时两侧都忽略代码型 Spec 的 `source.git`（发布时才采集）。草稿一侧的摘要为 `{id, draft: true, name, revision, spec_hash}`，没有 `seq`。
- 前端草稿编辑器状态栏的改动数：编辑器内容与已保存草稿一致（`saved`）时用该接口（`summary` 各项之和，口径同版本 diff 页）；
  有**未保存**的修改时服务端看不到，回退为原有的前端计算（`countSpecChanges`，按叶子路径，口径略有差异），界面上显示"约 N 处改动"。
  没有整体替换成接口的原因：编辑器是手动 / 闲置 5 秒保存，用接口会让改动数滞后于输入。

## 后端改进（前端联调反馈）— 大 payload 存根的实际行为写入 api.md（未改存储格式）

详设 3.3 / 6.2 写的是"超过 32KB 的**部分**转存为 blob"，实际实现（`EventSink`）是整个 `payload` 转存、库里只留 `{"$blob": hash}` 加体积很小
（序列化后 ≤ 256 字节）的顶层字段。没有改存储格式（改成字段级需要迁移已有事件，且前端 / 对比逻辑已按整 payload 存根实现），
而是在 `docs/api.md` 第 8 节写明行为和取回方式（`GET /blobs/{hash}` 返回完整原始 payload）。字段级存根仍可作为后续优化。

## 后端改进（前端联调反馈）— 小增量

- `AgentOut` 增加 `tags: list[str]`（该 agent 全部版本标签名，字典序），`GET /agents` 一次查询批量取出；前端 `useAgentTags` 随之删除（`GET /agents/{ref}/tags` 保留）。
- 新增 `PATCH /sessions/{id}`（只能改 `title`：去空白后 1 到 120 个字符，不可清空），返回 `{"session": SessionOut}`；详设 11.3 的会话接口里没有该项。

## 接入层补全 — 其余 MCP 工具 / CLI 命令、`[eval]` 默认配置、MCP 自动拉起服务、pull 保留注释

**新增文件**：`evalp/cli/{asset,dataset,replay,adapter,gc}.py`、`evalp/mcp/autostart.py`、`evalp/yamlio.py`；
`actions.py` / `render.py` / `workspace_client.py` / `client.py` 增加了对应的共享逻辑。仍然全部走 REST API，**没有新增或修改任何服务端接口**（`docs/api.md` 不变）。

- **新增依赖 `ruamel.yaml>=0.18`**（`backend/pyproject.toml`）：仓库原来只有 PyYAML，往返编辑（保留注释 / 键顺序 / 缩进）只能靠 ruamel。
  选择它而不是自己写"按行打补丁"的方案，是因为后者对列表、多行标量、锚点等情况容易写坏用户文件；ruamel 是纯 Python wheel，无需编译。
  保守处理：往返结果会与目标数据逐项比较（`yaml.safe_load` 相等），不一致、原文件无法解析或顶层不是映射时，退化为 PyYAML 整体重写（丢注释但内容正确）。
  只有 spec 与服务端不同、确实需要重写 `agent.yaml` 时才走这条路径；内容没变仍然不重写。缩进风格（序列是否缩进）从原文件推断。
- **`.evalp.toml` 的 `[eval]`**：详设 12.2 列出 `dataset` / `repeats` / `baseline` / `evaluators`；这里另外支持 `versions`（默认评测哪些版本）和 `concurrency`
  （对应 `POST /evals` 的 `concurrency`）。`evaluators` 可以是文件路径（相对项目目录）或 `[[eval.evaluators]]` 表数组。优先级：命令行 / 工具参数 > `[eval]` > 内置默认。
  未知键或类型错误会让读取项目配置的命令直接报错（带 `.evalp.toml` 路径和键名），而不是静默忽略——拼写错误能立刻发现。
  `eval run --dataset` 与 MCP `run_eval.dataset` 因此变成可选（两处都没有时报"未指定数据集"）。`evalp init` 生成的模板默认写入 `dataset` 和 `evaluators`，其余以注释给出。
- **`.evalp.toml` 的 `[mcp] autostart`**（详设没有）：设为 `false` 关闭自动拉起；`evalp mcp --no-autostart` 效果相同（详设 12.1 已有此参数名）。
- **MCP 自动拉起 `evalp serve`**：① 判断"在运行"用的是任意 HTTP 响应（包括 401），因此不需要 token；② 只在 `[server].url` 是本机地址时拉起，远程地址直接报错；
  ③ 子进程为 `python -m evalp serve --host --port [--data-dir]`，stdin 接 /dev/null、stdout/stderr 写数据目录下的 `mcp-autostart.log`（MCP 的 stdout 是协议通道，绝不能被占用）；
  ④ **子进程归拉起它的 MCP 进程所有**：MCP 退出（stdin 关闭、SIGTERM、`atexit`）时先 `terminate` 并等待，超时再用 `procutil.kill_process_tree` 杀整个进程树，最后 `wait()` 回收，不留僵尸；
  ⑤ 每次工具调用前都会确认服务可用（探测很快），所以服务中途被其他 MCP 进程带走也会被重新拉起；⑥ 启动时先尽早 `Popen`（不等待就绪），就绪等待（最多 30 秒）放在第一次工具调用里，避免拖慢 MCP 握手。
  取舍：多个 MCP 进程共享同一个服务时，先退出的那个如果是"拥有者"会把服务一起带走（评测依赖"重启恢复"继续）。没有选择"脱离父进程常驻"，因为那会留下用户不知道的后台进程。
  若 MCP 进程被 `SIGKILL`，子进程不会被清理（无法在进程内处理），下次启动时探测到服务仍在运行会直接复用。
- **MCP 工具**（详设 12.2 其余 10 个，共 19 个）：与详设的差异：
  - `update_draft` 的 `patch` 是对**服务端草稿 spec**（YAML 解析后的对象）做 JSON Merge Patch，`expected_revision` 必填；不改本地文件，返回里提示需要 `pull_draft`。
  - `upsert_asset` / `import_dataset` 的 `path` 相对项目目录解析，且**必须位于项目目录之内**（MCP 的调用方是 LLM，避免被诱导上传任意本地文件）；CLI 的对应命令不受此限。
    非 UTF-8 或超过 1MB 的文件走 `POST /assets/upload`（`blob_upload_id`），其余内联 `content`。`import_dataset` 增加了参数以外的行为：`name` 带 `dataset/` 前缀时自动去掉。
  - `replay_session` 的 `mode` 只接受 `anchored`（后端一期只实现锚定模式），另增 `turns?`、`wait?`（默认等待，最多 10 分钟）、`agent?`；"对比摘要"取自对比组的对齐结果。
    重放完成的判断是"每个被选轮次在新会话一侧都有结果且不是 running"（会话 status 在轮次间隙会回到 idle，不可靠）。
  - `compare_sessions` 每次调用会创建一个 history 对比组（`POST /compare`）；另增 `max_turns?`。`get_session` 的 `detail=full` 只在指定 `turn` 时才取事件并列出 trace 步骤（避免长会话输出爆炸），不指定 `turn` 时摘要最多显示最近 30 轮。
  - `list_agents` / `list_assets` 另增 `limit?`（`list_assets` 的 `kind?`、`q?` 见详设）；`run_eval` 另增 `concurrency?`。
- **CLI 命令**（详设 13 其余项）与详设的差异：
  - `evalp eval gate`：位置参数 `RUN_ID`（与 `report` / `compare` 一致），同时保留详设的 `--run <id>`；另增 `--min-pass-rate`、`--threshold`、`--wait`（先等评测 / 基线结束）、`--timeout`、`--json`（等同 `--format json`）。
    退出码：门禁通过 0，**未通过 1**（详设 13 节约定），用法错误 2；服务端 / 连接错误按全局约定也是 1（错误原因写 stderr，`--json` 时 stdout 只有 JSON）。CI 若要区分"未通过"与"基础设施故障"，请看 stderr 或用 `--json`。
    没有设置基线和 `--min-pass-rate` 时，已成功结束的评测视为通过（这是 `/evals/{id}/gate` 的既有语义），输出里会提示"通过率不参与判定"。
  - `evalp asset ls|show|add|diff`：`add` 的资产参数写 `kind/name`；`show` 把基本信息写 stderr、内容写 stdout（可重定向）；`diff` 默认比较最新修订与上一个，用 `--from/--to` 指定。
    `kind/name` 到 `ast_` id 的解析在客户端完成（`GET /assets?kind&q` 后精确匹配），没有新增服务端接口。
  - `evalp dataset import <file> --name x [--map 字段=列名,…]`：`--map` 用 `input=问题,expected=答案` 的写法（对应请求体的 `column_map`），按扩展名判断格式，可用 `--format` 覆盖。
  - `evalp replay <session> --to v13 [--mode anchored] [--turns 0,2] [--wait]`：不带 `--wait` 时只打印新会话 id 与对比链接（与 `eval run` 一致）。`--to` 写 `vN` / 标签且没有项目时，agent 取自原会话。
  - `evalp adapter check [--version]`：对应 `POST /versions/{ref}/adapter-check`，有失败项时退出码 1；配置型 agent 也能用（会真实调用 LLM）。
  - `evalp eval status <run>`：进度与预计剩余时间；"预计剩余"在客户端按已完成用例的平均速度估算（接口没有 ETA 字段）。
  - `evalp gc`：`POST /system/gc`。
- **未做**：`chat` 直接与草稿对话——会话（`sessions.version_id` 非空、重放 / 评测 / trace 都以版本为锚）不支持草稿，要改需要 schema、Runner 与前端同时调整，超出"极小补丁"，保留在 BACKLOG。

## 对比与重放 — 重放状态与所选轮次成为 compare_groups 的正式列

迁移 `0003` 给 `compare_groups` 加三列：`replay_status`（`running | completed | failed`，带 CHECK）、`replay_turns`（JSON，所选原轮次 idx）、`replay_error`（TEXT）。
详设 3.3 的 `compare_groups` 没有这些列；原因是前端需要稳定的重放进度（取代前端的“2 分钟没进展就算停止”启发式）和所选轮次，不能再塞在 `notes` 里。

- 旧数据：迁移里把 `mode='replay'` 行 `notes` 中的 `replay_turns` 迁到新列，`notes` 清空（若 notes 里还有别的键则保留其余键）；`replay_status` 按已有轮次推断（所选轮次在新会话里都有非 running 的轮次为 `completed`，否则 `failed`，迁移时不可能有重放在后台跑）。降级把 `replay_turns` 写回 `notes`（会覆盖用户备注）。
- 读取时仍兼容旧格式：`replay_turns` 为空但 `notes` 是旧 JSON 时从 notes 读，且该 notes 不作为备注返回。
- `failed` 的定义：轮次没能启动 / 被取消、整个任务出错、服务关闭或重启打断。单轮 agent 报错（`status=error|timeout`）是重放的**结果**，不算失败。库里是 `running` 但进程内没有对应后台任务（重启 / 崩溃）时，API 读出来按 `failed` 返回（不依赖启动时的清理）。
- 状态在 SSE 发 `idle` 之前写入终态。

## 对比与重放 — 重放期间 SSE 不发 idle

`SessionService.hold_stream(session_id)` 返回释放函数：持有期间，该会话的 SSE 即使没有轮次在执行也不发 `idle`、不关闭连接（心跳照常），释放时唤醒等待的流再判断是否空闲。`CompareService.replay` 在创建对比组之前持有、重放任务结束（状态已写终态）后释放。
详设 7.4 的 idle 语义对非重放会话不变。前端去掉了每 1.5s 检查并重连的轮询，只保留“重放仍在进行但连接已 idle 持续 5 秒”时的兜底重连。

## 对比与重放 — 锚定重放轮内并发（SessionService 支持同会话并发轮次）

契约 3 节、详设 9.4 要求锚定重放“各轮并发执行（并发上限 4）”。确认可行性：锚定模式第 k 轮的上下文完全取自**原会话**（`get_history(src, upto_idx=k)`），与新会话里其他轮次无关，所以可以并发；顺序依赖只存在于自由模式。
为此 `SessionService.run_turn` 增加 `concurrent=True`：只允许与同会话中同样 `concurrent` 的轮次并发，与普通轮次仍然互斥（普通对话行为不变，仍是 `SESSION_BUSY`）。改动点：
- `_running` 由“会话 → 一个槽位”改为“会话 → 槽位列表”；`is_running` / `running_turn_id` 语义不变，新增 `running_turn_ids`、`wait_turn`。
- 同一会话的并发轮次**共用一个 `EventSink`**（引用计数，最后一个归还时关闭）：`seq` 单调不重复、只有一份缓冲，事件靠 `turn_id` 区分。若各轮各自一个 sink，seq 会冲突，`last_seq` 水位也会让断线重连漏事件。
- 会话 `status`：并发轮次中最后一个结束才回到 `idle`（`error` 由最后结束的那一轮的结果决定，中途结束的轮次的 error 不单独保留）。
- SSE 的 idle 判定按“还有未结束的轮次”计算（见上一节）。
- 重放内：`Turn` 行按原轮次顺序创建（`start_lock`），之后各轮并发运行；全部重放合计最多同时执行 `REPLAY_CONCURRENCY=4` 个轮次（取代原来的“不同重放之间最多 4 个”）。LLM 的并发仍受各 Provider 的 `max_concurrency` 限制。

## 对比与重放 — 重放自由模式

`mode=free`：新会话用自己的回复续写上下文（`history_override=None`，取新会话 `status=ok` 的轮次），逐轮串行，`anchored_from_turn_id` 为空，`pair_index` 仍是原轮次 idx（用于对齐）。取舍：
- 只重放部分轮次时，上下文只含所选的前几轮（没选的轮次不在新会话里）。
- 某一轮 agent 出错后继续下一轮（出错的轮次不进历史），与锚定模式“单轮失败不影响其他轮”一致；详设没有规定。
- 全局信号量按“轮”获取（不是整个重放占一个名额），避免一个长的自由重放长期占满并发名额。

## 对比与重放 — 单侧发送、备注、alignment 缓存、前端增量对齐

- **单侧发送**：`POST /compare/{id}/turns` 增加 `side`（缺省 `both`，向后兼容）；`left/right` 时 `pair_index`、另一侧 turn id 为 null（`ComparePairAccepted` 的三个字段改为可空）。alignment 增加顶层 `forks`（单侧消息，含 `after_pair` 供排序），`turns` 不变，旧客户端忽略即可。详设 9.3 说“在 UI 中标为单侧消息”，这里由前端按 `forks` 渲染为独占一侧的行。UI 上运行期间两侧输入都被禁用（只要任一侧在跑），比后端更保守。
- **备注/标题编辑**：新增 `PATCH /compare/{id}`；`notes` 列原先被重放元数据占用（见上），现在是真正的用户备注，并在 `CompareGroup` 里返回。
- **alignment 缓存**：进程内 LRU（64 条），键 = 对比模式 + 重放所选轮次 + 两侧会话的 `last_seq` 与各轮 `(id, status, finished_at)`；任一侧有轮次在执行（库里 running 或内存里占着槽位）、或重放未结束时不缓存，且计算前后签名不一致也不缓存。不落库，重启后重算。
- **前端增量对齐**（详设 9.2）：运行中的行用浏览器里根据 SSE 事件算出的对齐（`incrementalAlign.ts`，`difflib.SequenceMatcher` 的最长连续匹配递归在 TS 里重写一份，用后端生成的 42 组用例做一致性测试）；两侧都结束后以服务端结果为准。只覆盖步骤对齐、参数 diff 和 final_text，指标仍取自 SSE 还原出的轮次。
- **会话列表**：API 缺省（`chat,replay`）保持不变以兼容旧客户端；前端“与其他会话对比”选择器改为默认 `mode=chat`，可勾选“包含重放会话”。
