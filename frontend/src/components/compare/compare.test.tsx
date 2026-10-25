import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { AlignTurn, Alignment } from '@/api/compareTypes'
import { buildSessionState } from '@/components/session'
import { toolTurnEvents } from '@/components/session/testEvents'
import { AlignmentPanel } from './AlignmentPanel'
import { CompareRowView } from './CompareRowView'
import {
  alignmentHighlights,
  buildRows,
  charDiff,
  describeArgsDiff,
  metricDelta,
  rowHighlights,
  summarizeSteps,
} from './compareUtils'

const metrics = { latency_ms: 100, ttft_ms: 10, tokens: 5, cost: 0.1, llm_calls: 1, tool_calls: 0 }

const align: AlignTurn = {
  pair: 0,
  left_turn_id: 'trn_L',
  right_turn_id: 'trn_R',
  steps: [
    { op: 'equal', left: 's1', right: 's1', sig: 'llm' },
    { op: 'insert', right: 's2', sig: 'tool:knowledge_search' },
    { op: 'equal', left: 's2', right: 's3', sig: 'tool:query_order' },
    { op: 'delete', left: 's3', sig: 'tool:old_tool' },
    { op: 'equal', left: 's4', right: 's4', sig: 'llm' },
  ],
  tool_args_diff: [{ left: 's2', right: 's3', changes: [{ path: 'order_id', left: 'A1', right: 'A2', op: 'change' }] }],
  metrics: { left: metrics, right: metrics },
  final_text: { left: '订单已发货', right: '订单已经发货了' },
}

describe('buildRows', () => {
  it('行来自对齐结果，并补上本地刚发送、对齐结果里还没有的 pair', () => {
    const a: Alignment = {
      turns: [
        { ...align, pair: 2 },
        { ...align, pair: 0, left_turn_id: 'a', right_turn_id: null },
      ],
    }
    const rows = buildRows(a, [
      { pair: 3, leftTurnId: 'l3', rightTurnId: 'r3' },
      { pair: 0, leftTurnId: 'a', rightTurnId: 'r0' }, // 对齐结果里右侧还没有：用本地的
    ])
    expect(rows.map((r) => r.pair)).toEqual([0, 2, 3])
    expect(rows[0]).toMatchObject({ leftTurnId: 'a', rightTurnId: 'r0' })
    expect(rows[2]).toMatchObject({ leftTurnId: 'l3', rightTurnId: 'r3' })
    expect(rows[2].align).toBeUndefined()
  })
})

describe('对齐高亮与摘要', () => {
  it('高亮 key 由 turn_id 与 step_id 组合；delete 在左、insert 在右、参数不同两侧都标', () => {
    expect(rowHighlights(align)).toEqual({
      left: ['trn_L:s3', 'trn_L:s2'],
      right: ['trn_R:s2', 'trn_R:s3'],
    })
    const h = alignmentHighlights({ turns: [align] })
    expect(h.left.has('trn_L:s3')).toBe(true)
    expect(h.right.has('trn_R:s2')).toBe(true)
  })

  it('单侧轮次不产生另一侧的高亮', () => {
    const one: AlignTurn = { ...align, right_turn_id: null, steps: [{ op: 'delete', left: 's1', sig: 'llm' }], tool_args_diff: [] }
    expect(rowHighlights(one)).toEqual({ left: ['trn_L:s1'], right: [] })
  })

  it('摘要：右侧多调用 / 左侧多调用 / 步骤一致 / 仅一侧', () => {
    expect(summarizeSteps(align)).toEqual(['左侧多调用 old_tool ×1', '右侧多调用 knowledge_search ×1'])
    expect(summarizeSteps({ ...align, steps: [{ op: 'equal', left: 's1', right: 's1', sig: 'llm' }] })).toEqual([])
    expect(
      summarizeSteps({
        ...align,
        steps: [
          { op: 'insert', right: 's1', sig: 'llm' },
          { op: 'insert', right: 's2', sig: 'llm' },
        ],
      }),
    ).toEqual(['右侧多 2 次 LLM 调用'])
    expect(summarizeSteps({ ...align, right_turn_id: null })).toEqual(['仅左侧有此轮'])
  })

  it('参数差异描述带上工具名', () => {
    expect(describeArgsDiff(align)).toEqual(['query_order 的参数 order_id："A1" → "A2"'])
  })
})

