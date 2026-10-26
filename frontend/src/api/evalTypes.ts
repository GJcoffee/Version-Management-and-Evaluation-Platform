/**
 * 评测接口的类型（一期收窄版，见 docs/contract-eval.md 与 docs/api.md「评测（P3-B）」）。
 * 与 types.ts 里早期的设计稿类型（EvalRun / EvalItem）区分开，这里一律以 `Out` 结尾，按实际接口定义。
 */
import type { ISODateTime } from './types'

export type EvalRunStatusOut =
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'completed_with_errors'
  | 'cancelled'
  | 'failed'

export type EvalItemStatusOut = 'pending' | 'running' | 'scoring' | 'done' | 'failed' | 'cancelled'

/** 终态：到达后 SSE 会发 idle 并关闭 */
export const EVAL_TERMINAL_STATUSES: readonly EvalRunStatusOut[] = [
  'completed',
  'completed_with_errors',
  'cancelled',
  'failed',
]
/** 仍在进行（需要轮询 / 订阅） */
export const EVAL_ACTIVE_STATUSES: readonly EvalRunStatusOut[] = ['queued', 'running', 'paused']

export interface EvalProgressOut {
  /** 暂停原因：manual（手动）| budget（超出预算自动暂停） */
  pause_reason?: string | null
  total: number
  pending?: number
  running?: number
  scoring?: number
  done: number
  failed: number
  cancelled?: number
  cached?: number
  cost_new?: number
}

export interface EvalEvaluatorStat {
  mean: number | null
  std?: number | null
  pass_rate?: number | null
  errors?: number
  n?: number
  parse_failures?: number
  /** 等待人工打分的评分数（human 评估器） */
  pending_human?: number
}

export interface EvalStatsOut {
  cases: number
  items: number
  done: number
  failed: number
  cached: number
  pass_rate: number | null
  evaluators: Record<string, EvalEvaluatorStat>
  latency_ms: { p50: number | null; p95: number | null }
  tokens: { prompt: number; completion: number }
  cost: number
  error_rate: number
  cancelled?: number
  /** 本次真正新增的成本（不含缓存命中），预算按它计算 */
  cost_new?: number
  /** 失败尝试浪费的成本 */
  cost_wasted?: number
  /** pass@k：键为 k（"1" "3" …），只含 repeats >= k 的项 */
  pass_at_k?: Record<string, number>
  /** 不稳定用例数：0 < 用例通过率 < 1 */
  flaky_cases?: number
  /** 评分出错的比例 */
  score_error_rate?: number
  by_tag?: Record<string, { pass_rate: number | null; cases: number }>
}

/** 评估器配置（创建时的请求体 / 详情里返回的配置） */
export type EvaluatorInput =
  | { key: string; type: 'exact_match'; gate?: boolean; values?: string[]; normalize?: string[]; field?: string }
  | {
      key: string
      type: 'contains'
      gate?: boolean
      values?: string[]
      mode?: 'all' | 'any'
      negate?: boolean
      case_sensitive?: boolean
    }
  | { key: string; type: 'regex'; gate?: boolean; pattern: string; flags?: string }
  | { key: string; type: 'json_schema'; gate?: boolean; schema: Record<string, unknown>; extract?: 'auto' | 'codeblock' }
  | { key: string; type: 'trace'; gate?: boolean; assert: TraceAssertInput }
  | { key: string; type: 'python'; gate?: boolean; entry: string; timeout_s?: number; python?: string; workdir?: string }
  | {
      key: string
      type: 'llm_judge'
      gate?: boolean
      provider: string
      model: string
      prompt: string
      scale?: [number, number]
      pass_threshold?: number
      /** 多次采样取平均，1–10 */
      samples?: number
      params?: Record<string, unknown>
    }
  | { key: string; type: 'human'; gate?: boolean; scale?: [number, number]; guideline?: string; pass_threshold?: number }

export interface TraceAssertInput {
  tool_called?: string[]
  tool_not_called?: string[]
  max_steps?: number
  max_latency_ms?: number
  max_tokens?: number
}

