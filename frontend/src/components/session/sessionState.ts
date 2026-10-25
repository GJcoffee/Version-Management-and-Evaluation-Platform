import type {
  EventEnvelope,
  LlmResponsePayload,
  Message,
  ToolCall,
  ToolCallDelta,
  TurnEndPayload,
  TurnError,
  TurnStatus,
  Usage,
} from '@/api/types'
import { buildStepTree, type TraceNode } from './traceTree'

/**
 * 会话事件流 -> 结构化状态（轮次 -> 消息 -> 步骤树）。reducer 是纯函数，不依赖 React / 网络。
 *
 * 要点：
 * - 落库的事件有 seq：已收到的 seq 直接忽略（去重）；比已收到的最大 seq 小但没见过的（乱序）会插入后整体重建；
 * - llm.delta 没有 seq、不落库：增量拼进当前 LLM 步骤的消息里；收到该步骤的 llm.response 后以其内容为准，
 *   之后迟到的 delta 被忽略；
 * - 大 payload 被转存为 blob 时（payload.$blob），对应字段缺失，视图上带 blob 哈希，由 UI 按需加载。
 */

// ───────────────────────────── 视图类型 ─────────────────────────────

export type ToolCallStatus =
  /** 模型还在生成这次调用（tool_call_delta 阶段） */
  | 'streaming'
  /** 模型已给出调用，尚未开始执行 */
  | 'pending'
  | 'running'
  | 'done'
  | 'error'
  /** 轮次已结束但这次调用没有结果（取消 / 超时 / 出错） */
  | 'interrupted'

export interface ToolCallView {
  /** 模型给出的 call id；增量阶段还没有 id 时为 `idx:N` 占位 */
  id: string
  /** 增量阶段的 tool_call index */
  index: number | null
  name: string
  /** 解析后的参数；流式阶段 / 无法解析时为原始字符串 */
  arguments: Record<string, unknown> | string | undefined
  /** 流式阶段累积的原始参数文本 */
  argsText: string
  /** tool.call 事件的 step_id（用于 Trace 联动与高亮） */
  stepId: string | null
  status: ToolCallStatus
  output: unknown
  hasOutput: boolean
  isError: boolean
  mocked: boolean
  latencyMs: number | null
  /** tool.call / tool.result 被转存为 blob 时的哈希（完整 payload 在 blob 里） */
  argsBlob: string | null
  outputBlob: string | null
}

export interface LlmInfo {
  provider: string | null
  model: string | null
  finishReason: string | null
  promptTokens: number | null
  completionTokens: number | null
  latencyMs: number | null
  ttftMs: number | null
  cost: number | null
}

export interface AssistantMessageView {
  /** React key，稳定且在轮次内唯一 */
  key: string
  /** 对应的 LLM 步骤 id（代码型 agent 的最终回复没有） */
  stepId: string | null
  content: string
  /** 正在流式输出（尚未收到 llm.response） */
  streaming: boolean
  /** 是本轮的最终回复 */
  final: boolean
  toolCalls: ToolCallView[]
  llm: LlmInfo | null
  /** llm.response / message.assistant 被转存为 blob 时的哈希 */
  contentBlob: string | null
  /** 是否已收到 llm.response（之后的 delta 被忽略） */
  responded: boolean
}

export interface TurnMetrics {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  /** 轮次耗时；结束前为 null（界面用 startedAt 现算） */
  latencyMs: number | null
  ttftMs: number | null
  cost: number
  llmCalls: number
  toolCalls: number
  /** 来自 turn.end 的权威值（false 表示运行中的实时累计） */
  final: boolean
}

export interface TurnState {
  id: string
  status: TurnStatus
  input: Message | null
  /** turn.start 被转存为 blob（用户输入很大）时的哈希；完整 payload 是 `{input, history_len}` */
  inputBlob: string | null
  historyLen: number
  messages: AssistantMessageView[]
  /** 步骤树（根节点列表），用于 TraceViewer */
  steps: TraceNode[]
  /** 本轮落库事件（按 seq 升序，不含 llm.delta） */
  events: EventEnvelope[]
  error: TurnError | null
  metrics: TurnMetrics
  startedAt: string | null
  endedAt: string | null
  /** 最近一个事件的时间戳 */
  lastEventAt: string | null
  ended: boolean
}

