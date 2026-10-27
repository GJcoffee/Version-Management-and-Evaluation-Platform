/**
 * 后端实体与协议的 TypeScript 类型。
 * 依据：详设 3.3（表结构）、6.1（消息模型）、6.2（事件协议）、8（评测）、9（对比）、11（REST）。
 *
 * 约定：
 * - 时间字段均为 ISO8601 UTC 字符串；
 * - 数据库中的 JSON 列在 API 中已反序列化为对象；
 * - SQLite 的 0/1 布尔列在 API 中是 boolean（如 archived、passed）；若后端实际返回 0/1，请在 api 层转换。
 */

// ───────────────────────────── 通用 ─────────────────────────────

export type ISODateTime = string

/** 分页响应：`?limit=50&cursor=<上一页最后一条的 id>` */
export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

/** 错误响应：`{"error": {code, message, details}}` */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, unknown> | null
  }
}

/** 详设 11.2 的错误码；前端也会产生 NETWORK_ERROR / HTTP_ERROR（见 client.ts） */
export type ErrorCode =
  | 'VALIDATION_ERROR'
  | 'SPEC_INVALID'
  | 'GIT_DIRTY'
  | 'UNAUTHORIZED'
  | 'NOT_FOUND'
  | 'DRAFT_CONFLICT'
  | 'SESSION_BUSY'
  | 'ASSET_IN_USE'
  | 'ASSET_DELETED'
  | 'AGENT_IN_USE'
  | 'CONTENT_MISSING'
  | 'CONFLICT'
  | 'FORBIDDEN'
  | 'INTERNAL_ERROR'
  | 'INVALID_STATE'
  | 'DATASET_PARSE_ERROR'
  | 'PROVIDER_ERROR'
  | 'AGENT_ERROR'
  | 'TIMEOUT'
  | 'NETWORK_ERROR'
  | 'HTTP_ERROR'

export type CreatedVia = 'ui' | 'cli' | 'mcp' | 'api'

// ───────────────────────────── Provider ─────────────────────────────

export interface ProviderModel {
  name: string
  context_window?: number | null
  /** 输入价格（元 / 百万 token，与后端 pricing 保持一致） */
  price_in?: number | null
  price_out?: number | null
}

/** GET /providers 的元素；密钥永不回传，只给 has_api_key */
export interface ProviderOut {
  id: string
  name: string
  base_url: string
  has_api_key: boolean
  headers: Record<string, string>
  models: ProviderModel[]
  rpm_limit: number | null
  tpm_limit: number | null
  max_concurrency: number
  created_at: ISODateTime
  updated_at: ISODateTime
}
export type Provider = ProviderOut

export interface ProviderCreate {
  name: string
  base_url: string
  api_key?: string
  headers?: Record<string, string>
  models?: ProviderModel[]
  rpm_limit?: number | null
  tpm_limit?: number | null
  max_concurrency?: number
}

/** PATCH：部分更新；`api_key: ""` 表示删除密钥，不传表示不改 */
export type ProviderUpdate = Partial<ProviderCreate>

export interface ProviderTestResult {
  ok: boolean
  latency_ms: number
  models?: string[]
  error?: string
}

// ───────────────────────────── Settings / Health / GC ─────────────────────────────

export interface SettingsValues {
  'scheduler.workers': number
  'scheduler.default_run_concurrency': number
  'scheduler.max_attempts': number
  /** 0 表示永久保留 */
  'retention.eval_sessions_days': number
}
export type SettingKey = keyof SettingsValues

export interface SettingsOut {
  values: SettingsValues
}
export interface SettingsPatch {
  values: Partial<SettingsValues>
}

export interface ProviderHealth {
  name: string
  inflight: number
  /** 未配置限流时后端可能返回 null */
  rpm_left: number | null
  tpm_left: number | null
}

export interface HealthOut {
  ok: boolean
  version: string
  db_size: number
  queue: { pending: number; running: number }
  workers: { busy: number; total: number }
  providers: ProviderHealth[]
}

export interface GcResult {
  blobs_deleted: number
  bytes_freed: number
}

// ───────────────────────────── 消息模型（6.1） ─────────────────────────────

