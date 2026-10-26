/**
 * 前端增量对齐（详设 9.2）：轮次运行中，根据 SSE 还原出的事件在浏览器里算出步骤对齐，
 * 让对齐视图随输出实时更新；轮次结束后以服务端的对齐结果为准（见 CompareView）。
 *
 * 算法与后端 `evalp/compare/align.py` 一致：步骤签名取自 `llm.response / tool.call / span.start /
 * message.assistant`，用 difflib.SequenceMatcher 的“最长连续匹配 + 递归”求对齐（autojunk=False），
 * 工具调用参数做 JSON diff。一致性由 incrementalAlign.test.ts 里用后端生成的用例守住。
 */
import type { AlignStep, AlignTurn, ToolArgChange } from '@/api/compareTypes'
import type { EventEnvelope } from '@/api/types'
import type { TurnState } from '@/components/session'

export interface Step {
  id: string
  sig: string
  /** 仅工具调用有值 */
  args?: unknown
}

/** 步骤签名；不参与对齐的事件返回 null */
export function stepSignature(e: Pick<EventEnvelope, 'type' | 'payload'>): string | null {
  const payload = (e.payload ?? {}) as Record<string, unknown>
  switch (e.type) {
    case 'llm.response':
      return 'llm'
    case 'tool.call':
      return `tool:${String(payload.name ?? '')}`
    case 'span.start':
      return `span:${String(payload.name ?? '')}`
    case 'message.assistant':
      return 'final'
    default:
      return null
  }
}

/** 事件序列 -> 步骤序列；没有 step_id 的步骤：final 记为 "final"，其余记为 "e{seq}" */
export function extractSteps(events: readonly EventEnvelope[]): Step[] {
  const out: Step[] = []
  for (const e of events) {
    const sig = stepSignature(e)
    if (sig === null) continue
    const id = e.step_id || (sig === 'final' ? 'final' : `e${e.seq}`)
    out.push({ id: String(id), sig, args: sig.startsWith('tool:') ? (e.payload as Record<string, unknown>).arguments : undefined })
  }
  return out
}

type Block = [i: number, j: number, size: number]

/** difflib.SequenceMatcher(autojunk=False).get_matching_blocks()，不含末尾哨兵 */
export function matchingBlocks(a: readonly string[], b: readonly string[]): Block[] {
  const b2j = new Map<string, number[]>()
  b.forEach((x, j) => {
    const list = b2j.get(x)
    if (list) list.push(j)
    else b2j.set(x, [j])
  })

  const longest = (alo: number, ahi: number, blo: number, bhi: number): Block => {
    let besti = alo
    let bestj = blo
    let bestsize = 0
    let j2len = new Map<number, number>()
    for (let i = alo; i < ahi; i++) {
      const newj2len = new Map<number, number>()
      for (const j of b2j.get(a[i]) ?? []) {
        if (j < blo) continue
        if (j >= bhi) break
        const k = (j2len.get(j - 1) ?? 0) + 1
        newj2len.set(j, k)
        if (k > bestsize) {
          besti = i - k + 1
          bestj = j - k + 1
          bestsize = k
        }
      }
      j2len = newj2len
    }
    return [besti, bestj, bestsize]
  }

  const queue: Array<[number, number, number, number]> = [[0, a.length, 0, b.length]]
  const found: Block[] = []
  while (queue.length > 0) {
    const [alo, ahi, blo, bhi] = queue.pop()!
    const [i, j, k] = longest(alo, ahi, blo, bhi)
    if (k === 0) continue
    found.push([i, j, k])
    if (alo < i && blo < j) queue.push([alo, i, blo, j])
    if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi])
  }
  found.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2])
  // 合并相邻的块（与 difflib 一致）
  const merged: Block[] = []
  for (const blk of found) {
    const last = merged[merged.length - 1]
    if (last && last[0] + last[2] === blk[0] && last[1] + last[2] === blk[1]) last[2] += blk[2]
    else merged.push([...blk])
  }
  return merged
}