export interface SessionStreamState {
  turns: TurnState[]
  /** 已收到的最大 seq */
  lastSeq: number
  /** 尚未被 llm.response 取代的 delta（乱序重建时重放） */
  pendingDeltas: EventEnvelope[]
}

export const initialSessionState: SessionStreamState = { turns: [], lastSeq: 0, pendingDeltas: [] }

const NO_TURN = '_'

function emptyMetrics(): TurnMetrics {
  return {
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    latencyMs: null,
    ttftMs: null,
    cost: 0,
    llmCalls: 0,
    toolCalls: 0,
    final: false,
  }
}

function newTurn(id: string): TurnState {
  return {
    id,
    status: 'running',
    input: null,
    inputBlob: null,
    historyLen: 0,
    messages: [],
    steps: [],
    events: [],
    error: null,
    metrics: emptyMetrics(),
    startedAt: null,
    endedAt: null,
    lastEventAt: null,
    ended: false,
  }
}

function newMessage(key: string, stepId: string | null): AssistantMessageView {
  return {
    key,
    stepId,
    content: '',
    streaming: false,
    final: false,
    toolCalls: [],
    llm: null,
    contentBlob: null,
    responded: false,
  }
}

function newToolCall(id: string): ToolCallView {
  return {
    id,
    index: null,
    name: '',
    arguments: undefined,
    argsText: '',
    stepId: null,
    status: 'pending',
    output: undefined,
    hasOutput: false,
    isError: false,
    mocked: false,
    latencyMs: null,
    argsBlob: null,
    outputBlob: null,
  }
}

/** 轮次状态映射：事件里的 completed 对应 Turn.status 的 ok */
export function normalizeTurnStatus(s: string | undefined | null): TurnStatus {
  switch (s) {
    case 'completed':
    case 'ok':
      return 'ok'
    case 'error':
    case 'timeout':
    case 'cancelled':
    case 'running':
      return s
    default:
      return 'error'
  }
}

// ───────────────────────────── 小工具 ─────────────────────────────

/** 模型返回的 JSON 无法解析时保留原字符串 */
function parseArgs(text: string): Record<string, unknown> | string {
  if (!text.trim()) return {}
  try {
    const v: unknown = JSON.parse(text)
    return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : text
  } catch {
    return text
  }
}

export function messageText(m: Message | null | undefined): string {
  if (!m) return ''
  const c = m.content
  if (c === null || c === undefined) return ''
  if (typeof c === 'string') return c
  return c.map((p) => (typeof p.text === 'string' ? p.text : '')).join('')
}

function blobOf(payload: object): string | null {
  const h = (payload as { $blob?: unknown }).$blob
  return typeof h === 'string' ? h : null
}

function replaceAt<T>(list: readonly T[], i: number, item: T): T[] {
  const out = list.slice()
  out[i] = item
  return out
}

// ───────────────────────────── 单个事件作用到轮次 ─────────────────────────────

function findMessageIndex(turn: TurnState, stepId: string | null): number {
  if (stepId) return turn.messages.findIndex((m) => m.stepId === stepId)
  return -1
}

/** 工具调用所属的消息：优先 parent_step_id 对应的 LLM 步骤，其次按 call id 查找，最后落到最后一条消息 */
function locateToolCall(turn: TurnState, callId: string, parentStep: string | null): [number, number] {
  for (let mi = turn.messages.length - 1; mi >= 0; mi--) {
    const ti = turn.messages[mi].toolCalls.findIndex((t) => t.id === callId)
    if (ti >= 0) return [mi, ti]
  }
  const mi = findMessageIndex(turn, parentStep)
  return [mi >= 0 ? mi : -1, -1]
}

function updateToolCall(
  turn: TurnState,
  ev: EventEnvelope,
  callId: string,
  patch: (t: ToolCallView) => ToolCallView,
): TurnState {
  let [mi, ti] = locateToolCall(turn, callId, ev.parent_step_id ?? null)
  let messages = turn.messages
  if (mi < 0) {
    // 没有对应的 LLM 步骤（代码型 agent）：挂到最后一条未收尾的消息，没有就新建一条
    const last = messages.length - 1
    if (last >= 0 && !messages[last].final) mi = last
    else {
      messages = [...messages, newMessage(`x:${messages.length}`, null)]
      mi = messages.length - 1
    }
  }
  const msg = messages[mi]
  let calls = msg.toolCalls
  if (ti < 0) {
    calls = [...calls, newToolCall(callId)]
    ti = calls.length - 1
  }
  calls = replaceAt(calls, ti, patch(calls[ti]))
  return { ...turn, messages: replaceAt(messages, mi, { ...msg, toolCalls: calls }) }
}

