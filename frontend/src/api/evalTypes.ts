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
  pass_rate?: number | null
  errors?: number
  n?: number
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
}

/** 评估器配置（创建时的请求体 / 详情里返回的配置） */
export type EvaluatorInput =
  | { key: string; type: 'exact_match'; gate?: boolean; values?: string[]; normalize?: string[] }
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
  | {
      key: string
      type: 'llm_judge'
      gate?: boolean
      provider: string
      model: string
      prompt: string
      scale?: [number, number]
      pass_threshold?: number
    }

/** 详情里的评估器配置：类型可能多于一期支持的四种 */
export interface EvaluatorOut {
  key: string
  type: string
  gate?: boolean
  [k: string]: unknown
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
  status: EvalRunStatusOut
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
  via?: 'ui'
}

export interface EvalCreated {
  batch_id: string
  runs: Array<{ id: string; version_id: string; status: EvalRunStatusOut }>
}

// ───────────────────────────── 评测对比 ─────────────────────────────

export type EvalCompareClass = 'improved' | 'regressed' | 'unchanged' | 'new' | 'missing'

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

export const EVAL_STREAM_EVENT_TYPES = ['eval.status', 'eval.progress', 'eval.item_done'] as const

export type EvalStreamEvent =
  | {
      type: 'eval.status'
      run_id: string
      status: EvalRunStatusOut
      stats: EvalStatsOut | null
      progress?: EvalProgressOut
      started_at?: string | null
      finished_at?: string | null
    }
  | { type: 'eval.progress'; run_id: string; progress: EvalProgressOut }
  | { type: 'eval.item_done'; run_id: string; item: EvalItemOut }
