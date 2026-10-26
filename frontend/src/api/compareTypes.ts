/** 对比与重放（docs/contract-eval.md §3、docs/api.md「对比与重放（P3-C）」） */
import type { ReplayMode } from './types'

export type { ReplayMode }

export type CompareMode = 'live' | 'history' | 'replay'

export interface CompareSide {
  session_id: string
  version_id: string
  version_seq: number | null
  agent_name: string | null
}

/** 重放状态：进行中 / 完成 / 失败（单轮 agent 报错不算失败；轮次没能执行、被取消或服务中断才算） */
export type ReplayStatus = 'running' | 'completed' | 'failed'

export interface CompareGroup {
  id: string
  kind: 'session'
  mode: CompareMode
  left: CompareSide
  right: CompareSide
  title: string | null
  /** 用户备注 */
  notes: string | null
  created_at: string
  /** 以下四项只有 replay 对比有值，其余为 null */
  replay_mode: ReplayMode | null
  replay_status: ReplayStatus | null
  /** 被选中重放的原轮次 idx（升序） */
  replay_turns: number[] | null
  replay_error: string | null
}

/** PATCH /compare/{id}：只改出现的字段；null 或空白表示清空 */
export interface CompareUpdate {
  title?: string | null
  notes?: string | null
}

export const COMPARE_TITLE_MAX = 120
export const COMPARE_NOTES_MAX = 2000

/** 发给哪一侧：both 是双栏同发；left / right 是单侧消息（分叉），不参与轮次对齐 */
export type CompareSend = 'both' | 'left' | 'right'

/** POST /compare：live 传 version_id（也可写 `agent:vN`），history 传 session_id */
export type CompareCreate =
  | { kind: 'session'; mode: 'live'; left: { version_id: string }; right: { version_id: string }; title?: string }
  | { kind: 'session'; mode: 'history'; left: { session_id: string }; right: { session_id: string }; title?: string }

export interface CompareTurnCreate {
  input: string
  variables?: Record<string, unknown>
  side?: CompareSend
}

export interface CompareTurnAccepted {
  /** 单侧消息为 null */
  pair_index: number | null
  /** 没有发给左侧时为 null */
  left_turn_id: string | null
  right_turn_id: string | null
  side: CompareSend
}

export interface ReplayRequest {
  target_version_id: string
  /** 原会话的轮次 idx；缺省为全部 */
  turns?: number[]
  /** anchored（默认）：各轮用原会话的历史、并发；free：新版本用自己的回复续写、逐轮串行 */
  mode?: ReplayMode
}

export interface ReplayResponse {
  session_id: string
  compare_id: string
}

export type AlignOp = 'equal' | 'insert' | 'delete'

export interface AlignStep {
  op: AlignOp
  /** 左侧 step_id（insert 没有）；每轮从 s1 重新编号，需与 turn_id 组合 */
  left?: string | null
  right?: string | null
  /** `llm` / `tool:{name}` / `span:{name}` / `final` */
  sig: string
}

export interface ToolArgChange {
  path: string
  left: unknown
  right: unknown
  op?: 'change' | 'add' | 'remove'
}

export interface ToolArgsDiff {
  left: string
  right: string
  changes: ToolArgChange[]
}

export interface AlignMetrics {
  latency_ms: number | null
  ttft_ms: number | null
  tokens: number
  cost: number
  llm_calls: number
  tool_calls: number
}

export interface AlignTurn {
  pair: number
  left_turn_id: string | null
  right_turn_id: string | null
  steps: AlignStep[]
  tool_args_diff: ToolArgsDiff[]
  /** 只有一侧有的轮次，另一侧为 null */
  metrics: { left: AlignMetrics | null; right: AlignMetrics | null }
  final_text: { left: string | null; right: string | null }
  status?: { left: string | null; right: string | null }
  input?: { left: string | null; right: string | null }
}

/** 单侧消息（分叉）：不参与轮次对齐，排在 after_pair 对应的行之后 */
export interface AlignFork {
  side: 'left' | 'right'
  turn_id: string
  idx: number
  /** 该侧此前最近一个成对轮次的 pair_index；前面没有则为 null */
  after_pair: number | null
  input: string | null
  final_text: string | null
  status: string
  metrics: AlignMetrics | null
}

export interface Alignment {
  turns: AlignTurn[]
  /** 旧服务端没有这个字段 */
  forks?: AlignFork[]
}
