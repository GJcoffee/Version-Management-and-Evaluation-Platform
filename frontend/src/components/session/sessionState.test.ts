import { describe, expect, it } from 'vitest'
import {
  buildSessionState,
  highlightKey,
  initialSessionState,
  isHighlighted,
  isRunning,
  normalizeTurnStatus,
  sessionReducer,
  totalMetrics,
} from './sessionState'
import { mk, toolTurnEvents } from './testEvents'

const fold = (events: ReturnType<typeof mk>[]) => events.reduce(sessionReducer, initialSessionState)

describe('sessionReducer：完整的一轮', () => {
  const state = buildSessionState(toolTurnEvents())
  const turn = state.turns[0]

  it('还原轮次、用户输入与状态', () => {
    expect(state.turns).toHaveLength(1)
    expect(turn.id).toBe('trn_1')
    expect(turn.status).toBe('ok') // 事件里的 completed 映射为 ok
    expect(turn.input?.content).toBe('查订单 A001')
    expect(turn.ended).toBe(true)
    expect(state.lastSeq).toBe(9)
    expect(isRunning(state)).toBe(false)
  })

  it('按 LLM 步骤还原消息，工具调用挂在发起它的消息下', () => {
    expect(turn.messages.map((m) => m.stepId)).toEqual(['s1', 's3'])
    const [m1, m2] = turn.messages
    expect(m1.toolCalls).toHaveLength(1)
    expect(m1.toolCalls[0]).toMatchObject({
      id: 'call_1',
      name: 'query_order',
      arguments: { order_id: 'A001' },
      stepId: 's2',
      status: 'done',
      mocked: true,
      isError: false,
      latencyMs: 50,
      output: { status: '已发货' },
    })
    expect(m2.content).toBe('订单已发货')
    expect(m2.final).toBe(true)
    expect(m1.final).toBe(false)
    expect(m2.streaming).toBe(false)
  })

  it('turn.end 的指标为权威值', () => {
    expect(turn.metrics).toMatchObject({
      promptTokens: 60,
      completionTokens: 13,
      totalTokens: 73,
      llmCalls: 2,
      toolCalls: 1,
      latencyMs: 575,
      ttftMs: 120,
      cost: 0.003,
      final: true,
    })
  })

  it('同时生成步骤树：llm 下挂着它发起的工具调用', () => {
    expect(turn.steps.map((n) => n.id)).toEqual(['s1', 's3', expect.stringContaining('message.assistant')])
    expect(turn.steps[0].children.map((c) => c.id)).toEqual(['s2'])
    expect(turn.events).toHaveLength(9)
  })
})

