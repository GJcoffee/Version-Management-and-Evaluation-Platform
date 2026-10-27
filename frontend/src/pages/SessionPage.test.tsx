import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Session } from '@/api/types'
import { buildSessionState, initialSessionState, type UseSessionStreamResult } from '@/components/session'
import { mk, toolTurnEvents } from '@/components/session/testEvents'
import { formatDuration } from '@/lib/format'
import { mockApi } from '@/test/mockApi'
import SessionPage from './SessionPage'

// SSE 订阅换成固定的对话：默认一轮，需要多轮的用例通过 stream.turns 替换
const state = buildSessionState(toolTurnEvents())
const stream = vi.hoisted(() => ({ turns: [] as unknown[] }))
vi.mock('@/components/session/useSessionStream', () => ({
  useSessionStream: (): UseSessionStreamResult => ({
    state: initialSessionState,
    turns: stream.turns as UseSessionStreamResult['turns'],
    status: 'idle',
    running: false,
    runningTurn: null,
    loading: false,
    reopen: vi.fn(),
    close: vi.fn(),
  }),
}))

const session: Session = {
  id: 'ses_1',
  agent_id: 'agt_1',
  agent_name: 'bot',
  version_id: 'ver_1',
  version_seq: 1,
  mode: 'chat',
  title: '测试会话',
  source_session_id: null,
  replay_mode: null,
  status: 'idle',
  last_seq: 9,
  stats: {},
  variables: {},
  created_at: '2026-10-26T00:00:00Z',
  updated_at: '2026-10-26T00:00:00Z',
} as Session

/** 模拟 matchMedia：narrow 为 true 时窄屏查询命中 */
function stubViewport(narrow: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((q: string) => ({
      matches: narrow && q.includes('max-width: 800px'),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}

function renderPage() {
  mockApi({ 'GET /api/v1/sessions/ses_1': { session, turns: [], events: null } })
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/sessions/ses_1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/sessions/:id" element={<SessionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  stream.turns = state.turns
})
afterEach(() => vi.unstubAllGlobals())

describe('SessionPage 响应式布局', () => {
  it('宽屏：Trace 作为右侧栏默认展开，可切换加宽', async () => {
    stubViewport(false)
    renderPage()
    const panel = await screen.findByTestId('trace-panel')
    expect(panel).toHaveAttribute('data-layout', 'side')
    expect(screen.getByRole('button', { name: '加宽' })).toBeInTheDocument()
  })

  it('窄屏（≤800px）：Trace 默认收起，点击按钮以底部抽屉打开，且不写入宽屏偏好', async () => {
    stubViewport(true)
    renderPage()
    await screen.findByTestId('session-page')
    expect(screen.queryByTestId('trace-panel')).toBeNull()
    await userEvent.click(screen.getByTestId('toggle-trace'))
    const panel = await screen.findByTestId('trace-panel')
    expect(panel).toHaveAttribute('data-layout', 'drawer')
    expect(panel.className).toContain('absolute')
    expect(screen.queryByRole('button', { name: '加宽' })).toBeNull()
    expect(localStorage.getItem('evalp.session.trace-open')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '收起 Trace' }))
    expect(screen.queryByTestId('trace-panel')).toBeNull()
  })

  it('窄屏：点击轮次下方的「查看 Trace」会打开抽屉；点击消息本身不会', async () => {
    stubViewport(true)
    renderPage()
    await screen.findByTestId('session-page')
    await userEvent.click(screen.getByTestId('user-message'))
    expect(screen.queryByTestId('trace-panel')).toBeNull()
    await userEvent.click(screen.getByTestId('select-turn'))
    expect(await screen.findByTestId('trace-panel')).toHaveAttribute('data-layout', 'drawer')
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 1 轮')
  })

  it('窗口从宽屏变窄时，侧栏切换为抽屉形态', async () => {
    let handler: (() => void) | undefined
    let narrow = false
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        get matches() {
          return narrow
        },
        addEventListener: (_: string, fn: () => void) => (handler = fn),
        removeEventListener: vi.fn(),
      })),
    )
    renderPage()
    expect(await screen.findByTestId('trace-panel')).toHaveAttribute('data-layout', 'side')
    narrow = true
    act(() => handler?.())
    expect(screen.queryByTestId('trace-panel')).toBeNull()
  })
})

/** 两轮对话：第 2 轮晚 10 秒开始，输入为「第二个问题」，各轮用时 575ms */
function loadTwoTurns() {
  const later = toolTurnEvents('trn_2', 20).map((e) =>
    e.type === 'turn.start'
      ? mk('turn.start', { input: { role: 'user', content: '第二个问题' }, history_len: 1 }, { seq: e.seq, turn: 'trn_2', at: 10_000 })
      : { ...e, ts: new Date(Date.parse(e.ts) + 10_000).toISOString() },
  )
  stream.turns = buildSessionState([...toolTurnEvents('trn_1', 1), ...later]).turns
}

describe('SessionPage 总用时', () => {
  it('在会话统计里显示各轮用时之和，悬停显示首轮开始到末轮结束的跨度', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    const latency = await screen.findByTestId('session-latency')
    expect(latency).toHaveTextContent(`总用时 ${formatDuration(575 * 2)}`)
    expect(latency).toHaveAttribute('title', expect.stringContaining(`首轮开始到末轮结束共 ${formatDuration(10_575)}`))
  })
})

describe('SessionPage Trace 轮次切换', () => {
  const turnEl = (i: number) => screen.getAllByTestId('turn')[i]

  it('上一轮 / 下一轮：Trace 与对话区同步，已折叠的轮次先展开并高亮选中', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    expect(await screen.findByTestId('trace-turn')).toHaveTextContent('第 2 / 2 轮')
    expect(screen.getByRole('button', { name: '下一轮' })).toBeDisabled()
    await userEvent.click(within(turnEl(0)).getByTestId('turn-header')) // 折叠第 1 轮
    expect(turnEl(0)).toHaveAttribute('data-collapsed')

    await userEvent.click(screen.getByRole('button', { name: '上一轮' }))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
    expect(turnEl(0)).not.toHaveAttribute('data-collapsed')
    expect(turnEl(0)).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: '上一轮' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: '下一轮' }))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 2 / 2 轮')
    expect(turnEl(1)).toHaveAttribute('aria-current', 'true')
  })

  it('下拉可跳到任意一轮', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    const select = await screen.findByRole('combobox', { name: '跳到指定轮次' })
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['第 1 轮 · 查订单 A001', '第 2 轮 · 第二个问题'])
    await userEvent.click(within(turnEl(0)).getByTestId('turn-header'))
    await userEvent.selectOptions(select, 'trn_1')
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
    expect(turnEl(0)).not.toHaveAttribute('data-collapsed')
    expect(turnEl(0)).toHaveAttribute('aria-current', 'true')
  })

  it('窄屏抽屉里同样可以切换', async () => {
    stubViewport(true)
    loadTwoTurns()
    renderPage()
    await screen.findByTestId('session-page')
    await userEvent.click(screen.getByTestId('toggle-trace'))
    expect(await screen.findByTestId('trace-panel')).toHaveAttribute('data-layout', 'drawer')
    await userEvent.click(within(turnEl(0)).getByTestId('turn-header'))
    await userEvent.click(screen.getByRole('button', { name: '上一轮' }))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
    expect(turnEl(0)).not.toHaveAttribute('data-collapsed')
  })

  it('在对话区点「查看 Trace」仍然把 Trace 切到该轮', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    await screen.findByTestId('trace-turn')
    await userEvent.click(within(turnEl(0)).getByTestId('select-turn'))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
  })
})
