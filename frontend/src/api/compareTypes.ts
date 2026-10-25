/** 对比与重放（docs/contract-eval.md §3、docs/api.md「对比与重放（P3-C）」） */

export type CompareMode = 'live' | 'history' | 'replay'

export interface CompareSide {
  session_id: string
  version_id: string
  version_seq: number | null
  agent_name: string | null
}

export interface CompareGroup {
  id: string
  kind: 'session'
  mode: CompareMode
  left: CompareSide
  right: CompareSide
  title: string | null
  created_at: string
}

/** POST /compare：live 传 version_id（也可写 `agent:vN`），history 传 session_id */
export type CompareCreate =
  | { kind: 'session'; mode: 'live'; left: { version_id: string }; right: { version_id: string }; title?: string }
  | { kind: 'session'; mode: 'history'; left: { session_id: string }; right: { session_id: string }; title?: string }

export interface CompareTurnCreate {
  input: string
  variables?: Record<string, unknown>
}

export interface CompareTurnAccepted {
  pair_index: number
  left_turn_id: string
  right_turn_id: string
}

export interface ReplayRequest {
  target_version_id: string
  /** 原会话的轮次 idx；缺省为全部 */
  turns?: number[]
  mode?: 'anchored'
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

export interface Alignment {
  turns: AlignTurn[]
}
