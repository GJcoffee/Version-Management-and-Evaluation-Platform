import { describe, expect, it } from 'vitest'
import type { EvalItemOut } from '@/api/evalTypes'
import { evalStreamReducer, initialEvalStreamState } from './useEvalStream'

const item = (id: string, over: Partial<EvalItemOut> = {}): EvalItemOut => ({
  id,
  run_id: 'evr_1',
  case_id: 'cas_1',
  case_key: 'k1',
  repeat_idx: 0,
  input_preview: 'hi',
  output_preview: null,
  expected: null,
  tags: [],
  status: 'done',
  passed: true,
  score: 1,
  cached: false,
  attempts: 1,
  error: null,
  latency_ms: 100,
  cost: 0.01,
  usage: null,
  session_id: 'ses_1',
  scores: [],
  ...over,
})

describe('evalStreamReducer', () => {
  it('eval.status 更新状态、统计、进度', () => {
    const s = evalStreamReducer(initialEvalStreamState, {
      type: 'eval.status',
      run_id: 'evr_1',
      status: 'running',
      stats: null,
      progress: { total: 4, done: 1, failed: 0 },
    })
    expect(s.status).toBe('running')
    expect(s.progress?.total).toBe(4)
  })

  it('eval.progress 只更新进度', () => {
    const s0 = evalStreamReducer(initialEvalStreamState, { type: 'eval.status', run_id: 'r', status: 'running', stats: null })
    const s = evalStreamReducer(s0, { type: 'eval.progress', run_id: 'r', progress: { total: 4, done: 3, failed: 0 } })
    expect(s.status).toBe('running')
    expect(s.progress?.done).toBe(3)
  })

  it('eval.item_done 按 id 覆盖并计数', () => {
    let s = evalStreamReducer(initialEvalStreamState, { type: 'eval.item_done', run_id: 'r', item: item('evi_1', { passed: false }) })
    s = evalStreamReducer(s, { type: 'eval.item_done', run_id: 'r', item: item('evi_1', { passed: true }) })
    s = evalStreamReducer(s, { type: 'eval.item_done', run_id: 'r', item: item('evi_2') })
    expect(Object.keys(s.items)).toEqual(['evi_1', 'evi_2'])
    expect(s.items.evi_1.passed).toBe(true)
    expect(s.itemDoneCount).toBe(3)
  })

  it('暂停原因：status=paused 带 pause_reason，继续后清空；budget_exceeded 事件也会置为 budget', () => {
    let s = evalStreamReducer(initialEvalStreamState, { type: 'eval.status', run_id: 'r', status: 'paused', pause_reason: 'manual', stats: null })
    expect(s.pauseReason).toBe('manual')
    s = evalStreamReducer(s, { type: 'eval.status', run_id: 'r', status: 'running', pause_reason: null, stats: null })
    expect(s.pauseReason).toBeNull()
    s = evalStreamReducer(s, { type: 'eval.budget_exceeded', run_id: 'r', max_cost: 1, cost: 1.2 })
    expect(s.pauseReason).toBe('budget')
    s = evalStreamReducer(s, { type: 'eval.status', run_id: 'r', status: 'paused', pause_reason: 'budget', stats: null })
    expect(s.pauseReason).toBe('budget')
    // 后续没有 pause_reason 的暂停快照不应丢掉已知原因
    s = evalStreamReducer(s, { type: 'eval.status', run_id: 'r', status: 'paused', stats: null })
    expect(s.pauseReason).toBe('budget')
  })
})