export interface ToolCall {
  id: string
  name: string
  /** 模型返回的 JSON 无法解析时保留原字符串 */
  arguments: Record<string, unknown> | string
}

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool'

export interface Message {
  role: MessageRole
  /** list 预留给多模态 */
  content?: string | Array<Record<string, unknown>> | null
  tool_calls?: ToolCall[] | null
  tool_call_id?: string | null
  name?: string | null
}

// ───────────────────────────── AgentSpec（4.1 / 4.2，依据 docs/api.md §1） ─────────────────────────────

/** 发布后固化的资产引用：`{$asset: {kind, name, rev, hash}}` */
export interface ResolvedAssetRef {
  $asset: { kind: AssetKind; name: string; rev: number; hash: string }
}

/** 草稿中的引用是字符串 `asset://{kind}/{name}@{latest|rev}`；版本中是固化形式 */
export type AssetRefValue = string | ResolvedAssetRef

export interface BuiltinKnowledgeTool {
  builtin: 'knowledge_search'
  with?: { sources?: AssetRefValue[]; top_k?: number }
}

export type ToolEntry = AssetRefValue | BuiltinKnowledgeTool

export interface SpecDisplayFields {
  /** 仅用于显示，不参与哈希，也不存入版本 */
  name?: string | null
  description?: string | null
  notes?: string | null
}

export interface ModelParams {
  temperature?: number | null
  top_p?: number | null
  max_tokens?: number | null
  seed?: number | null
  /** 允许额外字段，原样透传 */
  [k: string]: unknown
}

export interface ConfigSpec extends SpecDisplayFields {
  schema: 'evalp/v1'
  kind: 'config'
  model: { provider: string; name: string; params?: ModelParams }
  prompts?: {
    /** 内联文本或资产引用 */
    system?: AssetRefValue
    variables?: Record<string, unknown>
  }
  tools?: ToolEntry[]
  knowledge?: { inject?: AssetRefValue[] }
  runtime?: {
    max_steps?: number
    turn_timeout_s?: number
    parallel_tool_calls?: boolean
    tool_timeout_s?: number
  }
}

/** 快照入库规则：glob 匹配快照目录内的相对路径，命中的文件作为 kind 类型的资产入库；name 是资产名模板 */
export interface SnapshotRule {
  glob: string
  kind: AssetKind
  /** 变量：{stem}、{stem_nover}（去掉末尾版本号）、{agent}、{path} */
  name: string
}

export interface CodeSpec extends SpecDisplayFields {
  schema: 'evalp/v1'
  kind: 'code'
  transport: 'http' | 'subprocess'
  http?: { base_url: string; headers?: Record<string, string>; timeout_s?: number } | null
  subprocess?: { command: string[]; cwd?: string; env?: Record<string, string> } | null
  source?: {
    repo?: string
    capture_git?: boolean
    allow_dirty?: boolean
    /** 发布时由服务端写入，勿手写 */
    git?: { commit: string; patch_hash?: string | null } | null
  }
  params?: Record<string, unknown>
  runtime?: { turn_timeout_s?: number }
  /** 版本快照：dir 下的文件在 push 时按 map 规则上传为各类型资产（未命中的存为 file），files 由 push 填充；只做记录，不参与运行 */
  snapshot?: { dir: string; map?: SnapshotRule[]; files?: Record<string, AssetRefValue> } | null
}

export type AgentSpec = ConfigSpec | CodeSpec

/** `GET /spec/schema`：JSON Schema（oneOf + discriminator），仅用到最小子集 */
export interface JsonSchemaNode {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  const?: unknown
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  minLength?: number
  properties?: Record<string, JsonSchemaNode>
  required?: string[]
  items?: JsonSchemaNode
  additionalProperties?: boolean | JsonSchemaNode
  anyOf?: JsonSchemaNode[]
  oneOf?: JsonSchemaNode[]
  $ref?: string
  readOnly?: boolean
}
export interface SpecSchema extends JsonSchemaNode {
  $defs: Record<string, JsonSchemaNode>
  discriminator?: { propertyName: string; mapping: Record<string, string> }
}

