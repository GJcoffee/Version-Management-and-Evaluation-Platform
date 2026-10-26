import DiffMatchPatch from 'diff-match-patch'
import type { AlignFork, AlignTurn, Alignment, CompareMode, ReplayMode } from '@/api/compareTypes'
import { highlightKey, type TurnState } from '@/components/session'

// ───────────────────────────── 行模型 ─────────────────────────────

/** 一行 = 同一个 pair 的左右两轮（任一侧可以没有）；或一条单侧消息（分叉，只有一侧有轮次） */
export interface CompareRow {
  /** 行的唯一键：成对行为 `p{pair}`，单侧消息为 `f:{turn_id}` */
  key: string
  /** pair_index（live / replay）或 idx（history）；单侧消息为 -1 */
  pair: number
  leftTurnId: string | null
  rightTurnId: string | null
  /** 服务端对齐结果（没拉到时为 undefined） */
  align?: AlignTurn
  /** 单侧消息：发给了哪一侧 */
  fork?: 'left' | 'right'
}

/** 刚发送、对齐结果还没返回的 pair（POST /compare/{id}/turns 的响应） */
export interface SeededPair {
  pair: number
  leftTurnId: string
  rightTurnId: string
}

/** 刚发送的单侧消息；afterPair 为发送时界面上最后一个成对行的 pair（没有则 null） */
export interface SeededFork {
  side: 'left' | 'right'
  turnId: string
  afterPair: number | null
}

/**
 * 行 = 服务端对齐结果的轮次（重放时只含选中的轮次）+ 本地刚发送、对齐结果里还没有的 pair。
 * 成对行按 pair 升序；单侧消息（分叉）排在 after_pair 对应的行之后（同一位置左侧先、再按 idx）。
 */
export function buildRows(
  alignment: Alignment | undefined,
  seeded: readonly SeededPair[] = [],
  seededForks: readonly SeededFork[] = [],
): CompareRow[] {
  const rows = new Map<number, CompareRow>()
  for (const t of alignment?.turns ?? []) {
    rows.set(t.pair, { key: `p${t.pair}`, pair: t.pair, leftTurnId: t.left_turn_id, rightTurnId: t.right_turn_id, align: t })
  }
  for (const s of seeded) {
    const cur = rows.get(s.pair)
    if (!cur) rows.set(s.pair, { key: `p${s.pair}`, pair: s.pair, leftTurnId: s.leftTurnId, rightTurnId: s.rightTurnId })
    else if (!cur.leftTurnId || !cur.rightTurnId) {
      rows.set(s.pair, { ...cur, leftTurnId: cur.leftTurnId ?? s.leftTurnId, rightTurnId: cur.rightTurnId ?? s.rightTurnId })
    }
  }
  const pairRows = [...rows.values()].sort((a, b) => a.pair - b.pair)

  const forks: Array<{ after: number | null; side: 'left' | 'right'; turnId: string }> = (alignment?.forks ?? []).map((f: AlignFork) => ({
    after: f.after_pair,
    side: f.side,
    turnId: f.turn_id,
  }))
  const known = new Set(forks.map((f) => f.turnId))
  for (const f of seededForks) if (!known.has(f.turnId)) forks.push({ after: f.afterPair, side: f.side, turnId: f.turnId })
  if (forks.length === 0) return pairRows

  const forkRows = (after: number | null): CompareRow[] =>
    forks
      .filter((f) => f.after === after)
      .map((f) => ({
        key: `f:${f.turnId}`,
        pair: -1,
        fork: f.side,
        leftTurnId: f.side === 'left' ? f.turnId : null,
        rightTurnId: f.side === 'right' ? f.turnId : null,
      }))
  const out: CompareRow[] = forkRows(null)
  const placed = new Set<number | null>([null])
  for (const r of pairRows) {
    out.push(r)
    out.push(...forkRows(r.pair))
    placed.add(r.pair)
  }
  // after_pair 指向的成对行（暂时）不存在：放在最后，避免丢失
  for (const f of forks) {
    if (!placed.has(f.after)) {
      placed.add(f.after)
      out.push(...forkRows(f.after))
    }
  }
  return out
}

export function turnById(turns: readonly TurnState[], id: string | null): TurnState | undefined {
  return id ? turns.find((t) => t.id === id) : undefined
}

// ───────────────────────────── 高亮 ─────────────────────────────

export interface RowHighlights {
  left: string[]
  right: string[]
}

/** 单轮的高亮：delete 的步骤在左侧、insert 的步骤在右侧，参数不同的工具调用两侧都标；key 为 `turn_id:step_id` */
export function rowHighlights(t: AlignTurn): RowHighlights {
  const left: string[] = []
  const right: string[] = []
  const lid = t.left_turn_id
  const rid = t.right_turn_id
  for (const s of t.steps) {
    if (s.op === 'delete' && lid && s.left) left.push(highlightKey(lid, s.left))
    if (s.op === 'insert' && rid && s.right) right.push(highlightKey(rid, s.right))
  }
  for (const d of t.tool_args_diff ?? []) {
    if (lid && d.left) left.push(highlightKey(lid, d.left))
    if (rid && d.right) right.push(highlightKey(rid, d.right))
  }
  return { left, right }
}

/** 全部轮次的高亮集合 */
export function alignmentHighlights(a: Alignment | undefined): { left: ReadonlySet<string>; right: ReadonlySet<string> } {
  const left = new Set<string>()
  const right = new Set<string>()
  for (const t of a?.turns ?? []) {
    const h = rowHighlights(t)
    h.left.forEach((k) => left.add(k))
    h.right.forEach((k) => right.add(k))
  }
  return { left, right }
}

// ───────────────────────────── 步骤对齐摘要 ─────────────────────────────

