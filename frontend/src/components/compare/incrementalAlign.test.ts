import { describe, expect, it } from 'vitest'
import type { AlignStep } from '@/api/compareTypes'
import { buildSessionState } from '@/components/session'
import { mk, toolTurnEvents } from '@/components/session/testEvents'
import parity from './incrementalAlign.parity.json'
import { alignSteps, extractSteps, jsonDiff, liveAlignTurn, matchingBlocks, stepSignature } from './incrementalAlign'
import { alignmentHighlights, summarizeSteps } from './compareUtils'

// incrementalAlign.parity.json 由后端 evalp.compare.align 生成（align_steps / json_diff 的真实输出），
// 这里要求前端实现逐项一致；改动对齐算法时两边需一起更新并重新生成。
describe('与后端对齐算法一致', () => {
  it.each(parity.align.map((c, i) => [i, c] as const))('步骤对齐用例 #%i', (_i, c) => {
    const l = c.left.map((sig, i) => ({ id: `l${i}`, sig }))
    const r = c.right.map((sig, i) => ({ id: `r${i}`, sig }))
    expect(alignSteps(l, r)).toEqual(c.steps as AlignStep[])
  })

  it.each(parity.jsondiff.map((c, i) => [i, c] as const))('JSON diff 用例 #%i', (_i, c) => {
    expect(jsonDiff(c.left, c.right)).toEqual(c.diff)
  })
})

describe('matchingBlocks', () => {
  it('取最长连续匹配并递归两侧；相邻的块合并', () => {
    expect(matchingBlocks(['a', 'b', 'c'], ['a', 'b', 'c'])).toEqual([[0, 0, 3]])
    expect(matchingBlocks([], ['a'])).toEqual([])
    // 最长连续匹配 [llm, final]，左边多出的 llm 不与右边前面的 llm/tool 对齐
    expect(matchingBlocks(['llm', 'final'], ['llm', 'tool:q', 'llm', 'final'])).toEqual([[0, 2, 2]])
  })
})

describe('步骤提取', () => {
  it('签名只取 llm.response / tool.call / span.start / message.assistant', () => {
    expect(stepSignature({ type: 'llm.response', payload: {} as never })).toBe('llm')
    expect(stepSignature({ type: 'tool.call', payload: { name: 'q' } as never })).toBe('tool:q')
    expect(stepSignature({ type: 'span.start', payload: { name: 'rag' } as never })).toBe('span:rag')
    expect(stepSignature({ type: 'message.assistant', payload: {} as never })).toBe('final')
    expect(stepSignature({ type: 'llm.request', payload: {} as never })).toBeNull()
  })

  it('没有 step_id 的步骤：final 记为 final，其余 e{seq}', () => {
    const steps = extractSteps([
      mk('tool.call', { call_id: 'c', name: 'q', arguments: { a: 1 } }, { seq: 4, step: null }),
      mk('message.assistant', { content: '好' }, { seq: 5, step: null }),
    ])
    expect(steps).toEqual([
      { id: 'e4', sig: 'tool:q', args: { a: 1 } },
      { id: 'final', sig: 'final', args: undefined },
    ])
  })
})

describe('liveAlignTurn：运行中的增量对齐', () => {
  const full = buildSessionState(toolTurnEvents('trn_L')).turns[0]

  it('一侧已完成、另一侧才收到第一个 llm.response：已有的步骤先对上，其余算左侧独有', () => {
    const partial = buildSessionState(toolTurnEvents('trn_R').slice(0, 3)).turns[0] // 只到第一次 llm.response
    expect(partial.ended).toBe(false)
    const a = liveAlignTurn(0, 'trn_L', 'trn_R', full, partial)
    expect(a.steps.map((s) => `${s.op}:${s.sig}`)).toEqual(['equal:llm', 'delete:tool:query_order', 'delete:llm', 'delete:final'])
    expect(a.status).toEqual({ left: 'ok', right: 'running' })
    expect(summarizeSteps(a)).toContain('左侧多调用 query_order ×1')
    expect(alignmentHighlights({ turns: [a] }).left.has('trn_L:s2')).toBe(true)
  })

  it('随着右侧事件增多，对齐逐步收敛到完全一致', () => {
    const events = toolTurnEvents('trn_R')
    const ops = (n: number) =>
      liveAlignTurn(0, 'trn_L', 'trn_R', full, buildSessionState(events.slice(0, n)).turns[0]).steps.filter((s) => s.op !== 'equal').length
    const counts = [3, 4, events.length].map(ops)
    expect(counts[0]).toBeGreaterThan(counts[1])
    expect(counts[2]).toBe(0)
  })

  it('同名工具参数不同 -> tool_args_diff；一侧还没有轮次 -> 该侧为空', () => {
    const other = buildSessionState(
      toolTurnEvents('trn_R').map((e) =>
        e.type === 'tool.call' ? ({ ...e, payload: { ...e.payload, arguments: { order_id: 'A002' } } } as typeof e) : e,
      ),
    ).turns[0]
    const a = liveAlignTurn(0, 'trn_L', 'trn_R', full, other)
    expect(a.tool_args_diff).toHaveLength(1)
    expect(a.tool_args_diff[0].changes[0]).toMatchObject({ path: 'order_id', left: 'A001', right: 'A002' })
    const one = liveAlignTurn(0, 'trn_L', null, full, undefined)
    expect(one.steps.every((s) => s.op === 'delete')).toBe(true)
    expect(one.metrics.right).toBeNull()
  })
})
