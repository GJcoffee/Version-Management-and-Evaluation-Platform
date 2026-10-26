import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Alignment, CompareGroup } from '@/api/compareTypes'
import { compareKeys } from '@/api/queries/compare'
import { buildSessionState, initialSessionState, type UseSessionStreamResult } from '@/components/session'
import { toolTurnEvents } from '@/components/session/testEvents'
import { mockApi } from '@/test/mockApi'
import { CompareView } from './CompareView'

// 两侧的 SSE 订阅换成可控的桩：只关心页面对 status / reopen 的使用
const streams: Record<string, UseSessionStreamResult> = {}
vi.mock('@/components/session/useSessionStream', () => ({
  useSessionStream: (id: string | undefined) => streams[id ?? ''],
}))

function fakeStream(over: Partial<UseSessionStreamResult> = {}): UseSessionStreamResult {
  return {
    state: initialSessionState,
    turns: [],
    status: 'idle',
    running: false,
    runningTurn: null,
    loading: false,
    reopen: vi.fn(),
    close: vi.fn(),
    ...over,
  }
}

const side = (sid: string, seq: number) => ({ session_id: sid, version_id: `ver_${seq}`, version_seq: seq, agent_name: 'bot' })

const group = (over: Partial<CompareGroup> = {}): CompareGroup => ({
  id: 'cmp_1',
  kind: 'session',
  mode: 'live',
  left: side('ses_l', 1),
  right: side('ses_r', 2),
  title: '旧标题',
  notes: null,
  created_at: '2026-10-26T00:00:00Z',
  replay_mode: null,
  replay_status: null,
  replay_turns: null,
  replay_error: null,
  ...over,
})

const pairTurn = (pair: number, l: string | null, r: string | null) => ({
  pair,
  left_turn_id: l,
  right_turn_id: r,
  steps: [],
  tool_args_diff: [],
  metrics: { left: null, right: null },
  final_text: { left: null, right: null },
})

function renderView(g: CompareGroup, alignment: Alignment = { turns: [] }, extra: Record<string, unknown> = {}) {
  const api = mockApi({
    'GET /api/v1/compare/cmp_1/alignment': alignment,
    'GET /api/v1/compare/cmp_1': g,
    ...extra,
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = (gr: CompareGroup) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <CompareView group={gr} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const view = render(ui(g))
  return { api, qc, rerender: (gr: CompareGroup) => view.rerender(ui(gr)) }
}

beforeEach(() => {
  streams.ses_l = fakeStream()
  streams.ses_r = fakeStream()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('CompareView：重放状态来自服务端', () => {
  it('进行中显示“重放中”和进度；失败显示错误；完成显示“已完成”', async () => {
    const turns = buildSessionState(toolTurnEvents('trn_R')).turns
    streams.ses_r = fakeStream({ turns, status: 'open' })
    const running = group({ mode: 'replay', replay_mode: 'free', replay_status: 'running', replay_turns: [0, 1] })
    const { rerender } = renderView(running, { turns: [pairTurn(0, 'trn_L', 'trn_R'), pairTurn(1, 'trn_L2', null)] })
    const bar = await screen.findByTestId('replay-progress')
    expect(bar).toHaveAttribute('data-status', 'running')
    expect(bar).toHaveTextContent('自由模式')
    await waitFor(() => expect(bar).toHaveTextContent('已完成 1 / 2 轮')) // 对齐结果返回后
    expect(bar).toHaveTextContent('重放中')
    expect(await screen.findByTestId('cell-queued')).toBeInTheDocument()

    rerender({ ...running, replay_status: 'failed', replay_error: '第 2 轮未能执行: 读历史失败' })
    await waitFor(() => expect(screen.getByTestId('replay-failed')).toHaveTextContent('读历史失败'))
    expect(screen.queryByTestId('cell-queued')).toBeNull() // 不再排队
    expect(screen.getByTestId('cell-missing')).toHaveTextContent('未重放')

    rerender({ ...running, replay_status: 'completed' })
    await waitFor(() => expect(screen.getByTestId('replay-progress')).toHaveTextContent('已完成'))
    expect(screen.getByTestId('replay-progress')).toHaveAttribute('data-status', 'completed')
  })

  it('不再每 1.5s 重连；仅在“重放进行中但连接已 idle”持续 5s 后兜底重连一次', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    const reopen = vi.fn()
    streams.ses_r = fakeStream({ status: 'idle', reopen })
    renderView(group({ mode: 'replay', replay_mode: 'anchored', replay_status: 'running', replay_turns: [0] }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500)
    })
    expect(reopen).not.toHaveBeenCalled() // 旧实现在 1.5s / 3s / 4.5s 各重连一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(reopen).toHaveBeenCalledTimes(1)
  })

  it('重放已结束（或不是重放）时不会重连', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    const reopen = vi.fn()
    streams.ses_r = fakeStream({ status: 'idle', reopen })
    renderView(group({ mode: 'replay', replay_status: 'completed', replay_turns: [0] }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000)
    })
    expect(reopen).not.toHaveBeenCalled()
  })

  it('连接因 idle 关闭时刷新一次对比组，让完成状态立刻出现', async () => {
    const spy = vi.spyOn(QueryClient.prototype, 'invalidateQueries')
    renderView(group({ mode: 'replay', replay_status: 'running', replay_turns: [0] }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: compareKeys.detail('cmp_1') }))
    spy.mockRestore()
  })
})