describe('sessionReducer：去重与乱序', () => {
  it('重复的 seq 被忽略（状态引用不变）', () => {
    const events = toolTurnEvents()
    const once = fold(events)
    const twice = events.reduce(sessionReducer, once) // 全部再来一遍
    expect(twice).toBe(once)
  })

  it('乱序到达的事件按 seq 插入并重建', () => {
    const events = toolTurnEvents()
    const shuffled = [events[0], events[1], events[2], events[5], events[6], events[3], events[4], events[7], events[8]]
    const a = fold(shuffled)
    const b = fold(events)
    expect(a.turns[0].messages).toEqual(b.turns[0].messages)
    expect(a.turns[0].events.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(a.turns[0].metrics).toEqual(b.turns[0].metrics)
    expect(a.turns[0].status).toBe('ok')
    expect(a.lastSeq).toBe(9)
  })

  it('先到的 turn.end、后到的更早事件也能还原', () => {
    const events = toolTurnEvents()
    const reversed = [...events].reverse()
    const s = fold(reversed)
    expect(s.turns[0].messages.map((m) => m.stepId)).toEqual(['s1', 's3'])
    expect(s.turns[0].messages[0].toolCalls[0].status).toBe('done')
    expect(s.turns[0].input?.content).toBe('查订单 A001')
  })

  it('多轮按首次出现顺序排列，互不干扰', () => {
    const s = fold([...toolTurnEvents('trn_a', 1), ...toolTurnEvents('trn_b', 10)])
    expect(s.turns.map((t) => t.id)).toEqual(['trn_a', 'trn_b'])
    expect(s.turns[1].metrics.totalTokens).toBe(73)
  })
})

describe('sessionReducer：llm.delta 合并', () => {
  const start = mk('turn.start', { input: { role: 'user', content: '你好' }, history_len: 0 }, { seq: 1 })
  const req = mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1', at: 5 })
  const delta = (text: string) => mk('llm.delta', { step_id: 's1', text }, { step: 's1' })

  it('文本增量逐步拼接，流式标记在 llm.response 后消失', () => {
    let s = fold([start, req, delta('你'), delta('好，'), delta('世界')])
    expect(s.turns[0].messages).toHaveLength(1)
    expect(s.turns[0].messages[0]).toMatchObject({ content: '你好，世界', streaming: true, responded: false })
    expect(isRunning(s)).toBe(true)
    // 增量没有 seq：不推进 lastSeq
    expect(s.lastSeq).toBe(2)

    s = sessionReducer(
      s,
      mk(
        'llm.response',
        { content: '你好，世界！', tool_calls: [], finish_reason: 'stop', usage: { prompt_tokens: 3, completion_tokens: 4 }, latency_ms: 100, ttft_ms: 20, cost: 0.1 },
        { seq: 3, step: 's1', at: 100 },
      ),
    )
    expect(s.turns[0].messages[0]).toMatchObject({ content: '你好，世界！', streaming: false, responded: true })
    expect(s.pendingDeltas).toHaveLength(0)
    // 迟到的增量被忽略
    const late = sessionReducer(s, delta('多余'))
    expect(late).toBe(s)
    expect(late.turns[0].messages[0].content).toBe('你好，世界！')
  })

  it('delta 先于 llm.request 到达时也会建出消息', () => {
    const s = fold([delta('先到')])
    expect(s.turns[0].messages[0]).toMatchObject({ stepId: 's1', content: '先到', streaming: true })
    const s2 = sessionReducer(s, req)
    expect(s2.turns[0].messages).toHaveLength(1)
    expect(s2.turns[0].messages[0].llm?.model).toBe('m')
  })

  it('乱序重建时保留还没被 llm.response 取代的增量', () => {
    let s = fold([start, delta('流式'), delta('中')])
    // seq=2 的 llm.request 迟到：触发重建（lastSeq 已是 1，这里 2>1 属于顺序到达；改用 seq=0 的乱序）
    s = sessionReducer(s, mk('log', { level: 'info', message: 'x' }, { seq: 5 }))
    s = sessionReducer(s, req) // seq 2 < lastSeq 5：乱序
    expect(s.turns[0].messages).toHaveLength(1)
    expect(s.turns[0].messages[0]).toMatchObject({ content: '流式中', streaming: true })
  })

  it('tool_call 增量按 index 拼接名称与参数，解析成功后成为对象', () => {
    const d = (tc: Record<string, unknown>) => mk('llm.delta', { step_id: 's1', tool_call_delta: tc }, { step: 's1' })
    let s = fold([start, req, d({ index: 0, id: 'call_9', name: 'get_' }), d({ index: 0, name: 'weather' }), d({ index: 0, arguments: '{"city":' })])
    let c = s.turns[0].messages[0].toolCalls[0]
    expect(c).toMatchObject({ id: 'call_9', name: 'get_weather', status: 'streaming', arguments: '{"city":' })
    s = sessionReducer(s, d({ index: 0, arguments: '"北京"}' }))
    c = s.turns[0].messages[0].toolCalls[0]
    expect(c.arguments).toEqual({ city: '北京' })
    // 第二个并行调用
    s = sessionReducer(s, d({ index: 1, id: 'call_10', name: 'other', arguments: '{}' }))
    expect(s.turns[0].messages[0].toolCalls.map((x) => x.name)).toEqual(['get_weather', 'other'])
  })

  it('llm.response 之后，增量草稿被正式的 tool_calls 取代', () => {
    const d = (tc: Record<string, unknown>) => mk('llm.delta', { step_id: 's1', tool_call_delta: tc }, { step: 's1' })
    let s = fold([start, req, d({ index: 0, id: 'call_9', name: 'lookup', arguments: '{"q":"a' })])
    s = sessionReducer(
      s,
      mk(
        'llm.response',
        { content: null, tool_calls: [{ id: 'call_9', name: 'lookup', arguments: { q: 'abc' } }], finish_reason: 'tool_calls', usage: { prompt_tokens: 1, completion_tokens: 1 }, latency_ms: 1, ttft_ms: 1, cost: 0 },
        { seq: 3, step: 's1' },
      ),
    )
    const calls = s.turns[0].messages[0].toolCalls
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ id: 'call_9', arguments: { q: 'abc' }, status: 'pending' })
  })
})

