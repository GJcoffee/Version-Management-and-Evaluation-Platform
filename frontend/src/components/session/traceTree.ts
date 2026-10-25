import type { EventEnvelope } from '@/api/types'

/**
 * Trace 步骤树与时间瀑布图的纯函数（详设 6.2 / 14.2）。
 *
 * 步骤的配对规则：同一个 step_id 的事件属于同一个节点——
 *   llm.request / llm.response、tool.call / tool.result、span.start / span.end；
 * log、error、message.assistant 没有配对，各自成为一个瞬时节点；
 * parent_step_id 指向的节点存在时成为其子节点，否则挂在根上。
 */

export type TraceKind = 'llm' | 'tool' | 'span' | 'log' | 'error' | 'final'

/** running：已开始未结束；incomplete：轮次已结束但该步骤始终没有结束事件 */
export type TraceStatus = 'running' | 'ok' | 'error' | 'incomplete'

export const TRACE_KIND_LABEL: Record<TraceKind, string> = {
  llm: 'LLM',
  tool: '工具',
  span: 'Span',
  log: '日志',
  error: '错误',
  final: '最终回复',
}
export const TRACE_KINDS = Object.keys(TRACE_KIND_LABEL) as TraceKind[]

export interface TraceNode {
  /** 节点 id：step_id；没有 step_id 的事件用 `类型#seq` 合成 */
  id: string
  stepId: string | null
  kind: TraceKind
  /** 显示名：模型名 / 工具名 / span 名 / 日志级别 / 错误码 */
  name: string
  parentId: string | null
  status: TraceStatus
  /** 开始时间（epoch ms） */
  startMs: number
  /** 结束时间；仍在运行时为 null */
  endMs: number | null
  /** 展示用耗时：优先取 payload.latency_ms，否则用时间戳之差 */
  durationMs: number | null
  /** 第一个事件的 seq */
  seq: number | null
  mocked: boolean
  /** 构成该节点的事件（按到达顺序） */
  events: EventEnvelope[]
  children: TraceNode[]
}