describe('CompareView：标题与备注编辑', () => {
  it('行内编辑标题 -> PATCH /compare/{id}', async () => {
    const { api } = renderView(group(), { turns: [] }, {
      'PATCH /api/v1/compare/cmp_1': (c: { body: unknown }) => ({ ...group(), ...(c.body as object) }),
    })
    expect(await screen.findByTestId('compare-title')).toHaveTextContent('旧标题')
    await userEvent.click(screen.getByRole('button', { name: '编辑标题' }))
    const input = screen.getByTestId('compare-title-input')
    await userEvent.clear(input)
    await userEvent.type(input, '新标题{Enter}')
    await waitFor(() => expect(api.find('PATCH', /\/compare\/cmp_1$/)).toHaveLength(1))
    expect(api.find('PATCH', /cmp_1$/)[0].body).toEqual({ title: '新标题' })
    await waitFor(() => expect(screen.queryByTestId('compare-title-input')).toBeNull())
  })

  it('编辑备注 -> PATCH；失败时保持编辑状态', async () => {
    let fail = true
    const { api } = renderView(group(), { turns: [] }, {
      'PATCH /api/v1/compare/cmp_1': (c: { body: unknown }) =>
        fail ? { __status: 500, body: { error: { code: 'INTERNAL', message: 'boom' } } } : { ...group(), ...(c.body as object) },
    })
    await userEvent.click(await screen.findByRole('button', { name: '添加备注' }))
    await userEvent.type(screen.getByTestId('compare-notes-input'), '想验证新提示词')
    await userEvent.click(screen.getByRole('button', { name: '保存备注' }))
    await waitFor(() => expect(api.find('PATCH', /cmp_1$/)).toHaveLength(1))
    expect(screen.getByTestId('compare-notes-input')).toBeInTheDocument() // 失败：仍在编辑
    fail = false
    await userEvent.click(screen.getByRole('button', { name: '保存备注' }))
    await waitFor(() => expect(api.find('PATCH', /cmp_1$/)).toHaveLength(2))
    expect(api.find('PATCH', /cmp_1$/)[1].body).toEqual({ notes: '想验证新提示词' })
    await waitFor(() => expect(screen.queryByTestId('compare-notes-input')).toBeNull())
  })

  it('已有备注时展示备注文字', async () => {
    renderView(group({ notes: '第一行\n第二行' }))
    expect(await screen.findByTestId('compare-notes-text')).toHaveTextContent('第一行')
  })
})