describe('sessionReducer：错误、取消、超时', () => {
  const head = [
    mk('turn.start', { input: { role: 'user', content: 'hi' }, history_len: 0 }, { seq: 1 }),
    mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
    mk('llm.delta', { step_id: 's1', text: '部分' }, { step: 's1' }),
    mk('llm.delta', { step_id: 's1', tool_call_delta: { index: 0, id: 'c1', name: 'slow' } }, { step: 's1' }),
  ]

  it('error 事件记录错误，turn.end(error) 收尾并把未完成的流式内容/工具调用标为中断', () => {
    const s = fold([
      ...head,
      mk('error', { code: 'LLM_UNAVAILABLE', message: '模型服务不可用', retriable: true }, { seq: 3 }),
      mk('turn.end', { status: 'error', output: null, usage: { prompt_tokens: 0, completion_tokens: 0, llm_calls: 0, tool_calls: 0 }, cost: 0, latency_ms: 80, ttft_ms: null }, { seq: 4 }),
    ])
    const t = s.turns[0]
    expect(t.status).toBe('error')
    expect(t.error).toEqual({ code: 'LLM_UNAVAILABLE', message: '模型服务不可用', retriable: true })
    expect(t.messages[0].streaming).toBe(false)
    expect(t.messages[0].content).toBe('部分') // 已生成的内容保留
    expect(t.messages[0].toolCalls[0].status).toBe('interrupted')
    expect(t.metrics.final).toBe(true)
    expect(isRunning(s)).toBe(false)
  })

  it('取消：状态为 cancelled，没有错误', () => {
    const s = fold([
      ...head,
      mk('tool.call', { call_id: 'c1', name: 'slow', arguments: {} }, { seq: 3, step: 's2', parent: 's1' }),
      mk('turn.end', { status: 'cancelled', output: null, usage: { llm_calls: 1, tool_calls: 1 }, cost: 0, latency_ms: 50, ttft_ms: null }, { seq: 4 }),
    ])
    const t = s.turns[0]
    expect(t.status).toBe('cancelled')
    expect(t.error).toBeNull()
    expect(t.messages[0].toolCalls[0]).toMatchObject({ status: 'interrupted', name: 'slow' })
    // 步骤树里未结束的步骤标为 incomplete
    expect(t.steps[0].status).toBe('incomplete')
  })

  it('取消 / 出错后刷新（没有 llm.delta）：用 turn.end.partial_text 还原已流出的文本', () => {
    const end = (status: string, extra: Record<string, unknown> = {}) =>
      mk('turn.end', { status, output: null, usage: null, cost: 0, latency_ms: 5, ttft_ms: null, ...extra }, { seq: 4 })
    const base = [
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
    ]
    const cancelled = fold([...base, end('cancelled', { partial_text: '写到一半' })]).turns[0]
    expect(cancelled.messages).toHaveLength(1)
    expect(cancelled.messages[0]).toMatchObject({ content: '写到一半', partial: true, streaming: false, final: false })
    // 出错同样；没有 partial_text 时不产生空消息
    const errored = fold([...base, end('error', { partial_text: '前半' })]).turns[0]
    expect(errored.messages[0]).toMatchObject({ content: '前半', partial: true })
    const none = fold([...base, end('cancelled')]).turns[0]
    expect(none.messages[0]).toMatchObject({ content: '', partial: false })
    // 没有 llm.request 的消息（代码型 agent）：追加一条部分消息
    const code = fold([base[0], end('cancelled', { partial_text: '外部 agent 的输出' })]).turns[0]
    expect(code.messages.map((m) => [m.content, m.partial])).toEqual([['外部 agent 的输出', true]])
    // 正常结束不受影响
    const ok = fold([...base, end('completed', { partial_text: '不应使用' })]).turns[0]
    expect(ok.messages.every((m) => !m.partial)).toBe(true)
  })

  it('实时观看时已有的 delta 文本保留，并标为部分内容', () => {
    const t = fold([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('llm.request', { provider: 'p', model: 'm', params: {}, messages: [], tools: null }, { seq: 2, step: 's1' }),
      mk('llm.delta', { step_id: 's1', text: '已经流出的内容' }, { step: 's1' }),
      mk('turn.end', { status: 'cancelled', output: null, usage: null, cost: 0, latency_ms: 5, ttft_ms: null, partial_text: '已经流出' }, { seq: 3 }),
    ]).turns[0]
    expect(t.messages[0]).toMatchObject({ content: '已经流出的内容', partial: true })
  })

  it('超时：turn.end(timeout)', () => {
    const s = fold([
      ...head,
      mk('error', { code: 'TIMEOUT', message: '超过 180s', retriable: true }, { seq: 3 }),
      mk('turn.end', { status: 'timeout', output: null, usage: null, cost: null, latency_ms: 180000, ttft_ms: null }, { seq: 4 }),
    ])
    expect(s.turns[0].status).toBe('timeout')
    expect(s.turns[0].error?.code).toBe('TIMEOUT')
    expect(s.turns[0].metrics.latencyMs).toBe(180000)
  })

  it('工具返回错误：卡片标记 error', () => {
    const s = fold([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('tool.call', { call_id: 'c1', name: 't', arguments: {} }, { seq: 2, step: 's2' }),
      mk('tool.result', { call_id: 'c1', name: 't', output: 'boom', is_error: true, latency_ms: 3, mocked: false }, { seq: 3, step: 's2' }),
    ])
    const c = s.turns[0].messages[0].toolCalls[0]
    expect(c).toMatchObject({ status: 'error', isError: true, output: 'boom' })
  })
})