/** 详情里的评估器配置：类型可能多于一期支持的四种 */
export interface EvaluatorOut {
  key: string
  type: string
  gate?: boolean
  [k: string]: unknown
}

export interface EvalBudget {
  max_cost?: number | null
  max_tokens?: number | null
}

export interface EvalRunOut {
  id: string
  batch_id: string
  name: string | null
  agent_id: string
  agent_name: string | null
  version_id: string
  version_seq: number | null
  dataset_rev_id: string | null
  dataset_name: string | null
  dataset_rev: number | null
  case_filter: { tags?: string[]; case_keys?: string[] }
  evaluators: EvaluatorOut[]
  repeats: number
  concurrency: number
  cache_policy: string
  /** 预算上限：max_cost（元）、max_tokens；都是本次新增消耗的上限 */
  budget?: EvalBudget
  status: EvalRunStatusOut
  /** 暂停原因（仅 paused 时有值） */
  pause_reason?: string | null
  progress: EvalProgressOut
  stats: EvalStatsOut | null
  error: string | null
  created_at: ISODateTime
  started_at: ISODateTime | null
  finished_at: ISODateTime | null
}

export interface EvalBatchRun {
  id: string
  version_id: string
  status: EvalRunStatusOut
}

export interface EvalRunDetailOut extends EvalRunOut {
  batch_runs: EvalBatchRun[]
}

export interface EvalScoreOut {
  evaluator_key: string
  evaluator_type?: string | null
  gate?: boolean | null
  status: 'done' | 'error' | 'pending_human'
  score: number | null
  raw_score: number | null
  passed: boolean | null
  reason: string | null
  detail?: unknown
  judge_session_id?: string | null
  /** 人工评审人 */
  annotator?: string | null
  /** 评估器配置已变化、尚未重新评分 */
  stale?: boolean
}

export interface EvalItemError {
  code?: string
  message?: string
  retriable?: boolean
}

export interface EvalItemOut {
  id: string
  run_id: string
  case_id: string
  case_key: string
  repeat_idx: number
  input_preview: string | null
  output_preview: string | null
  expected: string | null
  tags: string[]
  status: EvalItemStatusOut
  passed: boolean | null
  /** 门禁评估器分数的平均值；执行失败为 0 */
  score: number | null
  cached: boolean
  attempts: number
  error: EvalItemError | string | null
  latency_ms: number | null
  cost: number | null
  usage: { prompt_tokens?: number; completion_tokens?: number } | null
  session_id: string | null
  scores: EvalScoreOut[]
}

export interface EvalTurnOut {
  idx: number
  input: unknown
  output: unknown
  status: string
}

export interface EvalItemDetailOut extends EvalItemOut {
  output: string | null
  turns: EvalTurnOut[]
  case_detail: {
    case_key?: string
    input?: { turns?: string[] }
    expected?: string | null
    rubric?: string | null
    tags?: string[]
    [k: string]: unknown
  }
}

export interface EvalCreateRequest {
  name?: string
  version_ids: string[]
  dataset: string
  case_filter?: { tags?: string[]; case_keys?: string[] }
  evaluators: EvaluatorInput[]
  repeats: number
  concurrency: number
  cache_policy?: 'reuse' | 'refresh'
  budget?: EvalBudget
  via?: 'ui'
}

/** 成本预估（创建响应与 dry_run 都带） */
export interface EvalEstimate {
  items: number
  cached_items: number
  /** 扣除缓存命中后预计新增的 tokens / 成本 */
  tokens: number
  cost: number
  gross_tokens: number
  gross_cost: number
  /** false 表示模型没有配置价格，成本按 0 计，仅供参考 */
  cost_known: boolean
  /** history（历史评测均值）| heuristic（粗略估算）| mixed */
  basis: 'history' | 'heuristic' | 'mixed'
  /** 设置了预算时，预估是否超出；未设置预算为 null */
  exceeds_budget: boolean | null
  per_version: Array<{
    version_id: string
    items: number
    cached_items: number
    tokens: number
    cost: number
    basis: string
  }>
}