/** `POST /drafts/{id}/validate` */
export interface SpecIssue {
  /** 点号路径，数组下标可写成 `tools[0]` 或 `tools.0` */
  path: string
  message: string
}
export interface ValidateResult {
  ok: boolean
  errors: SpecIssue[]
  warnings: SpecIssue[]
  /** 通过时为固化后的规范化 Spec（即发布时会存储的内容） */
  resolved_preview: AgentSpec | null
}

// ───────────────────────────── Agent / Draft / Version（docs/api.md §2-4） ─────────────────────────────

export type AgentKind = 'config' | 'code'

export interface AgentLatestVersion {
  id: string
  seq: number
  spec_hash: string
  created_at: ISODateTime
}

/** agent 的发布 / 推送规范：由服务端对所有入口（CLI、MCP、网页、HTTP API）一致地强制 */
export interface Conventions {
  /** params 里的参数名（如 hiagent_version）：发布必须带标签 v{params.<参数>}，且标签不能已存在 */
  tag_from_param: string | null
  /** 发布说明必填 */
  require_notes: boolean
  /** 快照 map 中这些类型的规则必须配置 id */
  require_source_id: AssetKind[]
  /** 来自快照的资产只允许快照推送写入 */
  snapshot_readonly: boolean
}

export interface Agent {
  id: string
  name: string
  kind: AgentKind
  description: string | null
  archived: boolean
  created_at: ISODateTime
  updated_at: ISODateTime
  latest_version: AgentLatestVersion | null
  /** 该 agent 全部版本标签的名称（按字典序） */
  tags: string[]
  conventions: Conventions
}

export interface AgentCreate {
  name: string
  kind: AgentKind
  description?: string
  spec?: Partial<AgentSpec>
}

export interface AgentUpdate {
  name?: string
  description?: string
  archived?: boolean
  /** 整体替换 */
  conventions?: Conventions
}