describe('sessionReducer：代码型 agent（没有 llm 事件）与 blob 存根', () => {
  it('只有 message.assistant 时生成最终回复消息', () => {
    const s = fold([
      mk('turn.start', { input: { role: 'user', content: 'q' }, history_len: 0 }, { seq: 1 }),
      mk('span.start', { name: 'retrieve', attrs: {} }, { seq: 2, step: 'x1' }),
      mk('span.end', { name: 'retrieve', attrs: {}, latency_ms: 12 }, { seq: 3, step: 'x1', at: 12 }),
      mk('message.assistant', { message: { role: 'assistant', content: '答案' } }, { seq: 4 }),
      mk('turn.end', { status: 'completed', output: null, usage: null, cost: null, latency_ms: 20, ttft_ms: null }, { seq: 5 }),
    ])
    const t = s.turns[0]
    expect(t.messages).toHaveLength(1)
    expect(t.messages[0]).toMatchObject({ content: '答案', final: true, stepId: null })
    expect(t.steps.map((n) => n.kind)).toEqual(['span', 'final'])
  })

  it('没有 message.assistant 时，用 turn.end 的 output.final 补上', () => {
    const s = fold([
      mk('turn.start', { input: { role: 'user', content: 'q' }, history_len: 0 }, { seq: 1 }),
      mk('turn.end', { status: 'completed', output: { final: { role: 'assistant', content: '兜底' }, messages: [] }, usage: null, cost: 0, latency_ms: 1, ttft_ms: null }, { seq: 2 }),
    ])
    expect(s.turns[0].messages[0]).toMatchObject({ content: '兜底', final: true })
  })

  it('turn.start 被转存为 blob（输入很大）时记录哈希', () => {
    const s = fold([mk('turn.start', { $blob: 'd'.repeat(64), history_len: 3 }, { seq: 1 })])
    expect(s.turns[0]).toMatchObject({ input: null, inputBlob: 'd'.repeat(64), historyLen: 3 })
  })

  it('被转存为 blob 的 payload 记录哈希', () => {
    const s = fold([
      mk('turn.start', { input: { role: 'user', content: 'x' }, history_len: 0 }, { seq: 1 }),
      mk('tool.call', { $blob: 'a'.repeat(64), call_id: 'c1', name: 'big' }, { seq: 2, step: 's2' }),
      mk('tool.result', { $blob: 'b'.repeat(64), call_id: 'c1', name: 'big', is_error: false, latency_ms: 1, mocked: false }, { seq: 3, step: 's2' }),
    ])
    const c = s.turns[0].messages[0].toolCalls[0]
    expect(c.argsBlob).toBe('a'.repeat(64))
    expect(c.outputBlob).toBe('b'.repeat(64))
    expect(c.hasOutput).toBe(false)
  })
})

describe('实时指标与选择器', () => {
  it('运行中从 llm.response 实时累计 tokens / 成本 / 调用次数', () => {
    const events = toolTurnEvents().slice(0, 7) // 到第二个 llm.response 为止，没有 turn.end
    const s = fold(events)
    expect(s.turns[0].metrics).toMatchObject({ promptTokens: 60, completionTokens: 13, llmCalls: 2, toolCalls: 1, final: false, latencyMs: null })
    expect(s.turns[0].metrics.cost).toBeCloseTo(0.003)
    expect(isRunning(s)).toBe(true)
  })

  it('totalMetrics 汇总各轮', () => {
    const s = fold([...toolTurnEvents('a', 1), ...toolTurnEvents('b', 10)])
    expect(totalMetrics(s.turns)).toMatchObject({ totalTokens: 146, llmCalls: 4, toolCalls: 2, latencyMs: 1150 })
  })

  it('normalizeTurnStatus', () => {
    expect(normalizeTurnStatus('completed')).toBe('ok')
    expect(normalizeTurnStatus('cancelled')).toBe('cancelled')
    expect(normalizeTurnStatus('weird')).toBe('error')
  })

  it('isHighlighted：支持纯 step_id 与带轮次的写法', () => {
    const set = new Set(['s2', highlightKey('trn_9', 's5')])
    expect(isHighlighted(set, 'trn_1', 's2')).toBe(true)
    expect(isHighlighted(set, 'trn_9', 's5')).toBe(true)
    expect(isHighlighted(set, 'trn_1', 's5')).toBe(false)
    expect(isHighlighted(set, 'trn_1', null)).toBe(false)
    expect(isHighlighted(undefined, 'trn_1', 's2')).toBe(false)
  })
})