function llmInfoFrom(p: LlmResponsePayload, prev: LlmInfo | null): LlmInfo {
  return {
    provider: prev?.provider ?? null,
    model: prev?.model ?? null,
    finishReason: p.finish_reason ?? null,
    promptTokens: p.usage?.prompt_tokens ?? null,
    completionTokens: p.usage?.completion_tokens ?? null,
    latencyMs: p.latency_ms ?? null,
    ttftMs: p.ttft_ms ?? null,
    cost: p.cost ?? null,
  }
}

function applyEventToTurn(turn: TurnState, ev: EventEnvelope): TurnState {
  let t: TurnState = {
    ...turn,
    events: [...turn.events, ev],
    lastEventAt: ev.ts,
    startedAt: turn.startedAt ?? ev.ts,
  }
  switch (ev.type) {
    case 'turn.start': {
      t = {
        ...t,
        input: ev.payload.input ?? t.input,
        inputBlob: ev.payload.input === undefined ? blobOf(ev.payload) : null,
        historyLen: ev.payload.history_len ?? 0,
        startedAt: ev.ts,
      }
      break
    }
    case 'llm.request': {
      const stepId = ev.step_id ?? null
      const i = findMessageIndex(t, stepId)
      const info: LlmInfo = {
        provider: ev.payload.provider ?? null,
        model: ev.payload.model ?? null,
        finishReason: null,
        promptTokens: null,
        completionTokens: null,
        latencyMs: null,
        ttftMs: null,
        cost: null,
      }
      if (i >= 0) {
        const m = t.messages[i]
        t = { ...t, messages: replaceAt(t.messages, i, { ...m, llm: m.llm ?? info, streaming: !m.responded }) }
      } else {
        const m = { ...newMessage(stepId ?? `llm:${t.messages.length}`, stepId), streaming: true, llm: info }
        t = { ...t, messages: [...t.messages, m] }
      }
      break
    }
    case 'llm.response': {
      const p = ev.payload
      const stepId = ev.step_id ?? null
      let i = findMessageIndex(t, stepId)
      let messages = t.messages
      if (i < 0) {
        messages = [...messages, newMessage(stepId ?? `llm:${messages.length}`, stepId)]
        i = messages.length - 1
      }
      const m = messages[i]
      const blob = blobOf(p)
      // 合并工具调用：已有的（按 id 或 index）保留状态，补全名称与参数；响应里没有的增量草稿丢弃
      let calls = m.toolCalls
      if (Array.isArray(p.tool_calls)) {
        const next: ToolCallView[] = []
        p.tool_calls.forEach((tc: ToolCall, idx: number) => {
          const existing =
            calls.find((c) => c.id === tc.id) ??
            calls.find((c) => c.status === 'streaming' && c.index === idx && !next.includes(c))
          const base = existing ?? newToolCall(tc.id)
          next.push({
            ...base,
            id: tc.id,
            index: idx,
            name: tc.name || base.name,
            arguments: tc.arguments ?? base.arguments,
            status: base.status === 'streaming' ? 'pending' : base.status,
          })
        })
        // 已经有结果、却不在响应里的调用（理论上不会出现）保留
        for (const c of calls) if (c.status !== 'streaming' && !next.some((n) => n.id === c.id)) next.push(c)
        calls = next
      } else {
        calls = calls.map((c) => (c.status === 'streaming' ? { ...c, status: 'pending' as const } : c))
      }
      const content = typeof p.content === 'string' ? p.content : m.content
      messages = replaceAt(messages, i, {
        ...m,
        content: blob && typeof p.content !== 'string' ? m.content : content,
        contentBlob: blob && typeof p.content !== 'string' ? blob : null,
        streaming: false,
        responded: true,
        toolCalls: calls,
        llm: llmInfoFrom(p, m.llm),
      })
      const usage = p.usage
      const mt = t.metrics
      const promptTokens = mt.promptTokens + (usage?.prompt_tokens ?? 0)
      const completionTokens = mt.completionTokens + (usage?.completion_tokens ?? 0)
      t = {
        ...t,
        messages,
        metrics: {
          ...mt,
          promptTokens,
          completionTokens,
          totalTokens: promptTokens + completionTokens,
          cost: mt.cost + (p.cost ?? 0),
          llmCalls: mt.llmCalls + 1,
          ttftMs: mt.ttftMs ?? p.ttft_ms ?? null,
        },
      }
      break
    }
    case 'tool.call': {
      const p = ev.payload
      t = updateToolCall(t, ev, p.call_id, (c) => ({
        ...c,
        name: p.name || c.name,
        arguments: p.arguments !== undefined ? p.arguments : c.arguments,
        argsBlob: blobOf(p) && p.arguments === undefined ? blobOf(p) : null,
        stepId: ev.step_id ?? c.stepId,
        status: c.status === 'done' || c.status === 'error' ? c.status : 'running',
      }))
      t = { ...t, metrics: { ...t.metrics, toolCalls: t.metrics.toolCalls + 1 } }
      break
    }
    case 'tool.result': {
      const p = ev.payload
      t = updateToolCall(t, ev, p.call_id, (c) => ({
        ...c,
        name: c.name || p.name,
        stepId: c.stepId ?? ev.step_id ?? null,
        output: p.output,
        hasOutput: p.output !== undefined,
        outputBlob: blobOf(p) && p.output === undefined ? blobOf(p) : null,
        isError: !!p.is_error,
        mocked: !!p.mocked,
        latencyMs: p.latency_ms ?? null,
        status: p.is_error ? 'error' : 'done',
      }))
      break
    }
    case 'message.assistant': {
      const msg = ev.payload.message
      const text = messageText(msg)
      const blob = blobOf(ev.payload)
      const last = t.messages.length - 1
      const lastMsg = last >= 0 ? t.messages[last] : null
      if (lastMsg && !lastMsg.final && lastMsg.toolCalls.length === 0 && lastMsg.responded) {
        // 配置型 agent：最终回复就是最后一次 LLM 调用的内容
        t = { ...t, messages: replaceAt(t.messages, last, { ...lastMsg, final: true }) }
      } else {
        const m: AssistantMessageView = {
          ...newMessage(`final:${t.messages.length}`, null),
          content: text,
          final: true,
          responded: true,
          contentBlob: blob && !msg ? blob : null,
        }
        t = { ...t, messages: [...t.messages, m] }
      }
      break
    }
    case 'error': {
      t = { ...t, error: { code: ev.payload.code, message: ev.payload.message, retriable: !!ev.payload.retriable } }
      break
    }
    case 'turn.end': {
      t = applyTurnEnd(t, ev.payload, ev.ts)
      break
    }
    default:
      break // span.* / log 只进入步骤树
  }
  return { ...t, steps: buildStepTree(t.events) }
}

