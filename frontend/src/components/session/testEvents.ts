import type { EventEnvelope, EventType } from '@/api/types'

/** 测试用：构造事件。ts 默认为基准时间 + offsetMs */
export const T0 = Date.parse('2026-10-26T08:00:00.000Z')

interface Opts {
  seq?: number | null
  turn?: string
  step?: string | null
  parent?: string | null
  at?: number
}

export function mk(type: EventType, payload: Record<string, unknown>, o: Opts = {}): EventEnvelope {
  return {
    seq: o.seq === undefined ? null : o.seq,
    turn_id: o.turn ?? 'trn_1',
    ts: new Date(T0 + (o.at ?? 0)).toISOString(),
    type,
    step_id: o.step ?? null,
    parent_step_id: o.parent ?? null,
    payload,
  } as EventEnvelope
}

/** 一轮完整的“LLM -> 工具 -> LLM -> 最终回复”事件序列（seq 从 start 开始） */
export function toolTurnEvents(turn = 'trn_1', start = 1): EventEnvelope[] {
  const s = (n: number) => start + n
  return [
    mk('turn.start', { input: { role: 'user', content: '查订单 A001' }, history_len: 0 }, { seq: s(0), turn, at: 0 }),
    mk('llm.request', { provider: 'fake', model: 'fake-model', params: {}, messages: [], tools: [] }, { seq: s(1), turn, step: 's1', at: 10 }),
    mk(
      'llm.response',
      {
        content: null,
        tool_calls: [{ id: 'call_1', name: 'query_order', arguments: { order_id: 'A001' } }],
        finish_reason: 'tool_calls',
        usage: { prompt_tokens: 20, completion_tokens: 5 },
        latency_ms: 300,
        ttft_ms: 120,
        cost: 0.001,
      },
      { seq: s(2), turn, step: 's1', at: 310 },
    ),
    mk('tool.call', { call_id: 'call_1', name: 'query_order', arguments: { order_id: 'A001' } }, { seq: s(3), turn, step: 's2', parent: 's1', at: 315 }),
    mk(
      'tool.result',
      { call_id: 'call_1', name: 'query_order', output: { status: '已发货' }, is_error: false, latency_ms: 50, mocked: true },
      { seq: s(4), turn, step: 's2', parent: 's1', at: 365 },
    ),
    mk('llm.request', { provider: 'fake', model: 'fake-model', params: {}, messages: [], tools: [] }, { seq: s(5), turn, step: 's3', at: 370 }),
    mk(
      'llm.response',
      {
        content: '订单已发货',
        tool_calls: [],
        finish_reason: 'stop',
        usage: { prompt_tokens: 40, completion_tokens: 8 },
        latency_ms: 200,
        ttft_ms: 80,
        cost: 0.002,
      },
      { seq: s(6), turn, step: 's3', at: 570 },
    ),
    mk('message.assistant', { message: { role: 'assistant', content: '订单已发货' } }, { seq: s(7), turn, at: 571 }),
    mk(
      'turn.end',
      {
        status: 'completed',
        output: { final: { role: 'assistant', content: '订单已发货' }, messages: [] },
        usage: { prompt_tokens: 60, completion_tokens: 13, llm_calls: 2, tool_calls: 1 },
        cost: 0.003,
        latency_ms: 575,
        ttft_ms: 120,
      },
      { seq: s(8), turn, at: 575 },
    ),
  ]
}
