import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Session } from '@/api/types'
import { buildSessionState, initialSessionState, type UseSessionStreamResult } from '@/components/session'
import { toolTurnEvents } from '@/components/session/testEvents'
import { mockApi } from '@/test/mockApi'
import SessionPage from './SessionPage'

// SSE 订阅换成固定的一轮对话
const state = buildSessionState(toolTurnEvents())
vi.mock('@/components/session/useSessionStream', () => ({
  useSessionStream: (): UseSessionStreamResult => ({
    state: initialSessionState,
    turns: state.turns,
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

beforeEach(() => localStorage.clear())
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