function applyTurnEnd(t: TurnState, p: TurnEndPayload, ts: string): TurnState {
  const status = normalizeTurnStatus(p.status)
  const u: Usage = p.usage ?? {}
  const prompt = u.prompt_tokens ?? t.metrics.promptTokens
  const completion = u.completion_tokens ?? t.metrics.completionTokens
  // 收尾：未完成的流式消息 / 工具调用标记为中断；没有 message.assistant 时用 turn.end 的 final 补上
  let messages: AssistantMessageView[] = t.messages.map((m) => ({
    ...m,
    streaming: false,
    toolCalls: m.toolCalls.map((c) =>
      c.status === 'streaming' || c.status === 'pending' || c.status === 'running'
        ? { ...c, status: 'interrupted' as const }
        : c,
    ),
  }))
  if (status === 'ok' && !messages.some((m) => m.final) && p.output?.final) {
    const text = messageText(p.output.final)
    const last = messages[messages.length - 1]
    if (last && last.toolCalls.length === 0 && last.responded) {
      messages = replaceAt(messages, messages.length - 1, { ...last, final: true })
    } else {
      messages = [
        ...messages,
        { ...newMessage(`final:${messages.length}`, null), content: text, final: true, responded: true },
      ]
    }
  }
  return {
    ...t,
    status,
    messages,
    ended: true,
    endedAt: ts,
    metrics: {
      promptTokens: prompt,
      completionTokens: completion,
      totalTokens: prompt + completion,
      latencyMs: p.latency_ms ?? null,
      ttftMs: p.ttft_ms ?? t.metrics.ttftMs,
      cost: p.cost ?? t.metrics.cost,
      llmCalls: u.llm_calls ?? t.metrics.llmCalls,
      toolCalls: u.tool_calls ?? t.metrics.toolCalls,
      final: true,
    },
  }
}