function ms(ts: string | undefined): number {
  const t = ts ? Date.parse(ts) : NaN
  return Number.isNaN(t) ? 0 : t
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function makeNode(id: string, stepId: string | null, kind: TraceKind, ev: EventEnvelope): TraceNode {
  return {
    id,
    stepId,
    kind,
    name: '',
    parentId: null,
    status: 'running',
    startMs: ms(ev.ts),
    endMs: null,
    durationMs: null,
    seq: ev.seq ?? null,
    mocked: false,
    events: [],
    children: [],
  }
}

/** 按事件序列构建步骤树，返回根节点列表（按开始时间排序）。events 需已按 seq 升序。 */
export function buildStepTree(events: readonly EventEnvelope[]): TraceNode[] {
  const nodes = new Map<string, TraceNode>()
  const order: TraceNode[] = []
  const parentOf = new Map<string, string | null>()
  let turnEnded = false

  const get = (ev: EventEnvelope, kind: TraceKind): TraceNode => {
    const stepId = ev.step_id ?? null
    const id = stepId ?? `${ev.type}#${ev.seq ?? order.length}`
    let n = nodes.get(id)
    if (!n) {
      n = makeNode(id, stepId, kind, ev)
      nodes.set(id, n)
      order.push(n)
      parentOf.set(id, ev.parent_step_id ?? null)
    } else if (!parentOf.get(id) && ev.parent_step_id) {
      parentOf.set(id, ev.parent_step_id)
    }
    n.events.push(ev)
    return n
  }

  for (const ev of events) {
    switch (ev.type) {
      case 'llm.request': {
        const n = get(ev, 'llm')
        n.name = ev.payload.model || n.name || 'LLM'
        n.startMs = ms(ev.ts)
        break
      }
      case 'llm.response': {
        const n = get(ev, 'llm')
        if (!n.name) n.name = 'LLM'
        const lat = num(ev.payload.latency_ms)
        n.endMs = ms(ev.ts)
        if (n.events.length === 1) n.startMs = lat !== null ? n.endMs - lat : n.endMs // 缺少 request 事件
        n.durationMs = lat ?? n.endMs - n.startMs
        n.status = 'ok'
        break
      }
      case 'tool.call': {
        const n = get(ev, 'tool')
        n.name = ev.payload.name
        n.startMs = ms(ev.ts)
        break
      }
      case 'tool.result': {
        const n = get(ev, 'tool')
        if (!n.name) n.name = ev.payload.name
        const lat = num(ev.payload.latency_ms)
        n.endMs = ms(ev.ts)
        if (n.events.length === 1) n.startMs = lat !== null ? n.endMs - lat : n.endMs
        n.durationMs = lat ?? n.endMs - n.startMs
        n.status = ev.payload.is_error ? 'error' : 'ok'
        n.mocked = !!ev.payload.mocked
        break
      }
      case 'span.start': {
        const n = get(ev, 'span')
        n.name = ev.payload.name
        n.startMs = ms(ev.ts)
        break
      }
      case 'span.end': {
        const n = get(ev, 'span')
        if (!n.name) n.name = ev.payload.name
        const lat = num(ev.payload.latency_ms)
        n.endMs = ms(ev.ts)
        if (n.events.length === 1) n.startMs = lat !== null ? n.endMs - lat : n.endMs
        n.durationMs = lat ?? n.endMs - n.startMs
        n.status = 'ok'
        break
      }
      case 'log': {
        const n = get(ev, 'log')
        n.name = String(ev.payload.level ?? 'info')
        n.endMs = n.startMs
        n.durationMs = 0
        n.status = /^(error|critical|fatal)/i.test(String(ev.payload.level)) ? 'error' : 'ok'
        break
      }
      case 'error': {
        const n = get(ev, 'error')
        n.name = ev.payload.code || 'ERROR'
        n.endMs = n.startMs
        n.durationMs = 0
        n.status = 'error'
        break
      }
      case 'message.assistant': {
        const n = get(ev, 'final')
        n.name = '最终回复'
        n.endMs = n.startMs
        n.durationMs = 0
        n.status = 'ok'
        break
      }
      case 'turn.end':
        turnEnded = true
        break
      default:
        break // turn.start / llm.delta 不是步骤
    }
  }

  // 轮次已结束但步骤没有结束事件：标为 incomplete
  if (turnEnded) for (const n of order) if (n.status === 'running') n.status = 'incomplete'

  // 挂接父子关系；父节点不存在、指向自己或形成环时挂到根
  const roots: TraceNode[] = []
  const wouldCycle = (id: string, parent: string): boolean => {
    for (let cur: string | null | undefined = parent, i = 0; cur && i < 1000; i++) {
      if (cur === id) return true
      cur = parentOf.get(cur)
    }
    return false
  }
  for (const n of order) {
    const p = parentOf.get(n.id)
    const parent = p && p !== n.id && nodes.has(p) && !wouldCycle(n.id, p) ? nodes.get(p)! : null
    if (parent) {
      n.parentId = parent.id
      parent.children.push(n)
    } else roots.push(n)
  }
  const cmp = (a: TraceNode, b: TraceNode) => a.startMs - b.startMs || (a.seq ?? 0) - (b.seq ?? 0)
  const sortRec = (list: TraceNode[]) => {
    list.sort(cmp)
    for (const n of list) sortRec(n.children)
  }
  sortRec(roots)
  return roots
}

/** 深度优先遍历（含根） */
export function walkTree(roots: readonly TraceNode[], fn: (n: TraceNode, depth: number) => void, depth = 0): void {
  for (const n of roots) {
    fn(n, depth)
    walkTree(n.children, fn, depth + 1)
  }
}

export interface TraceFilter {
  /** 只显示这些类型；空或未提供表示全部 */
  kinds?: ReadonlySet<TraceKind>
  /** 文本搜索（不区分大小写），匹配名称与事件内容 */
  query?: string
}

export interface FlatRow {
  node: TraceNode
  depth: number
  hasChildren: boolean
  expanded: boolean
}

const searchCache = new WeakMap<TraceNode, string>()

/** 节点的可搜索文本：名称、类型与全部事件 payload 的 JSON（小写） */
export function nodeSearchText(n: TraceNode): string {
  let t = searchCache.get(n)
  // 事件数量变化（配对完成）后缓存失效
  const key = `${n.events.length}`
  if (t === undefined || !t.startsWith(`${key}\u0000`)) {
    let body = ''
    for (const e of n.events) {
      try {
        body += JSON.stringify(e.payload) + '\n'
      } catch {
        /* 忽略不可序列化的内容 */
      }
    }
    t = `${key}\u0000${n.name}\n${n.kind}\n${n.stepId ?? ''}\n${body}`.toLowerCase()
    searchCache.set(n, t)
  }
  return t
}

export function nodeMatches(n: TraceNode, filter: TraceFilter): boolean {
  if (filter.kinds && filter.kinds.size > 0 && !filter.kinds.has(n.kind)) return false
  const q = filter.query?.trim().toLowerCase()
  if (q && !nodeSearchText(n).includes(q)) return false
  return true
}

/**
 * 展开成可渲染的行：
 * - 没有筛选条件时按 collapsed 折叠；
 * - 有筛选条件时只保留命中的节点及其祖先（祖先即使不命中也显示，保持树结构），并忽略折叠。
 */
export function flattenTree(
  roots: readonly TraceNode[],
  opts: { collapsed?: ReadonlySet<string>; filter?: TraceFilter } = {},
): FlatRow[] {
  const { collapsed, filter } = opts
  const active = !!filter && ((filter.kinds?.size ?? 0) > 0 || !!filter.query?.trim())
  const rows: FlatRow[] = []

  // 返回该子树是否有命中的节点
  const visit = (list: readonly TraceNode[], depth: number): boolean => {
    let any = false
    for (const n of list) {
      if (!active) {
        const isCollapsed = collapsed?.has(n.id) ?? false
        rows.push({ node: n, depth, hasChildren: n.children.length > 0, expanded: !isCollapsed })
        if (!isCollapsed) visit(n.children, depth + 1)
        any = true
        continue
      }
      const at = rows.length
      rows.push({ node: n, depth, hasChildren: n.children.length > 0, expanded: true })
      const childHit = visit(n.children, depth + 1)
      const self = nodeMatches(n, filter!)
      if (self || childHit) any = true
      else rows.length = at // 整个子树都不命中
    }
    return any
  }
  visit(roots, 0)
  return rows
}

// ───────────────────────────── 时间瀑布图 ─────────────────────────────

export interface WaterfallBar {
  /** 起点占总时长的比例 [0, 1] */
  left: number
  /** 宽度占总时长的比例 [0, 1]；瞬时节点为 0（渲染时给最小宽度） */
  width: number
  /** 相对起点的偏移 / 持续时间（毫秒） */
  offsetMs: number
  spanMs: number
  /** 仍在运行（右端画到“现在”） */
  open: boolean
}

export interface Timeline {
  originMs: number
  endMs: number
  totalMs: number
  bars: Map<string, WaterfallBar>
}

/**
 * 计算每个节点在瀑布图中的位置。
 * - originMs 缺省为最早的开始时间；endMs 缺省为最晚的结束时间；
 * - 运行中的节点（endMs 为 null）画到 nowMs（缺省为已知的最晚时间）；
 * - 总时长至少 1ms，避免除零。
 */
export function computeTimeline(
  nodes: readonly TraceNode[],
  opts: { originMs?: number; endMs?: number; nowMs?: number } = {},
): Timeline {
  let minStart = Infinity
  let maxEnd = -Infinity
  for (const n of nodes) {
    minStart = Math.min(minStart, n.startMs)
    maxEnd = Math.max(maxEnd, n.endMs ?? n.startMs)
  }
  if (!Number.isFinite(minStart)) {
    const o = opts.originMs ?? 0
    return { originMs: o, endMs: o + 1, totalMs: 1, bars: new Map() }
  }
  const nowMs = opts.nowMs ?? maxEnd
  for (const n of nodes) if (n.endMs === null && n.status === 'running') maxEnd = Math.max(maxEnd, nowMs)
  const originMs = Math.min(opts.originMs ?? minStart, minStart)
  const endMs = Math.max(opts.endMs ?? maxEnd, maxEnd)
  const totalMs = Math.max(endMs - originMs, 1)
  const bars = new Map<string, WaterfallBar>()
  const clamp = (x: number) => Math.min(1, Math.max(0, x))
  for (const n of nodes) {
    const open = n.endMs === null && n.status === 'running'
    const end = n.endMs ?? (open ? Math.max(nowMs, n.startMs) : n.startMs)
    const offsetMs = n.startMs - originMs
    const spanMs = Math.max(end - n.startMs, 0)
    const left = clamp(offsetMs / totalMs)
    bars.set(n.id, { left, width: clamp(Math.min(spanMs / totalMs, 1 - left)), offsetMs, spanMs, open })
  }
  return { originMs, endMs, totalMs, bars }
}

/** 瀑布图刻度：总时长 -> 约 n 个整齐的刻度（毫秒） */
export function timelineTicks(totalMs: number, n = 4): number[] {
  if (totalMs <= 0) return [0]
  const raw = totalMs / n
  const pow = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let t = 0; t <= totalMs + 1e-9; t += step) out.push(Math.round(t * 1000) / 1000)
  return out
}
