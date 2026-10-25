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

// ───────────────────────────── AgentSpec（4.1 / 4.2） ─────────────────────────────

/** 资产引用：`asset://{kind}/{name}@{latest|rev}`；发布后固化为 `{$asset: …}` */
export interface ResolvedAssetRef {
  $asset: { kind: AssetKind; name: string; rev: number; hash: string }
}

export interface ConfigSpec {
  schema: 'evalp/v1'
  kind: 'config'
  model: {
    provider: string
    name: string
    params?: {
      temperature?: number
      top_p?: number
      max_tokens?: number
      seed?: number
      [k: string]: unknown
    }
  }
  prompts?: {
    system?: string | ResolvedAssetRef
    variables?: Record<string, unknown>
  }
  /** 字符串为资产引用；对象为内置工具（`{builtin, with}`）或固化后的引用 */
  tools?: Array<string | ResolvedAssetRef | { builtin: string; with?: Record<string, unknown> }>
  knowledge?: { inject?: Array<string | ResolvedAssetRef> }
  runtime?: {
    max_steps?: number
    turn_timeout_s?: number
    parallel_tool_calls?: boolean
    tool_timeout_s?: number
  }
}

export interface CodeSpec {
  schema: 'evalp/v1'
  kind: 'code'
  transport: 'http' | 'subprocess'
  http?: { base_url: string; headers?: Record<string, string>; timeout_s?: number }
  subprocess?: { command: string[]; cwd?: string; env?: Record<string, string> }
  source?: { repo?: string; capture_git?: boolean; allow_dirty?: boolean }
  params?: Record<string, unknown>
  runtime?: { turn_timeout_s?: number }
}

export type AgentSpec = ConfigSpec | CodeSpec

// ───────────────────────────── Agent / Draft / Version ─────────────────────────────

export type AgentKind = 'config' | 'code'

export interface Agent {
  id: string
  name: string
  kind: AgentKind
  description: string | null
  archived: boolean
  created_at: ISODateTime
  updated_at: ISODateTime
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

export interface Version {
  id: string
  agent_id: string
  /** 展示为 v{seq} */
  seq: number
  spec_hash: string
  /** 已固化、规范化的 Spec */
  spec: AgentSpec
  source: VersionSource
  parent_version_id: string | null
  from_draft_id: string | null
  notes: string | null
  created_via: CreatedVia
  created_at: ISODateTime
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
  existed: boolean
}

// ───────────────────────────── 资产 ─────────────────────────────

export type AssetKind = 'prompt' | 'tool' | 'knowledge' | 'dataset' | 'file'

export interface Asset {
  id: string
  kind: AssetKind
  /** 小写字母、数字、连字符 */
  name: string
  description: string | null
  latest_rev: number
  /** 软删除时间 */
  deleted_at: ISODateTime | null
  created_at: ISODateTime
}

export interface AssetRevision {
  id: string
  asset_id: string
  rev: number
  blob_hash: string
  /** text/markdown、application/x-yaml、application/jsonl … */
  content_type: string
  size: number
  /** dataset: {case_count, schema_warnings} */
  meta: { case_count?: number; schema_warnings?: string[]; [k: string]: unknown }
  message: string | null
  created_at: ISODateTime
}

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

// ───────────────────────────── Session / Turn ─────────────────────────────

export type SessionMode = 'chat' | 'eval' | 'replay' | 'judge'
export type SessionStatus = 'idle' | 'running' | 'error'
export type ReplayMode = 'anchored' | 'free'

export interface SessionStats {
  tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
  cost?: number
  turns?: number
  [k: string]: unknown
}

export interface Session {
  id: string
  agent_id: string
  version_id: string
  mode: SessionMode
  title: string | null
  /** 重放来源 */
  source_session_id: string | null
  replay_mode: ReplayMode | null
  status: SessionStatus
  last_seq: number
  stats: SessionStats
  created_at: ISODateTime
  updated_at: ISODateTime
}

export type TurnStatus = 'running' | 'ok' | 'error' | 'cancelled' | 'timeout'

export interface Usage {
  prompt_tokens?: number
  completion_tokens?: number
  llm_calls?: number
  tool_calls?: number
}

export interface TurnOutput {
  final: Message
  /** 这一轮新增的全部 assistant 和 tool 消息 */
  messages: Message[]
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
  error: string | null
  started_at: ISODateTime
  finished_at: ISODateTime | null
}

// ───────────────────────────── 事件协议 evalp-event/1（6.2） ─────────────────────────────

/** 事件 payload 超过 32KB 的部分会转存为 blob，此时字段值为 `{ $blob: hash }` */
export interface BlobRef {
  $blob: string
}

export interface TurnStartPayload {
  input: Message
  history_len: number
}
export interface LlmRequestPayload {
  provider: string
  model: string
  params: Record<string, unknown>
  messages: Message[] | BlobRef
  tools: unknown[] | BlobRef
}
export interface ToolCallDelta {
  index?: number
  id?: string
  name?: string
  arguments?: string
}
/** 只推送、不落库、没有 seq */
export interface LlmDeltaPayload {
  step_id: string
  text?: string
  tool_call_delta?: ToolCallDelta
}
export interface LlmResponsePayload {
  content: string | null
  tool_calls: ToolCall[] | null
  finish_reason: string | null
  usage: { prompt_tokens: number; completion_tokens: number }
  latency_ms: number
  ttft_ms: number | null
  cost: number
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
  latency_ms: number
  mocked: boolean
}
export interface SpanStartPayload {
  name: string
  attrs?: Record<string, unknown>
}
export interface SpanEndPayload {
  name: string
  attrs?: Record<string, unknown>
  latency_ms: number
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
export interface TurnEndPayload {
  status: TurnStatus
  output: TurnOutput | null
  usage: Usage
  cost: number
  latency_ms: number
  ttft_ms: number | null
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

/** 会话 SSE 需要监听的全部事件类型（`idle` 由 useEventStream 单独处理） */
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
  'error',
  'turn.end',
] as const satisfies readonly EventType[]

interface EventEnvelopeBase<T extends EventType> {
  /** 平台分配，同一会话内单调递增；llm.delta 没有 seq */
  seq?: number | null
  turn_id: string
  ts: ISODateTime
  type: T
  /** 同一个步骤的开始和结束事件共用同一个 step_id */
  step_id?: string | null
  parent_step_id?: string | null
  payload: EventPayloadMap[T]
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
