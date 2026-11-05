import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Session } from '@/api/types'
import { buildSessionState, initialSessionState, type UseSessionStreamResult } from '@/components/session'
import { mk, recordedTurnEvents, toolTurnEvents } from '@/components/session/testEvents'
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

function renderPage(over: Partial<Session> = {}, routes: Record<string, unknown> = {}) {
  const api = mockApi({
    'GET /api/v1/sessions/ses_1': { session: { ...session, ...over }, turns: [], events: null }, ...routes,
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/sessions/ses_1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/sessions/:id" element={<SessionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return api
}

beforeEach(() => {
  localStorage.clear()
  stream.turns = state.turns
})
afterEach(() => vi.unstubAllGlobals())

describe('SessionPage 响应式布局', () => {
  it('打开会话时每轮默认折叠，只显示标题、摘要和指标', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    await screen.findByTestId('session-page')
    const turns = screen.getAllByTestId('turn')
    expect(turns).toHaveLength(2)
    for (const turn of turns) {
      expect(turn).toHaveAttribute('data-collapsed')
      expect(within(turn).getByTestId('turn-header-metrics')).toBeInTheDocument()
      expect(within(turn).queryByTestId('user-message')).toBeNull()
    }
    await userEvent.click(within(turns[0]).getByRole('button', { name: /第 1 轮/ }))
    expect(within(turns[0]).getByTestId('user-message')).toBeInTheDocument()
    expect(turns[1]).toHaveAttribute('data-collapsed')
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 2 / 2 轮')
  })

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
    await userEvent.click(screen.getByTestId('turn-header'))
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

  it('录制会话每轮、会话总计和 Trace 采用同一原始执行耗时', async () => {
    stubViewport(false)
    stream.turns = buildSessionState([
      ...recordedTurnEvents('a', 1),
      ...recordedTurnEvents('b', 20, 600_000),
    ]).turns
    renderPage()
    expect(await screen.findByTestId('session-latency')).toHaveTextContent(`总用时 ${formatDuration(142_470)}`)
    for (const bar of screen.getAllByTestId('turn-header-metrics')) {
      expect(bar.querySelector('[data-metric="latency"]')).toHaveTextContent(formatDuration(71_235))
    }
    expect(screen.getByTestId('trace-duration')).toHaveTextContent(formatDuration(71_235))
    expect(screen.getByTestId('trace-duration')).not.toHaveTextContent(formatDuration(71_760))
  })
})

describe('SessionPage 远端指标（HiAgent）', () => {
  const remoteEvents = (extra: ReturnType<typeof mk>[]) => [
    mk('turn.start', { input: { role: 'user', content: '查订单' }, history_len: 0 }, { seq: 1, at: 0 }),
    mk('turn.end', { status: 'completed', output: null, cost: 0, latency_ms: null, ttft_ms: null,
      usage: { trace_status: 'pending', prompt_tokens: 9, completion_tokens: 3, tool_calls: 1 } }, { seq: 2, at: 300 }),
    ...extra,
  ]

  it('Trace 补齐中：会话汇总显示同步中，不显示本地用时与成本', async () => {
    stubViewport(false)
    stream.turns = buildSessionState(remoteEvents([])).turns
    renderPage()
    expect(await screen.findByTestId('session-latency')).toHaveTextContent('HiAgent 用时 同步中')
    expect(screen.getByTestId('session-calls')).toHaveTextContent('LLM 同步中')
    expect(screen.getByTestId('session-metrics')).not.toHaveTextContent('¥')
  })

  it('补齐后显示远端用时与调用次数', async () => {
    stubViewport(false)
    stream.turns = buildSessionState(remoteEvents([
      mk('metrics', { scope: 'turn', source: 'hiagent_trace', upstream_run_ms: 20_000, llm_calls: 2, trace_status: 'complete' },
        { seq: 3, at: 400 }),
    ])).turns
    renderPage()
    expect(await screen.findByTestId('session-latency')).toHaveTextContent('HiAgent 用时 20.000s')
    expect(screen.getByTestId('session-calls')).toHaveTextContent('LLM ×2')
  })

  it('未取得：显示未取得，悬停给出原因', async () => {
    stubViewport(false)
    stream.turns = buildSessionState(remoteEvents([
      mk('metrics', { scope: 'turn', trace_status: 'unavailable', trace_note: 'Trace 查询 HTTP 401' }, { seq: 3, at: 400 }),
    ])).turns
    renderPage()
    const latency = await screen.findByTestId('session-latency')
    expect(latency).toHaveTextContent('HiAgent 用时 未取得')
    expect(latency.getAttribute('title')).toContain('Trace 查询 HTTP 401')
    expect(screen.getByTestId('session-calls')).toHaveTextContent('1 轮未取得')
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
    expect(turnEl(0)).toHaveAttribute('data-collapsed')
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
    expect(turnEl(0)).toHaveAttribute('data-collapsed')
    await userEvent.click(screen.getByRole('button', { name: '上一轮' }))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
    expect(turnEl(0)).not.toHaveAttribute('data-collapsed')
  })

  it('在对话区点「查看 Trace」仍然把 Trace 切到该轮', async () => {
    stubViewport(false)
    loadTwoTurns()
    renderPage()
    await screen.findByTestId('trace-turn')
    await userEvent.click(within(turnEl(0)).getByTestId('turn-header'))
    await userEvent.click(within(turnEl(0)).getByTestId('select-turn'))
    expect(screen.getByTestId('trace-turn')).toHaveTextContent('第 1 / 2 轮')
  })
})


describe('SessionPage 适配器标签', () => {
  const hiagent = {
    environment: 'production', revision: 3, reason: '切换到新发布的桥接',
    binding: { id: 'hiagent', version: '1.0.0', credential_ref: 'cred_1', config: { published_version: '47', app_id: 'app-1' } },
  }

  it('显示「生产 · 存档 #N」，悬停显示适配器、关键配置和存档说明，不再请求版本绑定', async () => {
    stubViewport(false)
    const { find } = renderPage({ adapter_snapshot: hiagent } as Partial<Session>)
    const badge = await screen.findByTestId('adapter-badge')
    expect(badge).toHaveTextContent('生产 · 存档 #3')
    expect(badge.getAttribute('title')).toContain('HiAgent 1.0.0 · 发布版本 47 · app app-1')
    expect(badge.getAttribute('title')).toContain('说明：切换到新发布的桥接')
    expect(find('GET', /adapter-binding/)).toHaveLength(0)
  })

  it('请求测试却落到生产时标明未生效', async () => {
    stubViewport(false)
    renderPage({
      adapter_snapshot: { ...hiagent, environment: 'production', requested_environment: 'test' },
    } as Partial<Session>)
    const badge = await screen.findByTestId('adapter-badge')
    expect(badge).toHaveTextContent('生产 · 存档 #3（测试未生效）')
    expect(badge.getAttribute('title')).toContain('已使用生产')
  })

  it('测试环境的会话显示「测试 · 存档 #N」', async () => {
    stubViewport(false)
    renderPage({ adapter_snapshot: { ...hiagent, environment: 'test', revision: 2 } } as Partial<Session>)
    expect(await screen.findByTestId('adapter-badge')).toHaveTextContent('测试 · 存档 #2')
  })

  it('未绑定适配器的会话保持原样', async () => {
    stubViewport(false)
    renderPage()
    await screen.findByTestId('agent-link')
    expect(screen.queryByTestId('adapter-badge')).toBeNull()
  })
})