function applyDeltaToTurn(turn: TurnState, ev: EventEnvelope): TurnState {
  if (ev.type !== 'llm.delta' || turn.ended) return turn
  const p = ev.payload
  const stepId = p.step_id ?? ev.step_id ?? null
  let i = findMessageIndex(turn, stepId)
  let messages = turn.messages
  if (i < 0) {
    // 没有 step_id 的增量：接到最后一条尚未收尾的 LLM 消息上
    if (!stepId) {
      for (let k = messages.length - 1; k >= 0; k--) {
        if (!messages[k].responded && !messages[k].final) {
          i = k
          break
        }
      }
    }
    if (i < 0) {
      messages = [...messages, { ...newMessage(stepId ?? `llm:${messages.length}`, stepId), streaming: true }]
      i = messages.length - 1
    }
  }
  const m = messages[i]
  if (m.responded) return turn // llm.response 已给出权威内容，迟到的增量忽略
  let next: AssistantMessageView = { ...m, streaming: true }
  if (typeof p.text === 'string' && p.text) next = { ...next, content: m.content + p.text }
  if (p.tool_call_delta) next = { ...next, toolCalls: applyToolCallDelta(next.toolCalls, p.tool_call_delta) }
  return { ...turn, messages: replaceAt(messages, i, next), lastEventAt: ev.ts }
}

/** 按 index 拼接 name / arguments 片段；参数文本能解析成 JSON 对象时 arguments 为对象，否则是原始字符串 */
function applyToolCallDelta(calls: ToolCallView[], d: ToolCallDelta): ToolCallView[] {
  const idx = d.index ?? 0
  let at = calls.findIndex((c) => c.status === 'streaming' && c.index === idx)
  let list = calls
  if (at < 0) {
    list = [...calls, { ...newToolCall(d.id ?? `idx:${idx}`), index: idx, status: 'streaming' }]
    at = list.length - 1
  }
  const c = list[at]
  const raw = c.argsText + (d.arguments ?? '')
  const updated: ToolCallView = {
    ...c,
    id: d.id ?? c.id,
    name: c.name + (d.name ?? ''),
    argsText: raw,
    arguments: raw ? parseArgs(raw) : c.arguments,
  }
  return replaceAt(list, at, updated)
}

// ───────────────────────────── reducer ─────────────────────────────

function turnKey(ev: EventEnvelope): string {
  return ev.turn_id ?? NO_TURN
}

function hasSeq(events: readonly EventEnvelope[], seq: number): boolean {
  let lo = 0
  let hi = events.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const s = events[mid].seq ?? 0
    if (s === seq) return true
    if (s < seq) lo = mid + 1
    else hi = mid - 1
  }
  return false
}

function upsertTurn(turns: TurnState[], id: string, fn: (t: TurnState) => TurnState): TurnState[] {
  const i = turns.findIndex((t) => t.id === id)
  if (i < 0) return [...turns, fn(newTurn(id))]
  return replaceAt(turns, i, fn(turns[i]))
}

/** 轮次内的 LLM 步骤键（delta 的 step_id 在 payload 里，也可能在外层） */
function stepKey(ev: EventEnvelope): string {
  const inner = ev.type === 'llm.delta' ? ev.payload.step_id : null
  return `${turnKey(ev)}\u0000${inner ?? ev.step_id ?? ''}`
}

/** 从全部落库事件重建（乱序事件插入之后使用） */
function rebuild(events: EventEnvelope[], deltas: EventEnvelope[]): SessionStreamState {
  const sorted = events.slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
  let state: SessionStreamState = { ...initialSessionState }
  for (const ev of sorted) state = applyPersisted(state, ev)
  const responded = new Set<string>()
  for (const t of state.turns) for (const m of t.messages) if (m.responded && m.stepId) responded.add(`${t.id}\u0000${m.stepId}`)
  const pending = deltas.filter((d) => !responded.has(stepKey(d)))
  let turns = state.turns
  for (const d of pending) turns = upsertTurn(turns, turnKey(d), (t) => applyDeltaToTurn(t, d))
  return { ...state, turns, pendingDeltas: pending }
}