describe('CompareView：单侧发送', () => {
  const type = async (text: string) => {
    await userEvent.type(screen.getByRole('textbox'), `${text}{Enter}`)
  }

  it('默认两侧发送，请求带 side=both', async () => {
    const { api } = renderView(group(), { turns: [] }, {
      'POST /api/v1/compare/cmp_1/turns': { pair_index: 0, left_turn_id: 'trn_a', right_turn_id: 'trn_b', side: 'both' },
    })
    await screen.findByTestId('send-side')
    expect(screen.getByTestId('send-side-both')).toHaveAttribute('aria-checked', 'true')
    await type('你好')
    await waitFor(() => expect(api.find('POST', /compare\/cmp_1\/turns$/)).toHaveLength(1))
    expect(api.find('POST', /turns$/)[0].body).toMatchObject({ input: '你好', side: 'both' })
    expect(streams.ses_l.reopen).toHaveBeenCalled()
    expect(streams.ses_r.reopen).toHaveBeenCalled()
  })

  it('选“仅左侧”：只向左侧发送、只重连左侧，并立刻出现“单侧消息”行', async () => {
    const { api } = renderView(group(), { turns: [pairTurn(0, 'trn_l0', 'trn_r0')] }, {
      'POST /api/v1/compare/cmp_1/turns': { pair_index: null, left_turn_id: 'trn_fork', right_turn_id: null, side: 'left' },
    })
    await screen.findAllByTestId('compare-row')
    await userEvent.click(screen.getByTestId('send-side-left'))
    expect(screen.getByTestId('send-side-left')).toHaveAttribute('aria-checked', 'true')
    await type('只问左边')
    await waitFor(() => expect(api.find('POST', /turns$/)).toHaveLength(1))
    expect(api.find('POST', /turns$/)[0].body).toMatchObject({ input: '只问左边', side: 'left' })
    expect(streams.ses_l.reopen).toHaveBeenCalled()
    expect(streams.ses_r.reopen).not.toHaveBeenCalled()
    const rows = await screen.findAllByTestId('compare-row')
    expect(rows).toHaveLength(2)
    expect(rows[1]).toHaveAttribute('data-fork', 'left')
    expect(screen.getByTestId('fork-label')).toHaveTextContent('单侧消息 · 仅左侧')
    expect(rows[0]).not.toHaveAttribute('data-fork')
  })

  it('服务端对齐结果里的 forks 排在对应成对行之后，成对行的行号不受影响', async () => {
    renderView(group(), {
      turns: [pairTurn(0, 'a0', 'b0'), pairTurn(1, 'a1', 'b1')],
      forks: [{ side: 'right', turn_id: 'b_fork', idx: 1, after_pair: 0, input: 'x', final_text: null, status: 'ok', metrics: null }],
    })
    const rows = await screen.findAllByTestId('compare-row')
    expect(rows.map((r) => r.getAttribute('data-fork') ?? 'pair')).toEqual(['pair', 'right', 'pair'])
    expect(rows[2]).toHaveTextContent('第 2 轮')
  })
})

describe('CompareView：运行中的增量对齐', () => {
  it('一侧还在运行时，对齐视图按已收到的事件实时对齐；两侧结束后以服务端结果为准', async () => {
    const full = buildSessionState(toolTurnEvents('trn_L')).turns
    const partial = buildSessionState(toolTurnEvents('trn_R').slice(0, 3)).turns // 右侧只收到第一次 llm.response
    streams.ses_l = fakeStream({ turns: full, status: 'idle' })
    streams.ses_r = fakeStream({ turns: partial, status: 'open', running: true })
    const g = group({ mode: 'history' })
    // 服务端的对齐结果（此刻是旧的）：步骤一致
    const server = {
      turns: [
        {
          ...pairTurn(0, 'trn_L', 'trn_R'),
          steps: [
            { op: 'equal' as const, left: 's1', right: 's1', sig: 'llm' },
            { op: 'equal' as const, left: 's4', right: 's4', sig: 'final' },
          ],
        },
      ],
    }
    const { rerender } = renderView(g, server)
    await userEvent.click(await screen.findByTestId('align-toggle'))
    const panel = await screen.findByTestId('alignment-panel')
    expect(panel).toHaveTextContent('左侧多调用 query_order ×1') // 来自浏览器里的增量对齐

    // 右侧也结束：沿用服务端结果（步骤一致）
    streams.ses_r = fakeStream({ turns: buildSessionState(toolTurnEvents('trn_R')).turns, status: 'idle' })
    rerender(g)
    await waitFor(() => expect(screen.getByTestId('alignment-panel')).toHaveTextContent('步骤一致'))
  })
})