describe('metricDelta', () => {
  it('次数少/多只标差值；耗时类右侧更低为 better', () => {
    expect(metricDelta(100, 80, true)).toMatchObject({ delta: -20, trend: 'better' })
    expect(metricDelta(100, 130, true)).toMatchObject({ trend: 'worse', ratio: 0.3 })
    expect(metricDelta(1, 2, false)?.trend).toBe('neutral')
    expect(metricDelta(3, 3, true)?.trend).toBe('same')
    expect(metricDelta(null, 3, true)).toBeNull()
  })
})

describe('charDiff', () => {
  it('按字符 diff 中文，拼接后可还原两侧文本', () => {
    const d = charDiff('订单已发货', '订单已经发货了')
    expect(d.filter((p) => p.op !== 'insert').map((p) => p.text).join('')).toBe('订单已发货')
    expect(d.filter((p) => p.op !== 'delete').map((p) => p.text).join('')).toBe('订单已经发货了')
    expect(d.some((p) => p.op === 'insert')).toBe(true)
  })
})

describe('AlignmentPanel', () => {
  it('显示摘要与参数差异；逐字 diff 默认折叠，点击展开', async () => {
    render(<AlignmentPanel align={align} />)
    expect(screen.getByText('右侧多调用 knowledge_search ×1')).toBeInTheDocument()
    expect(screen.getByTestId('args-diff')).toHaveTextContent('order_id')
    expect(screen.queryByTestId('text-diff')).toBeNull()
    await userEvent.click(screen.getByTestId('toggle-text-diff'))
    const diff = screen.getByTestId('text-diff')
    expect(diff.querySelectorAll('[data-diff="insert"]').length).toBeGreaterThan(0)
  })
})

describe('CompareRowView', () => {
  const state = buildSessionState(toolTurnEvents('trn_L'))
  const base = {
    ordinal: 1,
    leftSessionId: 'ses_l',
    rightSessionId: 'ses_r',
    alignOn: false,
    leftHighlight: undefined,
    rightHighlight: undefined,
    trace: null,
    onOpenTrace: () => {},
  }
  const wrap = (ui: React.ReactNode) => (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )

  it('重放中右侧还没跑到的轮次显示“排队中”，结束后显示“未重放”', () => {
    const row = { pair: 0, leftTurnId: 'trn_L', rightTurnId: null }
    const { rerender } = render(
      wrap(<CompareRowView {...base} row={row} mode="replay" left={state.turns[0]} right={undefined} replayActive />),
    )
    expect(screen.getByTestId('cell-queued')).toHaveTextContent('排队中')
    expect(within(screen.getByTestId('cell-left')).getByTestId('turn')).toBeInTheDocument()
    rerender(wrap(<CompareRowView {...base} row={row} mode="replay" left={state.turns[0]} right={undefined} replayActive={false} />))
    expect(screen.getByTestId('cell-missing')).toHaveTextContent('未重放')
  })

  it('两侧都结束时显示指标对照与差值', () => {
    const row = { pair: 0, leftTurnId: 'trn_L', rightTurnId: 'trn_L' }
    render(wrap(<CompareRowView {...base} row={row} mode="live" left={state.turns[0]} right={state.turns[0]} replayActive={false} />))
    const cmp = screen.getByTestId('metrics-compare')
    expect(cmp).toHaveAttribute('data-settled')
    expect(cmp.querySelector('[data-metric="llm_calls"]')).toHaveTextContent('×2')
    expect(cmp.querySelector('[data-metric="latency"]')).toHaveAttribute('data-trend', 'same')
  })
})