function applyPersisted(state: SessionStreamState, ev: EventEnvelope): SessionStreamState {
  const turns = upsertTurn(state.turns, turnKey(ev), (t) => applyEventToTurn(t, ev))
  let pendingDeltas = state.pendingDeltas
  if (ev.type === 'llm.response' && pendingDeltas.length > 0) {
    const k = stepKey(ev)
    pendingDeltas = pendingDeltas.filter((d) => stepKey(d) !== k)
  }
  return { turns, pendingDeltas, lastSeq: Math.max(state.lastSeq, ev.seq ?? 0) }
}

/**
 * 会话事件 reducer。事件可以重复、乱序（按 seq 去重 / 重排）；llm.delta 没有 seq，直接合并。
 */
export function sessionReducer(state: SessionStreamState, ev: EventEnvelope): SessionStreamState {
  if (ev.type === 'llm.delta') {
    const id = turnKey(ev)
    const before = state.turns.find((t) => t.id === id)
    const turns = upsertTurn(state.turns, id, (t) => applyDeltaToTurn(t, ev))
    // 轮次已结束 / 该步骤已有 llm.response 时增量被忽略，不记录
    if (before && turns.find((t) => t.id === id) === before) return state
    return { ...state, turns, pendingDeltas: [...state.pendingDeltas, ev] }
  }
  const seq = ev.seq
  if (typeof seq !== 'number') return applyPersisted(state, ev) // 没有 seq 的落库事件（不应出现）：按到达顺序处理
  if (seq > state.lastSeq) return applyPersisted(state, ev)
  // 不大于已收到的最大 seq：重复则忽略，乱序（先前缺失）则插入后重建
  const turn = state.turns.find((t) => t.id === turnKey(ev))
  if (turn && hasSeq(turn.events, seq)) return state
  const all = state.turns.flatMap((t) => t.events)
  return rebuild([...all, ev], state.pendingDeltas)
}

/** 由事件列表直接构建状态（历史回放、测试） */
export function buildSessionState(events: readonly EventEnvelope[]): SessionStreamState {
  return events.reduce(sessionReducer, initialSessionState)
}

// ───────────────────────────── 选择器 ─────────────────────────────

/** 是否有轮次在运行 */
export function isRunning(state: SessionStreamState): boolean {
  return state.turns.some((t) => t.status === 'running' && !t.ended)
}

export function runningTurn(state: SessionStreamState): TurnState | null {
  for (let i = state.turns.length - 1; i >= 0; i--) if (!state.turns[i].ended) return state.turns[i]
  return null
}

export interface TotalMetrics {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  cost: number
  llmCalls: number
  toolCalls: number
  /** 各轮耗时之和（未结束的轮次不计） */
  latencyMs: number
}

/** 全部轮次的累计指标 */
export function totalMetrics(turns: readonly TurnState[]): TotalMetrics {
  const acc: TotalMetrics = { promptTokens: 0, completionTokens: 0, totalTokens: 0, cost: 0, llmCalls: 0, toolCalls: 0, latencyMs: 0 }
  for (const t of turns) {
    acc.promptTokens += t.metrics.promptTokens
    acc.completionTokens += t.metrics.completionTokens
    acc.totalTokens += t.metrics.totalTokens
    acc.cost += t.metrics.cost
    acc.llmCalls += t.metrics.llmCalls
    acc.toolCalls += t.metrics.toolCalls
    acc.latencyMs += t.metrics.latencyMs ?? 0
  }
  return acc
}

/**
 * 高亮集合里的元素写法：`s2`（所有轮次中 step_id 为 s2 的步骤）或 `trn_xxx:s2`（限定轮次）。
 * step_id 在每一轮里都从 s1 重新计数，所以双栏对比时建议用带轮次的写法。
 */
export function highlightKey(turnId: string, stepId: string): string {
  return `${turnId}:${stepId}`
}

export function isHighlighted(set: ReadonlySet<string> | undefined, turnId: string, stepId: string | null): boolean {
  if (!set || set.size === 0 || !stepId) return false
  return set.has(stepId) || set.has(highlightKey(turnId, stepId))
}