/** `llm` / `tool:x` / `span:x` / `final` -> 人话 */
export function describeSig(sig: string): string {
  if (sig === 'llm') return 'LLM'
  if (sig === 'final') return '最终回复'
  if (sig.startsWith('tool:')) return sig.slice(5)
  if (sig.startsWith('span:')) return `span ${sig.slice(5)}`
  return sig
}

function countBySig(sigs: string[]): Array<[string, number]> {
  const m = new Map<string, number>()
  for (const s of sigs) m.set(s, (m.get(s) ?? 0) + 1)
  return [...m.entries()]
}

function phrase(side: '左侧' | '右侧', sig: string, n: number): string {
  if (sig === 'llm') return `${side}多 ${n} 次 LLM 调用`
  if (sig === 'final') return `${side}多一条最终回复`
  if (sig.startsWith('tool:')) return `${side}多调用 ${sig.slice(5)} ×${n}`
  return `${side}多 ${describeSig(sig)} ×${n}`
}

/** 摘要行，例如 ["右侧多调用 knowledge_search ×1"]；两侧步骤一致且参数相同时返回空数组 */
export function summarizeSteps(t: AlignTurn): string[] {
  if (!t.left_turn_id && !t.right_turn_id) return []
  if (!t.left_turn_id) return ['仅右侧有此轮']
  if (!t.right_turn_id) return ['仅左侧有此轮']
  const out: string[] = []
  const del = countBySig(t.steps.filter((s) => s.op === 'delete').map((s) => s.sig))
  const ins = countBySig(t.steps.filter((s) => s.op === 'insert').map((s) => s.sig))
  for (const [sig, n] of del) out.push(phrase('左侧', sig, n))
  for (const [sig, n] of ins) out.push(phrase('右侧', sig, n))
  return out
}

/** 工具参数差异的描述：`query_order.order_id: "A1" → "A2"` */
export function describeArgsDiff(t: AlignTurn): string[] {
  const sigOf = new Map<string, string>()
  for (const s of t.steps) {
    if (s.op === 'equal' && s.left) sigOf.set(s.left, s.sig)
  }
  const out: string[] = []
  for (const d of t.tool_args_diff ?? []) {
    const name = describeSig(sigOf.get(d.left) ?? 'tool:?')
    for (const c of d.changes) {
      const f = (v: unknown) => (v === null || v === undefined ? '（无）' : typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v))
      out.push(`${name} 的参数 ${c.path || '(根)'}：${f(c.left)} → ${f(c.right)}`)
    }
  }
  return out
}

// ───────────────────────────── 指标对照 ─────────────────────────────

export type MetricKey = 'latency' | 'ttft' | 'tokens' | 'cost' | 'llm_calls' | 'tool_calls'

export interface MetricDef {
  key: MetricKey
  label: string
  /** 数值越小越好（耗时、tokens、成本）；次数类只标出差值，不判优劣 */
  lowerIsBetter: boolean
}

export const METRICS: MetricDef[] = [
  { key: 'latency', label: '耗时', lowerIsBetter: true },
  { key: 'ttft', label: 'TTFT', lowerIsBetter: true },
  { key: 'tokens', label: 'tokens', lowerIsBetter: true },
  { key: 'cost', label: '成本', lowerIsBetter: true },
  { key: 'llm_calls', label: 'LLM 调用', lowerIsBetter: false },
  { key: 'tool_calls', label: '工具调用', lowerIsBetter: false },
]

export function metricValue(t: TurnState | undefined, key: MetricKey): number | null {
  if (!t) return null
  const m = t.metrics
  switch (key) {
    case 'latency':
      return m.latencyMs
    case 'ttft':
      return m.ttftMs
    case 'tokens':
      return m.totalTokens
    case 'cost':
      return m.cost
    case 'llm_calls':
      return m.llmCalls
    case 'tool_calls':
      return m.toolCalls
  }
}

export type Trend = 'better' | 'worse' | 'neutral' | 'same'

export interface MetricDelta {
  /** right - left */
  delta: number
  /** 相对左侧的变化比例；左侧为 0 时为 null */
  ratio: number | null
  trend: Trend
}

/** 右侧相对左侧的差值与趋势：任一侧缺失返回 null */
export function metricDelta(left: number | null, right: number | null, lowerIsBetter: boolean): MetricDelta | null {
  if (left === null || right === null) return null
  const delta = right - left
  const ratio = left !== 0 ? delta / Math.abs(left) : null
  if (Math.abs(delta) < 1e-12) return { delta: 0, ratio: 0, trend: 'same' }
  if (!lowerIsBetter) return { delta, ratio, trend: 'neutral' }
  return { delta, ratio, trend: delta < 0 ? 'better' : 'worse' }
}

// ───────────────────────────── 文本 diff ─────────────────────────────

export type DiffOp = 'equal' | 'insert' | 'delete'
export interface DiffPiece {
  op: DiffOp
  text: string
}

/** 逐字（按字符）diff，适合中文；left -> right */
export function charDiff(left: string, right: string): DiffPiece[] {
  const dmp = new DiffMatchPatch()
  dmp.Diff_Timeout = 1
  const diffs = dmp.diff_main(left, right)
  dmp.diff_cleanupSemantic(diffs)
  return diffs.map(([op, text]) => ({ op: op === 0 ? 'equal' : op > 0 ? 'insert' : 'delete', text }))
}

// ───────────────────────────── 杂项 ─────────────────────────────

export const MODE_TAG: Record<CompareMode, string> = { live: '实时', history: '历史', replay: '重放' }
export const REPLAY_MODE_LABEL: Record<ReplayMode, string> = { anchored: '锚定模式', free: '自由模式' }