export interface Draft {
  id: string
  agent_id: string
  name: string
  spec: AgentSpec
  base_version_id: string | null
  /** 乐观锁，每次保存 +1；写草稿必须带 expected_revision */
  revision: number
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface VersionSource {
  git_commit?: string
  git_branch?: string
  dirty?: boolean
  patch_blob?: string
}

/** 版本列表条目：没有 spec 和 assets */
export interface VersionSummary {
  id: string
  agent_id: string
  /** 展示为 v{seq} */
  seq: number
  spec_hash: string
  source: VersionSource
  parent_version_id: string | null
  from_draft_id: string | null
  notes: string | null
  created_via: CreatedVia
  created_at: ISODateTime
  tags: string[]
}

export interface VersionAssetRef {
  spec_path: string
  kind: AssetKind
  name: string
  rev: number
  asset_revision_id: string
}

/** `GET /versions/{ref}/assets` 的一项：版本绑定的资产 */
export interface VersionAssetItem {
  asset_id: string
  kind: AssetKind
  name: string
  /** 来源平台上的原始名称（可含中文） */
  display_name: string | null
  /** 来源平台上的稳定 ID */
  source_id: string | null
  rev: number
  asset_revision_id: string
  /** Spec 中引用它的位置（身份键路径） */
  spec_paths: string[]
  /** 'snapshot'：来自 agent 快照（只读） */
  origin: 'snapshot' | null
  /** 该修订最近一次变化发生在哪个版本（连续使用同一修订的最早版本） */
  changed_in_seq: number
  changed_in_version_id: string
  /** 变化前的修订号；该版本首次出现这个资产时为 null */
  previous_rev: number | null
}

export interface VersionAssets {
  version: VersionSummary
  items: VersionAssetItem[]
}

/** `GET /versions/{ref}` 与发布结果里的 version */
export interface Version extends VersionSummary {
  /** 已固化、规范化的 Spec */
  spec: AgentSpec
  assets: VersionAssetRef[]
}

/** 标签可以移动到其他版本 */
export interface VersionTag {
  agent_id: string
  tag: string
  version_id: string
  updated_at: ISODateTime
}

export interface PublishResult {
  version: Version
  /** spec_hash 已存在：返回已有版本，没有创建新版本 */
  existed: boolean
}

export interface PublishRequest {
  expected_revision: number
  notes?: string
  /** 与创建版本原子完成；agent 开启 tag_from_param 规范时必填且必须等于 v{params.<参数>} */
  tag?: string
  via?: CreatedVia
}

export type DiffOp = 'add' | 'remove' | 'replace' | 'asset_changed'

export interface ContentDiff {
  format: 'unified'
  /** 从 `@@` 开始，不含文件头 */
  text: string
}

export interface SpecChange {
  /**
   * 点号连接；列表元素按身份键：`tools[query-order]`、`knowledge.inject[faq]`，其余按下标 `a[1]`；
   * 快照里的资产按 `snapshot.files[类型/名称]`（如 `snapshot.files[skill/crawl-engine]`）。
   */
  path: string
  op: DiffOp
  /** asset_changed 时给出资产的类型与名称（add / remove 的 left / right 里有 kind、name、rev）；改过名的有 renamed_from（旧名） */
  asset?: { kind: AssetKind; name: string; renamed_from?: string }
  left?: unknown
  right?: unknown
  content_diff?: ContentDiff
}

/** diff 的一侧：版本，或草稿（`draft: true`，没有 seq，有 name / revision） */
export interface VersionDiffSide {
  id: string
  spec_hash: string
  seq?: number
  draft?: boolean
  name?: string
  revision?: number
}

export interface VersionDiff {
  left: VersionDiffSide
  right: VersionDiffSide
  changes: SpecChange[]
  summary: Record<DiffOp, number>
}

// ───────────────────────────── 资产（docs/api.md §5-6） ─────────────────────────────

export type AssetKind =
  | 'prompt'
  | 'tool'
  | 'knowledge'
  | 'kb_config'
  | 'skill'
  | 'mcp'
  | 'database'
  | 'app_config'
  | 'dataset'
  | 'file'

export interface AssetRevision {
  id: string
  asset_id: string
  rev: number
  blob_hash: string
  /** text/markdown、application/x-yaml、application/jsonl … */
  content_type: string
  size: number
  /**
   * prompt: {variables}；tool: {tool_name, impl_type}；dataset: {case_count, schema_warnings, format, column_map}。
   * `origin: 'snapshot'`：来自 agent 快照（只读），由 push 写入。
   */
  meta: { case_count?: number; schema_warnings?: unknown[]; origin?: 'snapshot'; [k: string]: unknown }
  message: string | null
  created_at: ISODateTime
}

/** `GET /assets/revisions/{id}` 与 `GET /assets/{id}/revisions/{rev}`：带内容 */
export interface AssetRevisionDetail extends AssetRevision {
  kind: AssetKind
  name: string
  /** 非 UTF-8 或大于 5MB 时为 null */
  content: string | null
  content_available: boolean
}

export interface Asset {
  id: string
  kind: AssetKind
  /** 小写字母、数字、连字符 */
  name: string
  /** 来源平台上的原始名称（可含中文），仅用于展示 */
  display_name: string | null
  /** 来源平台上的稳定 ID（快照入库的资产才有） */
  source_id: string | null
  /** 改名前用过的资产名 */
  former_names: string[]
  description: string | null
  /** 当前生效的修订号（不一定等于最大修订号） */
  latest_rev: number
  /** 软删除时间 */
  deleted_at: ISODateTime | null
  created_at: ISODateTime
  latest: AssetRevision | null
}

export interface AssetCreate {
  kind: AssetKind
  name: string
  description?: string
  content?: string
  blob_upload_id?: string
  content_type?: string
  message?: string
}

export interface AssetRevisionCreate {
  content?: string
  blob_upload_id?: string
  content_type?: string
  message?: string
  column_map?: Record<string, string | string[]>
}

export interface AssetWriteResult {
  asset: Asset
  revision: AssetRevision
  /** 内容与已有修订相同，没有新建修订 */
  existed: boolean
  created: boolean
}

export interface AssetDeleteResult {
  deleted: 'soft' | 'hard'
  id: string
  versions?: number
  evals?: number
}

export interface AssetReferences {
  asset: Asset
  versions: Array<{
    version_id: string
    agent_id: string
    agent_name: string
    seq: number
    rev: number
    revision_id: string
    spec_path: string
  }>
  evals: Array<{
    run_id: string
    batch_id: string
    status: string
    agent_id: string
    version_id: string
    dataset_rev_id: string
    rev: number
  }>
  drafts: Array<{ draft_id: string; draft_name: string; agent_id: string; agent_name: string }>
  in_use: boolean
}

export interface DatasetCaseBrief {
  case_key: string
  input: { turns: string[] }
  expected?: string | null
  tags: string[]
}

interface AssetDiffBase {
  kind: AssetKind
  identical: boolean
  left: AssetRevisionDetail | AssetRevision
  right: AssetRevisionDetail | AssetRevision
}
export interface TextAssetDiff extends AssetDiffBase {
  type: 'text'
  content_diff: ContentDiff | null
}
export interface BinaryAssetDiff extends AssetDiffBase {
  type: 'binary'
  content_diff: null
  sizes: { left: number; right: number }
}
export interface DatasetAssetDiff extends AssetDiffBase {
  type: 'dataset'
  summary: { added: number; removed: number; modified: number; unchanged: number; left_count: number; right_count: number }
  added: DatasetCaseBrief[]
  removed: DatasetCaseBrief[]
  modified: Array<{
    case_key: string
    changed_fields: Array<'input' | 'expected' | 'rubric' | 'tags' | 'metadata'>
    left: Record<string, unknown>
    right: Record<string, unknown>
  }>
  truncated: boolean
}
export type AssetDiff = TextAssetDiff | BinaryAssetDiff | DatasetAssetDiff

/** 数据集修订解析后的用例 */
export interface EvalCase {
  id: string
  dataset_rev_id: string
  case_key: string
  idx: number
  /** 统一为多轮格式 */
  input: { turns: string[] }
  expected: string | null
  rubric: string | null
  tags: string[]
  /** 可含 tool_mocks / variables */
  metadata: { variables?: Record<string, unknown>; tool_mocks?: Record<string, unknown>; [k: string]: unknown }
  case_hash: string
}

export type DatasetFormat = 'jsonl' | 'csv'

export interface DatasetImportRequest {
  name: string
  format: DatasetFormat
  content: string
  description?: string
  message?: string
  /** CSV：`{字段名: 列名}`；input 可为列名数组（多轮） */
  column_map?: Record<string, string | string[]>
}

export interface DatasetWarning {
  code: string
  message: string
  count?: number
  lines?: number[]
}

export interface DatasetImportResult {
  asset: Asset
  revision: AssetRevision
  case_count: number
  warnings: DatasetWarning[]
  existed: boolean
  created: boolean
}

// ───────────────────────────── Session / Turn（docs/api.md §8） ─────────────────────────────

export type SessionMode = 'chat' | 'eval' | 'replay' | 'judge'
export type SessionStatus = 'idle' | 'running' | 'error'
export type ReplayMode = 'anchored' | 'free'

/** 会话累计指标 */
export interface SessionStats {
  turns?: number
  prompt_tokens?: number
  completion_tokens?: number
  llm_calls?: number
  tool_calls?: number
  cost?: number
  [k: string]: unknown
}

export interface Session {
  id: string
  agent_id: string
  agent_name: string | null
  version_id: string
  version_seq: number | null
  mode: SessionMode
  title: string | null
  /** 重放来源 */
  source_session_id: string | null
  replay_mode: ReplayMode | null
  status: SessionStatus
  last_seq: number
  stats: SessionStats
  /** 会话级模板变量（优先级：Spec < 会话 < 单轮） */
  variables: Record<string, unknown>
  created_at: ISODateTime
  updated_at: ISODateTime
}

/** POST /sessions：`version_id` 与 `agent`（+ `tag`）二选一 */
export interface SessionCreate {
  version_id?: string
  agent?: string
  /** 标签、v12 或 latest（缺省 latest） */
  tag?: string
  title?: string
  variables?: Record<string, unknown>
}

export interface SessionListParams {
  /** agent 的 id 或名称 */
  agent_id?: string
  version_id?: string
  /** 缺省为 chat,replay（不含 eval / judge） */
  mode?: string
  q?: string
  limit?: number
}

export type TurnStatus = 'running' | 'ok' | 'error' | 'cancelled' | 'timeout'

export interface Usage {
  prompt_tokens?: number
  completion_tokens?: number
  llm_calls?: number
  tool_calls?: number
}

export interface TurnOutput {
  /** 最终回复；取消 / 出错的轮次没有 */
  final: Message | null
  /** 这一轮新增的全部 assistant 和 tool 消息 */
  messages: Message[]
  /** 取消 / 出错 / 超时的轮次：已流出的部分文本（不属于对话历史） */
  partial_text?: string | null
}

/** 轮次错误；agent 报错、超时都以 200 + turn.status 返回，原因在这里 */
export interface TurnError {
  code: string
  message: string
  retriable: boolean
}

export interface Turn {
  id: string
  session_id: string
  idx: number
  /** 双栏对比时两侧轮次共享 */
  pair_index: number | null
  status: TurnStatus
  input: Message
  output: TurnOutput | null
  usage: Usage
  cost: number
  latency_ms: number | null
  ttft_ms: number | null
  error: TurnError | null
  anchored_from_turn_id: string | null
  started_at: ISODateTime
  finished_at: ISODateTime | null
}

export interface SessionDetail {
  session: Session
  turns: Turn[]
  events: EventEnvelope[] | null
}

/** POST /sessions/{id}/turns 的请求体 */
export interface TurnCreate {
  input: string | Message
  /** 单轮变量，覆盖会话变量 */
  variables?: Record<string, unknown>
  tool_mocks?: Record<string, unknown>
  pair_index?: number | null
  wait?: boolean
}

/** 默认 202：轮次已在后台执行，用 stream_url 订阅过程 */
export interface TurnAccepted {
  turn_id: string
  stream_url: string
  turn: Turn
}

// ───────────────────────────── 事件协议 evalp-event/1（6.2） ─────────────────────────────

/**
 * 大 payload（> events.payload_inline_limit_kb）转存为 blob：库里（也就是回放时）的整个 payload 被换成
 * `{ $blob: hash, ...体积很小的顶层字段 }`；`GET /blobs/{hash}` 返回完整的 payload。
 * 实时推送的事件仍是完整内容。
 */
export interface BlobRef {
  $blob: string
}

export interface TurnStartPayload {
  input: Message
  history_len: number
}
export interface LlmRequestPayload {
  provider: string | null
  model: string
  params: Record<string, unknown>
  messages: Message[] | BlobRef
  tools: unknown[] | BlobRef | null
}
/** `{index, id?, name?, arguments?}`：name / arguments 是需要按 index 拼接的片段 */
export interface ToolCallDelta {
  index?: number
  id?: string
  name?: string
  arguments?: string
}
/** 只推送、不落库、没有 seq */
export interface LlmDeltaPayload {
  step_id?: string | null
  text?: string
  tool_call_delta?: ToolCallDelta
}
export interface LlmResponsePayload {
  content: string | null
  tool_calls: ToolCall[] | null
  finish_reason: string | null
  usage: { prompt_tokens: number; completion_tokens: number; estimated?: boolean } | null
  latency_ms: number | null
  ttft_ms: number | null
  cost: number | null
}
export interface ToolCallPayload {
  call_id: string
  name: string
  arguments: Record<string, unknown> | string
}
export interface ToolResultPayload {
  call_id: string
  name: string
  output: unknown
  is_error: boolean
  latency_ms: number | null
  mocked: boolean
}
export interface SpanStartPayload {
  name: string
  attrs?: Record<string, unknown>
}
export interface SpanEndPayload {
  name: string
  attrs?: Record<string, unknown>
  latency_ms: number | null
}
export type LogLevel = 'debug' | 'info' | 'warning' | 'error'
export interface LogPayload {
  level: LogLevel | string
  message: string
}
export interface MessageAssistantPayload {
  message: Message
}
export interface ErrorPayload {
  code: string
  message: string
  retriable: boolean
}
/** 事件里的轮次状态：completed 对应 Turn.status 的 ok */
export type TurnEndStatus = 'completed' | 'error' | 'timeout' | 'cancelled'
export interface TurnEndPayload {
  status: TurnEndStatus
  output: TurnOutput | null
  usage: Usage | null
  cost: number | null
  latency_ms: number | null
  ttft_ms: number | null
  /** 取消 / 出错 / 超时时，已流出但没来得及成为 llm.response 的文本（过长时被截断） */
  partial_text?: string | null
}

export interface EventPayloadMap {
  'turn.start': TurnStartPayload
  'llm.request': LlmRequestPayload
  'llm.delta': LlmDeltaPayload
  'llm.response': LlmResponsePayload
  'tool.call': ToolCallPayload
  'tool.result': ToolResultPayload
  'span.start': SpanStartPayload
  'span.end': SpanEndPayload
  log: LogPayload
  'message.assistant': MessageAssistantPayload
  error: ErrorPayload
  'turn.end': TurnEndPayload
}

export type EventType = keyof EventPayloadMap

/**
 * 会话 SSE 需要监听的全部帧名（`idle` 由 useEventStream 单独处理）。
 * 与 `EventType` 的区别：业务事件 `error` 在 SSE 线上叫 `agent.error`（避开 `EventSource.onerror` 的同名事件，
 * 避免被误判为断线）；帧里 `data.type` 仍是 `error`，所以 reducer 看到的事件类型不变。
 */
export const SESSION_EVENT_TYPES = [
  'turn.start',
  'llm.request',
  'llm.delta',
  'llm.response',
  'tool.call',
  'tool.result',
  'span.start',
  'span.end',
  'log',
  'message.assistant',
  'agent.error',
  'turn.end',
] as const

interface EventEnvelopeBase<T extends EventType> {
  /** 平台分配，同一会话内单调递增；llm.delta 没有 seq */
  seq?: number | null
  turn_id?: string | null
  ts: ISODateTime
  type: T
  /** 同一个步骤的开始和结束事件共用同一个 step_id */
  step_id?: string | null
  parent_step_id?: string | null
  /** 转存为 blob 时只剩 `$blob` 和小字段，见 BlobRef */
  payload: EventPayloadMap[T] & Partial<BlobRef>
}

/** 事件外层结构，按 type 区分 payload（可判别联合） */
export type EventEnvelope = { [T in EventType]: EventEnvelopeBase<T> }[EventType]
export type EventEnvelopeOf<T extends EventType> = Extract<EventEnvelope, { type: T }>

// ───────────────────────────── 评估器配置（8.5） ─────────────────────────────

interface EvaluatorBase {
  /** 评估器键，同一 Run 内唯一 */
  key: string
  /** 是否参与 Item 通过判定；默认 true，human 默认 false */
  gate?: boolean
}
export interface ExactMatchEvaluator extends EvaluatorBase {
  type: 'exact_match'
  field?: string
  normalize?: Array<'strip' | 'lower' | 'nfkc' | 'punct'>
}
export interface ContainsEvaluator extends EvaluatorBase {
  type: 'contains'
  values?: string[]
  mode?: 'all' | 'any'
  negate?: boolean
}
export interface RegexEvaluator extends EvaluatorBase {
  type: 'regex'
  pattern: string
  flags?: string
}
export interface JsonSchemaEvaluator extends EvaluatorBase {
  type: 'json_schema'
  schema: Record<string, unknown>
  extract?: 'auto' | 'codeblock'
}
export interface TraceEvaluator extends EvaluatorBase {
  type: 'trace'
  assert: {
    tool_called?: string[]
    tool_not_called?: string[]
    max_steps?: number
    max_latency_ms?: number
    max_tokens?: number
  }
}
export interface LlmJudgeEvaluator extends EvaluatorBase {
  type: 'llm_judge'
  provider: string
  model: string
  /** prompt 资产引用 */
  prompt: string
  scale?: [number, number]
  pass_threshold?: number
  samples?: number
}
export interface PythonEvaluator extends EvaluatorBase {
  type: 'python'
  entry: string
  timeout_s?: number
}
export interface HumanEvaluator extends EvaluatorBase {
  type: 'human'
  scale?: [number, number]
  guideline?: string
}
export type EvaluatorConfig =
  | ExactMatchEvaluator
  | ContainsEvaluator
  | RegexEvaluator
  | JsonSchemaEvaluator
  | TraceEvaluator
  | LlmJudgeEvaluator
  | PythonEvaluator
  | HumanEvaluator
export type EvaluatorType = EvaluatorConfig['type']

// ───────────────────────────── 评测（8） ─────────────────────────────

export interface EvalBatch {
  id: string
  name: string | null
  created_via: CreatedVia
  created_at: ISODateTime
}

export type EvalRunStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'completed_with_errors'
  | 'cancelled'

export type EvalItemStatus = 'pending' | 'running' | 'scoring' | 'done' | 'failed' | 'cancelled'

export type CachePolicy = 'reuse' | 'refresh'

export interface EvalProgress {
  total?: number
  done?: number
  failed?: number
  running?: number
  cached?: number
}

export interface EvalRunStats {
  cases: number
  items: number
  done: number
  failed: number
  cached: number
  /** 各用例通过率的平均值 */
  pass_rate: number
  pass_at_k: Record<string, number>
  /** 0 < 用例通过率 < 1 */
  flaky_cases: number
  evaluators: Record<string, { mean: number | null; std: number | null; errors: number }>
  latency_ms: { p50: number; p95: number }
  tokens: { prompt: number; completion: number }
  cost: number
  error_rate: number
  by_tag: Record<string, { pass_rate: number; cases: number }>
}

export interface EvalRun {
  id: string
  batch_id: string
  agent_id: string
  version_id: string
  dataset_rev_id: string
  case_filter: { tags?: string[]; case_keys?: string[] }
  evaluators: EvaluatorConfig[]
  evaluators_hash: string
  repeats: number
  concurrency: number
  seed: number | null
  cache_policy: CachePolicy
  budget: { max_cost?: number; max_tokens?: number }
  status: EvalRunStatus
  progress: EvalProgress
  stats: EvalRunStats | null
  error: string | null
  created_at: ISODateTime
  started_at: ISODateTime | null
  finished_at: ISODateTime | null
}

export interface EvalItem {
  id: string
  run_id: string
  case_id: string
  repeat_idx: number
  cache_key: string
  status: EvalItemStatus
  session_id: string | null
  reused_from_item_id: string | null
  attempts: number
  error: string | null
  latency_ms: number | null
  usage: Usage | null
  cost: number | null
  /** 门禁评估器是否全部通过 */
  passed: boolean | null
  started_at: ISODateTime | null
  finished_at: ISODateTime | null
}

export type EvalScoreStatus = 'done' | 'error' | 'pending_human'

export interface EvalScore {
  id: string
  item_id: string
  evaluator_key: string
  /** 单个评估器配置的哈希，用于仅重新评分 */
  evaluator_hash: string
  status: EvalScoreStatus
  /** 归一化到 [0,1] */
  score: number | null
  raw_score: number | null
  passed: boolean | null
  reason: string | null
  detail: Record<string, unknown> | null
  judge_session_id: string | null
  annotator: string | null
  created_at: ISODateTime
}

/** 评测 SSE（`GET /evals/{id}/stream`）事件名；payload 结构以后端实现为准，这里按最小假设 */
export const EVAL_EVENT_TYPES = [
  'eval.progress',
  'eval.item_done',
  'eval.status',
  'eval.budget_exceeded',
] as const
export type EvalEventType = (typeof EVAL_EVENT_TYPES)[number]

// ───────────────────────────── 对比（9） ─────────────────────────────

export type CompareKind = 'session' | 'eval' | 'version'
export type CompareMode = 'live' | 'history' | 'replay'

export interface CompareGroup {
  id: string
  kind: CompareKind
  mode: CompareMode | null
  /** live/history 模式下保存会话 id；eval/version 保存对应 id（具体结构以后端为准） */
  left_ref: string
  right_ref: string
  title: string | null
  notes: string | null
  created_at: ISODateTime
}