/** 步骤签名序列的对齐，输出与后端 `align_steps` 相同：equal / delete / insert（不同的步骤先删后增） */
export function alignSteps(left: readonly Step[], right: readonly Step[]): AlignStep[] {
  const blocks = matchingBlocks(
    left.map((s) => s.sig),
    right.map((s) => s.sig),
  )
  blocks.push([left.length, right.length, 0])
  const out: AlignStep[] = []
  let i = 0
  let j = 0
  for (const [bi, bj, size] of blocks) {
    for (; i < bi; i++) out.push({ op: 'delete', left: left[i].id, sig: left[i].sig })
    for (; j < bj; j++) out.push({ op: 'insert', right: right[j].id, sig: right[j].sig })
    for (let n = 0; n < size; n++) out.push({ op: 'equal', left: left[i + n].id, right: right[j + n].id, sig: left[i + n].sig })
    i += size
    j += size
  }
  return out
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** 两个 JSON 值的差异（对象按键、数组按下标递归）；与后端 `json_diff` 一致 */
export function jsonDiff(left: unknown, right: unknown, path = ''): ToolArgChange[] {
  if (isObject(left) && isObject(right)) {
    const out: ToolArgChange[] = []
    const keys = [...Object.keys(left), ...Object.keys(right).filter((k) => !(k in left))]
    for (const k of keys) {
      const p = path ? `${path}.${k}` : k
      if (!(k in right)) out.push({ path: p, left: left[k], right: null, op: 'remove' })
      else if (!(k in left)) out.push({ path: p, left: null, right: right[k], op: 'add' })
      else out.push(...jsonDiff(left[k], right[k], p))
    }
    return out
  }
  if (Array.isArray(left) && Array.isArray(right)) {
    const out: ToolArgChange[] = []
    for (let i = 0; i < Math.max(left.length, right.length); i++) {
      const p = `${path}[${i}]`
      if (i >= right.length) out.push({ path: p, left: left[i], right: null, op: 'remove' })
      else if (i >= left.length) out.push({ path: p, left: null, right: right[i], op: 'add' })
      else out.push(...jsonDiff(left[i], right[i], p))
    }
    return out
  }
  if (JSON.stringify(left) === JSON.stringify(right) && typeof left === typeof right) return []
  return [{ path, left, right, op: 'change' }]
}

const finalText = (t: TurnState | undefined): string | null => {
  const m = t?.messages.find((x) => x.final)
  return m ? m.content : null
}

const metricsOf = (t: TurnState | undefined) =>
  t
    ? {
        latency_ms: t.metrics.latencyMs,
        ttft_ms: t.metrics.ttftMs,
        tokens: t.metrics.totalTokens,
        cost: t.metrics.cost,
        llm_calls: t.metrics.llmCalls,
        tool_calls: t.metrics.toolCalls,
      }
    : null

/**
 * 用两侧当前已收到的事件算出一行的对齐结果（形状同服务端 `turns[]` 的一项）。
 * 任一侧还没有轮次时，该侧的步骤为空（全部算对侧独有）。
 */
export function liveAlignTurn(
  pair: number,
  leftTurnId: string | null,
  rightTurnId: string | null,
  left: TurnState | undefined,
  right: TurnState | undefined,
): AlignTurn {
  const ls = left ? extractSteps(left.events) : []
  const rs = right ? extractSteps(right.events) : []
  const steps = alignSteps(ls, rs)
  const lmap = new Map(ls.map((s) => [s.id, s]))
  const rmap = new Map(rs.map((s) => [s.id, s]))
  const tool_args_diff: AlignTurn['tool_args_diff'] = []
  for (const st of steps) {
    if (st.op === 'equal' && st.sig.startsWith('tool:') && st.left && st.right) {
      const changes = jsonDiff(lmap.get(st.left)?.args, rmap.get(st.right)?.args)
      if (changes.length > 0) tool_args_diff.push({ left: st.left, right: st.right, changes })
    }
  }
  return {
    pair,
    left_turn_id: leftTurnId,
    right_turn_id: rightTurnId,
    steps,
    tool_args_diff,
    metrics: { left: metricsOf(left), right: metricsOf(right) },
    final_text: { left: finalText(left), right: finalText(right) },
    status: { left: left?.status ?? null, right: right?.status ?? null },
  }
}