export interface EvalCreated {
  batch_id: string | null
  runs: Array<{ id: string; version_id: string; status: EvalRunStatusOut }>
  dry_run?: boolean
  estimate?: EvalEstimate | null
}

/** POST /evals/{id}/rescore 的响应 */
export interface EvalRescoreResult {
  rescored_items: number
  /** 评估器配置（evaluator_hash）发生变化、被重新计算的评估器 key */
  evaluators_changed: string[]
  rebased: boolean
}

export interface EvalHumanScoreEntry {
  item_id: string
  evaluator_key: string
  raw_score: number
  reason?: string | null
  annotator?: string | null
}

/** GET /evals/{id}/gate 的响应 */
export interface EvalGateResult {
  passed: boolean
  reasons: string[]
  summary: {
    run_id: string
    status: EvalRunStatusOut
    pass_rate: number | null
    failed_items?: number | null
    baseline_run_id?: string | null
    max_regressions: number
    min_pass_rate: number | null
    baseline_status?: EvalRunStatusOut
    baseline_pass_rate?: number | null
    regressions?: number
    improved?: number
    unchanged?: number
    mean_delta?: number | null
    ci95?: [number, number] | null
    sign_test_p?: number | null
    regressed_cases?: Array<{ case_key: string; delta: number | null; item_ids: string[] }>
  }
}

export interface EvalGateParams {
  baseline?: string
  max_regressions?: number
  min_pass_rate?: number
  threshold?: number
}

// ───────────────────────────── 评测对比 ─────────────────────────────

export type EvalCompareClass = 'improved' | 'regressed' | 'unchanged' | 'new' | 'missing' | 'unscored'

export interface EvalCompareSide {
  id: string
  version_seq: number | null
  stats: EvalStatsOut | null
  [k: string]: unknown
}

export interface EvalCompareSummary {
  improved: number
  regressed: number
  unchanged: number
  new: number
  missing: number
  mean_delta: number | null
  /** 只统计两侧都有分数的用例数 */
  paired?: number
  unscored?: number
  /** 重复间结果不稳定的用例数 */
  flaky?: number
  /** 平均 Δ 的配对 bootstrap 95% 置信区间；配对用例不足时为 null */
  ci95?: [number, number] | null
  wins?: number
  ties?: number
  losses?: number
  /** 符号检验（双侧）p 值；没有胜负用例时为 null */
  sign_test_p?: number | null
  metric_deltas: {
    pass_rate?: number | null
    latency_p50_ms?: number | null
    cost?: number | null
    tokens_total?: number | null
    [k: string]: number | null | undefined
  }
  [k: string]: unknown
}

export interface EvalCompareCaseSide {
  score: number | null
  pass_rate: number | null
  item_ids: string[]
}

export interface EvalCompareCase {
  case_key: string
  class: string
  /** 重复间结果不稳定 */
  flaky?: boolean
  delta: number | null
  tags: string[]
  left: EvalCompareCaseSide | null
  right: EvalCompareCaseSide | null
}

export interface EvalCompareOut {
  left: EvalCompareSide
  right: EvalCompareSide
  warnings: string[]
  summary: EvalCompareSummary
  cases: { items: EvalCompareCase[]; next_cursor: string | null }
}

// ───────────────────────────── SSE ─────────────────────────────

export const EVAL_STREAM_EVENT_TYPES = ['eval.status', 'eval.progress', 'eval.item_done', 'eval.budget_exceeded'] as const

export type EvalStreamEvent =
  | {
      type: 'eval.status'
      run_id: string
      status: EvalRunStatusOut
      stats: EvalStatsOut | null
      progress?: EvalProgressOut
      pause_reason?: string | null
      started_at?: string | null
      finished_at?: string | null
    }
  | { type: 'eval.budget_exceeded'; run_id: string; max_cost?: number | null; max_tokens?: number | null; cost?: number; tokens?: number }
  | { type: 'eval.progress'; run_id: string; progress: EvalProgressOut }
  | { type: 'eval.item_done'; run_id: string; item: EvalItemOut }
